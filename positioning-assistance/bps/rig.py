"""Adds emulated neighbouring cells to the IQ stream of a real transmitter, for testing UE positioning on one eNB. [design]

The rt-mbms demo has one eNB. Positioning needs several cells, each arriving at its own time. This mixer sits between the transmitter and the modem on the ZMQ
stream: the transmitter is the reference cell, and the other cells are emulated PRS-only subframes added at the cell acquisition subframes, each delayed by its
propagation delay and relative transmit delay (RTD) with sub-sample resolution. The cyclic prefix is a parameter (`normal_cp`): the cell acquisition subframe and the PRS
are built with the real cell's.

Steps: lock onto the real cell acquisition subframes by correlating with the PSS and SSS of the real cell (they are on the air, so this needs no cooperation
from the transmitter), measure the scale of the stream from that match, then at every cell acquisition subframe add each emulated cell's PRS subframe
(TS 36.211 clause 6.10.4, from bps.prs) at its delay and a chosen power below the real cell's.

What this is not: a channel model. Every cell has one path, no fading, no noise of its own, no frequency offset. What is real is that the receiver sees several
cells' PRS overlapping at different delays in one stream, and the cell acquisition subframe, PSS, SSS and the rest of the real cell untouched.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .prs import prs_subframe
from .sync import cas_subframe

FS = 15.36e6                 # sample rate of the transmitter's ZMQ stream
BLOCK = 15360                # samples per message: 1 ms
NOISE_POOL = 1 << 22         # samples of the noise pool (0.27 s of the stream)
CAS_PERIOD = 614400          # 40 ms: cell acquisition subframes of an MBMS-dedicated cell, 25 PRB (frames with n_f mod 4 = 0)
_PAD = 16384                 # length used for a fractionally delayed subframe


def upsample2(w: np.ndarray) -> np.ndarray:
    """A band-limited waveform from 7.68 to 15.36 Msps by zero padding its spectrum."""
    n = len(w)
    spec = np.fft.fft(w)
    out = np.zeros(2 * n, dtype=complex)
    out[: n // 2] = spec[: n // 2]
    out[-(n // 2):] = spec[n // 2:]
    return np.fft.ifft(out) * 2.0


def frac_delay(x: np.ndarray, delay: float, n: int = _PAD) -> np.ndarray:
    """`x` zero-padded to n samples and delayed by `delay` samples (may be fractional, 0 <= delay < 1 is intended) in the frequency domain."""
    pad = np.zeros(n, dtype=complex)
    pad[: len(x)] = x
    f = np.fft.fftfreq(n)
    return np.fft.ifft(np.fft.fft(pad) * np.exp(-2j * np.pi * f * delay))


def correlation_search(rx: np.ndarray, ref: np.ndarray) -> tuple[float, float, float, np.ndarray]:
    """Position, in samples with sub-sample resolution, of the strongest match of `ref` in `rx`; the correlation magnitude there; that magnitude over the median
    magnitude away from the peak; and the magnitudes on the integer lags. The coarse peak is found on the integer lags by an FFT correlation; the position between
    samples by evaluating the correlation at fractional lags (within 0.6 sample) over the bins where the reference has energy, then a parabola."""
    m = 1 << int(math.ceil(math.log2(len(rx) + len(ref))))
    X = np.fft.fft(rx, m)
    S = np.fft.fft(ref, m)
    C = X * np.conj(S)
    c = np.abs(np.fft.ifft(C))[: len(rx) - len(ref) + 1]     # numpy's ifft already divides by m, as the fractional evaluation below does
    coarse = int(np.argmax(c))
    away = np.ones(len(c), dtype=bool)
    away[max(0, coarse - 200): coarse + 200] = False
    median = float(np.median(c[away])) if away.any() else 0.0
    f = np.fft.fftfreq(m)
    bins = np.nonzero(np.abs(S) > 1e-3 * np.abs(S).max())[0]
    taus = coarse + 0.1 * (np.arange(13) - 6)
    vals = np.array([abs(np.sum(C[bins] * np.exp(2j * np.pi * f[bins] * t))) / m for t in taus])
    bi = int(np.argmax(vals))
    frac = 0.0
    if 0 < bi < len(vals) - 1:
        a, b, cc = vals[bi - 1], vals[bi], vals[bi + 1]
        den = a - 2 * b + cc
        if den != 0:
            frac = 0.5 * (a - cc) / den
    return float(taus[bi] + 0.1 * frac), float(vals[bi]), (float(vals[bi]) / median if median > 0 else 0.0), c


def correlation_lag(rx: np.ndarray, ref: np.ndarray) -> tuple[float, float]:
    """As correlation_search, without the extras: (lag, magnitude)."""
    lag, val, _, _ = correlation_search(rx, ref)
    return lag, val


@dataclass
class EmulatedCell:
    cell_id: int
    prs_id: Optional[int] = None     # the cell identity when absent
    normal_cp: Optional[bool] = None  # this cell's cyclic prefix; the real cell's when absent
    power_db: Optional[float] = None  # this cell's power against the real cell's PSS and SSS elements; the mixer's `power_db` when absent
    cfo_hz: float = 0.0               # this cell's carrier frequency offset against the real cell's, as a receiver locked to the real cell sees it (separate oscillators)


class Mixer:
    """Block in, block out. `delay_s(cell_id, t_gps_ns)` is the arrival delay of an emulated cell's subframe grid against the real cell's (propagation plus the
    RTD folded to the closest subframe); `now_gps_ns()` the network time. Call process() with each message of the transmitter's stream."""

    def __init__(self, cells: list[EmulatedCell], delay_s: Callable[[int, int], float], now_gps_ns: Callable[[], int], real_cell_id: int = 1,
                 n_rb: int = 25, power_db: float = -6.0, lock_blocks: int = 2 * CAS_PERIOD // BLOCK + 2, real_cell_has_prs: bool = True,
                 min_lock_ratio: float = 8.0, normal_cp: bool = False, snr_db: Optional[float] = None, seed: int = 0):
        if n_rb != 25:
            raise ValueError("the mixer is written for the 25 PRB demo carrier (cell acquisition every 40 ms, 15.36 Msps stream)")
        self.cells = cells
        self.delay_s = delay_s
        self.now = now_gps_ns
        self.real_cell_id = real_cell_id
        self.power_db = power_db
        self._lock_len = lock_blocks * BLOCK
        self._hist: list[np.ndarray] = []
        self._hist_n = 0
        self._n0 = 0                         # absolute index of the first sample of the next block
        self.p0: Optional[float] = None      # absolute position of a real cell acquisition subframe, once locked
        self.scale: Optional[float] = None
        self._next_occasion = 0
        self._pending: list[tuple[int, np.ndarray]] = []   # (absolute start, samples)
        self.min_lock_ratio = min_lock_ratio
        self.normal_cp = normal_cp   # the real cell's cyclic prefix: the cell acquisition subframe and the PRS are built with it (TS 36.211 clause 6.1: the same as subframe #0)
        self._sync_ref = upsample2(cas_subframe(real_cell_id, n_rb, normal_cp=normal_cp))
        # What the real cell sends in its cell acquisition subframe that is known here: the PSS and SSS and, if it sends them, its own PRS. Correlating with
        # the PSS and SSS alone is biased by a few nanoseconds by the PRS elements next to them, so the PRS goes into the reference when it is on the air.
        self._lock_ref = self._sync_ref + (upsample2(prs_subframe(real_cell_id, 0, n_rb, n_f=0, normal_cp=normal_cp)) if real_cell_has_prs else 0.0)
        self._waves = {c.cell_id: upsample2(prs_subframe(c.cell_id if c.prs_id is None else c.prs_id, 0, n_rb, n_f=0,
                                                    normal_cp=normal_cp if c.normal_cp is None else c.normal_cp)) for c in cells}
        self._noise_pool: Optional[np.ndarray] = None
        self.snr_db = snr_db         # white noise added to the whole stream once locked, for measuring what a receiver does at a given signal to noise ratio
        self._rng = np.random.default_rng(seed)
        self.lock_quality = 0.0       # match of the latest lock attempt over the background
        self.best_lock_ratio = 0.0    # the strongest match of any attempt, including those refused for not repeating
        self.injected = 0

    # -- locking
    def _try_lock(self) -> None:
        x = np.concatenate(self._hist)
        lag, val, ratio, c = correlation_search(x, self._lock_ref)
        self.lock_quality = ratio
        self.best_lock_ratio = max(self.best_lock_ratio, ratio)
        # a real cell acquisition subframe repeats every 40 ms: the same match must be there one period before or after the peak
        coarse = int(round(lag))
        others = [coarse + k * CAS_PERIOD for k in (-1, 1) if 0 <= coarse + k * CAS_PERIOD < len(c)]
        periodic = any(c[max(0, o - 2): o + 3].max() > 0.5 * val for o in others)
        if ratio < self.min_lock_ratio or not periodic:
            half = len(self._hist) // 2
            self._hist = self._hist[half:]
            self._hist_n = sum(len(h) for h in self._hist)
            return
        i0 = int(math.floor(lag))
        seg = x[i0: i0 + len(self._sync_ref)]
        # the scale of the stream: the PSS and SSS are on the air at unit resource element power, so their amplitude in the stream is the scale
        shifted = frac_delay(self._sync_ref, lag - i0, len(self._sync_ref) + 64)[: len(self._sync_ref)]
        self.scale = float(abs(np.vdot(shifted, seg)) / np.vdot(shifted, shifted).real)
        first_n = self._n0 - self._hist_n          # absolute index of the first sample in the history
        self.p0 = first_n + lag
        # the next cell acquisition subframe that has not started yet, with a block of margin for scheduling
        self._next_occasion = max(0, math.ceil((self._n0 + BLOCK - self.p0) / CAS_PERIOD))
        self._hist = []
        self._hist_n = 0

    # -- injections
    def _schedule(self) -> None:
        # schedule the occasions whose start lies within the next two blocks (plus the largest delay)
        horizon = self._n0 + 3 * BLOCK
        while True:
            a = self.p0 + self._next_occasion * CAS_PERIOD
            if a - 15360 > horizon:
                break
            t = self.now()
            for c in self.cells:
                start = a + self.delay_s(c.cell_id, t) * FS
                i0 = int(math.floor(start))
                w = frac_delay(self._waves[c.cell_id] * (self.scale * 10 ** ((self.power_db if c.power_db is None else c.power_db) / 20.0)), start - i0)
                if c.cfo_hz:
                    w = w * np.exp(2j * np.pi * c.cfo_hz * np.arange(len(w)) / FS)   # the phase starts at the cell's subframe, as its oscillator's does
                self._pending.append((i0, w))
                self.injected += 1
            self._next_occasion += 1

    def _noise(self, n: int) -> np.ndarray:
        """`n` samples of white complex noise of variance 2 per sample, read at a random start from a pool drawn once. A block is 1 ms of stream and the rig must process
        it in less than that to keep pace with the transmitter: drawing the noise afresh for every block takes more."""
        if self._noise_pool is None:
            self._noise_pool = self._rng.standard_normal(2 * NOISE_POOL, dtype=np.float32).view(np.complex64)
        start = int(self._rng.integers(0, NOISE_POOL))
        if start + n <= NOISE_POOL:
            return self._noise_pool[start: start + n]
        return np.concatenate((self._noise_pool[start:], self._noise_pool[: start + n - NOISE_POOL]))

    def process(self, block: np.ndarray) -> np.ndarray:
        """Takes one message of the transmitter's stream, returns the same samples with the emulated cells added."""
        block = np.asarray(block, dtype=np.complex64)
        n = len(block)
        if self.p0 is None:
            self._hist.append(block.astype(complex))
            self._hist_n += n
            self._n0 += n
            if self._hist_n >= self._lock_len:
                self._try_lock()
            return block
        self._schedule()
        out = block.astype(complex)
        if self.snr_db is not None:
            # Es/N0 per resource element at the modem, which takes the stream down to 7.68 Msps: a resource element of unit power is `scale` in amplitude and the noise
            # of variance v per sample at 15.36 Msps leaves v / 2 per sample, and per bin, after the filter that halves the rate.
            v = 2.0 * self.scale ** 2 / 10 ** (self.snr_db / 10.0)
            out += np.float32(math.sqrt(v / 2.0)) * self._noise(n)
        keep = []
        for i0, w in self._pending:
            lo, hi = max(i0, self._n0), min(i0 + len(w), self._n0 + n)
            if hi > lo:
                out[lo - self._n0: hi - self._n0] += w[lo - i0: hi - i0]
            if i0 + len(w) > self._n0 + n:
                keep.append((i0, w))
        self._pending = keep
        self._n0 += n
        return out.astype(np.complex64)


__all__ = ["FS", "BLOCK", "CAS_PERIOD", "upsample2", "frac_delay", "correlation_lag", "correlation_search", "EmulatedCell", "Mixer"]
