"""Primary and secondary synchronization signals of a cell acquisition subframe, as the rig needs them. [spec]

The rig (bps.rig) finds the real cell's cell acquisition subframes in the transmitter's IQ stream by correlating with that cell's PSS and SSS, which are on the air, so
it needs no cooperation from the transmitter. The sequences and their mapping follow TS 36.211 V19.3.0 clauses 6.11.1 and 6.11.2, with the pattern of an MBMS-dedicated
cell (slot 0 of the frames with n_f mod 4 = 0). Nothing here measures a time of arrival: positioning uses PRS only.
"""
from __future__ import annotations

import numpy as np

from .prs import _FFT_FOR_RB, ofdm_subframe

# Table 6.11.1.1-1: root index of the Zadoff-Chu sequence for N_ID^(2)  [spec]
_PSS_ROOT = {0: 25, 1: 29, 2: 34}
SYNC_LEN = 62


def split_cell_id(cell_id: int) -> tuple[int, int]:
    """Clause 6.11: N_ID^cell = 3 N_ID^(1) + N_ID^(2), N_ID^(1) in 0..167 and N_ID^(2) in 0..2. Returns (N_ID^(1), N_ID^(2))."""
    if not 0 <= cell_id <= 503:
        raise ValueError("a physical-layer cell identity is 0..503")
    return cell_id // 3, cell_id % 3


def pss_sequence(n_id_2: int) -> np.ndarray:
    """Clause 6.11.1.1: d(n) = exp(-j pi u n (n + 1) / 63) for n = 0..30 and exp(-j pi u (n + 1) (n + 2) / 63) for n = 31..61."""
    if n_id_2 not in _PSS_ROOT:
        raise ValueError("N_ID^(2) is 0, 1 or 2")
    u = _PSS_ROOT[n_id_2]
    n = np.arange(SYNC_LEN)
    phase = np.where(n <= 30, u * n * (n + 1), u * (n + 1) * (n + 2)) / 63.0
    return np.exp(-1j * np.pi * phase)


def _m_sequence(taps: tuple[int, ...]) -> np.ndarray:
    """x(i + 5) = (sum of x(i + t) for t in taps) mod 2, x(0..3) = 0, x(4) = 1, for i = 0..25; returns 1 - 2 x(i), i = 0..30."""
    x = [0, 0, 0, 0, 1] + [0] * 26
    for i in range(26):
        x[i + 5] = sum(x[i + t] for t in taps) % 2
    return 1.0 - 2.0 * np.array(x, dtype=float)


_S_TILDE = _m_sequence((2, 0))
_C_TILDE = _m_sequence((3, 0))
_Z_TILDE = _m_sequence((4, 2, 1, 0))


def sss_indices(n_id_1: int) -> tuple[int, int]:
    """Clause 6.11.2.1: m' = N_ID^(1) + q (q + 1) / 2 with q = floor((N_ID^(1) + q' (q' + 1) / 2) / 30) and q' = floor(N_ID^(1) / 30);
    m0 = m' mod 31 and m1 = (m0 + floor(m' / 31) + 1) mod 31."""
    if not 0 <= n_id_1 <= 167:
        raise ValueError("N_ID^(1) is 0..167")
    qp = n_id_1 // 30
    q = (n_id_1 + qp * (qp + 1) // 2) // 30
    mp = n_id_1 + q * (q + 1) // 2
    m0 = mp % 31
    return m0, (m0 + mp // 31 + 1) % 31


def sss_sequence(n_id_1: int, n_id_2: int, subframe: int = 0) -> np.ndarray:
    """Clause 6.11.2.1: d(2n) and d(2n + 1) from two length-31 m-sequences, scrambled by sequences that depend on N_ID^(2) and on the m indices;
    the combination differs between subframes 0 to 4 and 5 to 9. An MBMS-dedicated cell sends the SSS in slot 0 of subframe 0."""
    if n_id_2 not in _PSS_ROOT:
        raise ValueError("N_ID^(2) is 0, 1 or 2")
    if not 0 <= subframe <= 9:
        raise ValueError("subframe is 0..9")
    m0, m1 = sss_indices(n_id_1)
    n = np.arange(31)
    s0 = _S_TILDE[(n + m0) % 31]
    s1 = _S_TILDE[(n + m1) % 31]
    c0 = _C_TILDE[(n + n_id_2) % 31]
    c1 = _C_TILDE[(n + n_id_2 + 3) % 31]
    z0 = _Z_TILDE[(n + m0 % 8) % 31]
    z1 = _Z_TILDE[(n + m1 % 8) % 31]
    d = np.zeros(SYNC_LEN)
    if subframe < 5:
        d[0::2], d[1::2] = s0 * c0, s1 * c1 * z0
    else:
        d[0::2], d[1::2] = s1 * c0, s0 * c1 * z1
    return d


def sync_slot_grid(cell_id: int, n_rb_dl: int, normal_cp: bool = True) -> np.ndarray:
    """Slot 0 of a cell acquisition subframe: the PSS in the last OFDM symbol and the SSS in the one before it, on k = n - 31 + N_RB^DL N_sc^RB / 2,
    n = 0..61 (clauses 6.11.1.2 and 6.11.2.2, MBMS-dedicated cell). Nothing else is generated: no PBCH, no cell-specific reference signals."""
    if n_rb_dl not in _FFT_FOR_RB:
        raise ValueError(f"N_RB^DL {n_rb_dl} is not one of {sorted(_FFT_FOR_RB)}")
    n1, n2 = split_cell_id(cell_id)
    n_sym = 7 if normal_cp else 6
    grid = np.zeros((12 * n_rb_dl, n_sym), dtype=complex)
    k = np.arange(SYNC_LEN) - 31 + 6 * n_rb_dl
    grid[k, n_sym - 1] = pss_sequence(n2)
    grid[k, n_sym - 2] = sss_sequence(n1, n2, 0)
    return grid


def cas_subframe(cell_id: int, n_rb_dl: int, normal_cp: bool = True) -> np.ndarray:
    """Waveform of the cell acquisition subframe as far as it is generated here: slot 0 with the PSS and SSS, slot 1 empty."""
    slot0 = sync_slot_grid(cell_id, n_rb_dl, normal_cp)
    return ofdm_subframe([slot0, np.zeros_like(slot0)], n_rb_dl)


__all__ = ["split_cell_id", "pss_sequence", "sss_indices", "sss_sequence", "sync_slot_grid", "cas_subframe"]
