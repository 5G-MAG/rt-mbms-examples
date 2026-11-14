"""Primary and secondary synchronization signals of a cell, and the time difference a UE measures from them. [spec] unless marked [design]

This is the alternative the proposal names where the cell acquisition subframes are not SFN'ed: the time of arrival of each cell is taken from its PSS and SSS
instead of from PRS. The sequences and their mapping follow TS 36.211 V19.3.0 clauses 6.11.1 and 6.11.2, with the pattern of an MBMS-dedicated cell (slot 0 of
the frames with n_f mod 4 = 0). This module is the signal processing a UE would do, run on a simulated channel, feeding the same positioning chain as bps.prs; it is the
reference for the modem's own implementation (rt-mbms-modem src/SyncSignal.h, compared with the golden vectors of tools/gen_sync_golden.py), which measures these signals
on the samples it receives when the UE asks for them (ModemPrs(signal="pss_sss")). The transmitter needs nothing of its own: srsRAN already sends the PSS and SSS.

Standing: this is the proposal's alternative, not the measurement the specification sets requirements for. TS 36.133 V19.6.0 clause 8.1.2.5 writes the RSTD
requirements for PRS and allows the CRS in addition ("it is up to UE implementation whether to use or not the CRS for RSTD measurements"); PSS and SSS are not
named (SPEC-CLAIMS.md C38). The accuracy requirement (clause 9.1.10.1) is stated at a PRS Es/Iot, so it does not transfer to this signal.

Bandwidth: 62 subcarriers at 15 kHz, 0.93 MHz, against 4.5 MHz of a 25 PRB PRS, so the same noise gives a coarser time of arrival.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .prs import _FFT_FOR_RB, correlation_lag, correlation_peak, ofdm_subframe, sample_rate_hz

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


@dataclass(frozen=True)
class SyncCell:
    """A cell as the UE sees it. `delay_s` is the arrival delay of the cell's subframe grid against the UE's own (the reference cell's), `power_db` the
    received power against the reference cell."""
    cell_id: int
    delay_s: float
    power_db: float = 0.0


# [design] rms error of the time difference measured in simulation (25 PRB, one path, four cells overlapping at equal power, random relative delays within
# +-3 us of the lab's, the cancelling estimator, 14 draws of 3 cells each, seed 77): `python3 -m bps.compare sigma`, output in
# results/sync-sigma-2026-11-14.txt. A different channel, number of cells or power spread needs different values.
SYNC_TOA_SIGMA_NS = ((0.0, 52.2), (10.0, 16.8), (20.0, 5.3), (30.0, 1.7))


def toa_sigma_ns(snr_db: float) -> float:
    """Error of one time difference from the table above, interpolated between its points. Above 30 dB the 30 dB value is kept (the real error is lower);
    below 0 dB nothing was measured, so it is refused instead of guessed."""
    if snr_db < SYNC_TOA_SIGMA_NS[0][0]:
        raise ValueError(f"no measured error below {SYNC_TOA_SIGMA_NS[0][0]} dB per resource element; pass the error explicitly")
    xs, ys = zip(*SYNC_TOA_SIGMA_NS)
    return float(np.interp(snr_db, xs, ys))


def _delayed(ref_padded_fft: np.ndarray, freqs: np.ndarray, lag_s: float) -> np.ndarray:
    return np.fft.ifft(ref_padded_fft * np.exp(-2j * np.pi * freqs * lag_s))


def measure_rstd_sync(cells: list[SyncCell], n_rb_dl: int, snr_db: float, rng: np.random.Generator, window_ms: float = 3.0,
                      cancel: bool = True) -> list[float]:
    """[design] Time differences, in seconds, of cells 1..N against cell 0, estimated from the PSS and SSS in simulated received waveforms.

    All cells send their cell acquisition subframe at the same time on the UE's grid, so they overlap in one received window, as non-SFN'ed cells do on air.
    The reference cell's nominal subframe start is 1 ms into the window; a cell's arrival is its `delay_s` after that. `snr_db` is the signal to noise ratio per
    resource element of a cell at `power_db` = 0 (noise variance per subcarrier against the unit power of a PSS or SSS element). One path per cell, white
    Gaussian noise, no frequency offset.

    Each cell is found by correlating the window with that cell's own PSS and SSS. Different cells' sequences are not orthogonal, so with `cancel` False
    each cell's peak is pulled by the others (a bias set by the relative delays that does not average out), and a cell much stronger than another makes the
    correlation lock on the wrong one; cells that share N_ID^(2) also share their PSS. With `cancel` True (the default) the cells are taken strongest first:
    its delay and complex amplitude are estimated, its signal is subtracted, and the next is looked for in what remains; two refinement passes then redo each
    cell with the others removed.
    """
    if n_rb_dl not in _FFT_FOR_RB:
        raise ValueError(f"N_RB^DL {n_rb_dl} is not one of {sorted(_FFT_FOR_RB)}")
    if len(cells) < 2:
        raise ValueError("a time difference needs a reference cell and at least one other")
    fs = sample_rate_hz(n_rb_dl)
    n_win = int(round(window_ms * 1e-3 * fs))
    room_s = (window_ms - 2.0) * 1e-3
    for c in cells:
        if not -1e-3 <= c.delay_s <= room_s:
            raise ValueError(f"delay {c.delay_s * 1e6:.1f} us is outside the window (-1000 .. {room_s * 1e6:.0f} us)")
    freqs = np.fft.fftfreq(n_win, d=1 / fs)
    refs = [cas_subframe(c.cell_id, n_rb_dl) for c in cells]
    ref_fft = []
    for ref in refs:
        padded = np.zeros(n_win, dtype=complex)
        padded[: len(ref)] = ref
        ref_fft.append(np.fft.fft(padded))
    rx = np.zeros(n_win, dtype=complex)
    for c, rf in zip(cells, ref_fft):
        rx += 10 ** (c.power_db / 20) * _delayed(rf, freqs, 1e-3 + c.delay_s)
    sigma = math.sqrt(10 ** (-snr_db / 10) / 2)    # per real component; complex variance 10^(-snr/10) gives that variance per subcarrier after the FFT
    rx = rx + sigma * (rng.standard_normal(n_win) + 1j * rng.standard_normal(n_win))
    if not cancel:
        toa = [correlation_lag(rx, ref) / fs - 1e-3 for ref in refs]
        return [toa[i] - toa[0] for i in range(1, len(cells))]

    lags = [0.0] * len(cells)
    contrib = [np.zeros(n_win, dtype=complex) for _ in cells]
    residual = rx.copy()

    def fit(i: int, target: np.ndarray) -> tuple[float, np.ndarray, float]:
        lag, _ = correlation_peak(target, refs[i])
        shifted = _delayed(ref_fft[i], freqs, lag / fs)
        amp = np.vdot(shifted, target) / np.vdot(shifted, shifted)
        return lag / fs, amp * shifted, float(abs(amp) * np.linalg.norm(shifted))

    remaining = set(range(len(cells)))
    while remaining:                       # strongest first
        best = max(remaining, key=lambda i: fit(i, residual)[2])
        lags[best], contrib[best], _ = fit(best, residual)
        residual = residual - contrib[best]
        remaining.discard(best)
    for _ in range(2):                     # refine each cell with the others removed
        for i in range(len(cells)):
            residual = residual + contrib[i]
            lags[i], contrib[i], _ = fit(i, residual)
            residual = residual - contrib[i]
    toa = [lag - 1e-3 for lag in lags]
    return [toa[i] - toa[0] for i in range(1, len(cells))]


__all__ = ["split_cell_id", "pss_sequence", "sss_indices", "sss_sequence", "sync_slot_grid", "cas_subframe", "SyncCell", "measure_rstd_sync", "toa_sigma_ns", "SYNC_TOA_SIGMA_NS", "SYNC_LEN"]
