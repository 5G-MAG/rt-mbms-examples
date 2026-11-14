"""Broadcaster side of option 1: keeping the relative transmit delay (RTD) of every cell current. [design]

Option 1 (deck slide 13) lets the eNBs be unsynchronised as long as each has a delay against the reference eNB that is constant in
time, and sends that RTD to the UE in the assistance document, "periodically updated". Somebody has to measure it. A monitoring
receiver at a known position timestamps the frame boundary of every cell on one clock; knowing each cell's distance, the transmit
offset of a cell is its arrival time minus d/c. The RTD of a cell is that offset minus the reference cell's, wrapped to
[-0.5, +0.5) ms because TS 36.214 V19.0.0 clause 5.1.12 defines RSTD against the reference cell's subframe "closest in time".

The RTD is fitted as a straight line in time, so the document can carry a value, a drift rate and their uncertainties
(`assistance.Rtd`), which the UE evaluates at the time of its own measurement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import geo
from .assistance import Rtd
from .timebase import C_MPS, SUBFRAME_NS


class InsufficientData(ValueError):
    pass


@dataclass(frozen=True)
class Epoch:
    """One measurement round: when it happened on the monitor clock, and each cell's arrival time of one of its frame boundaries."""

    t_gps_ns: int
    arrivals_ns: dict[int, int]  # cell id -> arrival time of that cell's boundary, same clock as t_gps_ns


def _wrap_subframe_ns(x: int) -> int:
    """Fold into [-0.5, +0.5) ms."""
    x %= SUBFRAME_NS
    return x - SUBFRAME_NS if x >= SUBFRAME_NS // 2 else x


def _fit_line(ts_s: list[float], ys: list[float], floor_sigma: float = 0.0) -> tuple[float, float, float, float, float]:
    """Least squares y = a + b*t. Returns (a, b, residual std, std of a, std of b); `ts_s` is measured from the instant the value
    `a` is wanted at (zero), so `a` is the fitted value there."""
    n = len(ts_s)
    x = list(ts_s)
    mx = sum(x) / n
    my = sum(ys) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx == 0:
        raise InsufficientData("all measurements are at the same time")
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, ys)) / sxx
    a = my - b * mx
    ssr = sum((yi - (a + b * xi)) ** 2 for xi, yi in zip(x, ys))
    sigma = max(math.sqrt(ssr / (n - 2)) if n > 2 else 0.0, floor_sigma)
    var_a = sigma**2 * (1 / n + mx**2 / sxx)
    var_b = sigma**2 / sxx
    return a, b, sigma, math.sqrt(var_a), math.sqrt(var_b)


class RtdMonitor:
    """Accumulates epochs from a monitoring receiver and estimates each neighbour cell's RTD against the reference cell."""

    def __init__(self, monitor_lla, cells_lla: dict[int, tuple[float, float, float]], reference_id: int,
                 window_s: float = 600.0, min_epochs: int = 5, min_std_ns: float = 5.0,
                 timestamp_sigma_ns: float = 0.0):
        """`cells_lla`: cell id -> (lat, lon, height). `min_std_ns` is a floor on the reported uncertainty: the monitor's own
        calibration and position error, which the scatter of the fit cannot show. `timestamp_sigma_ns` is the 1-sigma of one cell's arrival
        timestamp when the monitoring receiver's noise is known: a fit through few epochs underestimates its own scatter, so the fit is
        given at least the noise two such timestamps (the cell's and the reference's) are known to have."""
        if reference_id not in cells_lla:
            raise ValueError("the reference cell must be one of the cells")
        self._ref = reference_id
        self._delay_ns = {cid: geo_distance_m(monitor_lla, lla) / C_MPS * 1e9 for cid, lla in cells_lla.items()}
        self._window_ns = int(window_s * 1e9)
        self._min_epochs = min_epochs
        self._min_std_ns = min_std_ns
        self._ts_sigma_ns = timestamp_sigma_ns
        self._epochs: list[Epoch] = []

    @property
    def reference_std_ns(self) -> Optional[float]:
        """The standard deviation of one arrival timestamp, i.e. of the reference cell's timing as the monitor takes it (None when the monitor's noise is not known)."""
        return self._ts_sigma_ns if self._ts_sigma_ns > 0 else None

    def add(self, epoch: Epoch) -> None:
        if self._ref not in epoch.arrivals_ns:
            raise ValueError("an epoch must include the reference cell")
        if self._epochs and epoch.t_gps_ns <= self._epochs[-1].t_gps_ns:
            raise ValueError("epochs must be added in time order")
        self._epochs.append(epoch)
        cutoff = epoch.t_gps_ns - self._window_ns
        self._epochs = [e for e in self._epochs if e.t_gps_ns >= cutoff]

    def estimate(self) -> dict[int, Rtd]:
        """RTD, drift and uncertainties of every neighbour cell, valid at the time of the newest epoch."""
        if len(self._epochs) < self._min_epochs:
            raise InsufficientData(f"{len(self._epochs)} epochs, need {self._min_epochs}")
        t_last = self._epochs[-1].t_gps_ns
        ids = sorted({cid for e in self._epochs for cid in e.arrivals_ns} - {self._ref})
        out: dict[int, Rtd] = {}
        for cid in ids:
            ts, ys = [], []
            prev = None
            for e in self._epochs:
                if cid not in e.arrivals_ns:
                    continue
                # Difference the integer arrival times first: a GPS time in nanoseconds (about 1.5e18) is beyond float64's resolution
                # of 128 ns, so subtracting a float delay from it would cost that much.
                rtd = float(_wrap_subframe_ns(round((e.arrivals_ns[cid] - e.arrivals_ns[self._ref])
                                                    - (self._delay_ns[cid] - self._delay_ns[self._ref]))))
                if prev is not None:  # unwrap against the previous value so a cell near +-0.5 ms does not jump
                    rtd += SUBFRAME_NS * round((prev - rtd) / SUBFRAME_NS)
                prev = rtd
                ts.append((e.t_gps_ns - t_last) / 1e9)
                ys.append(rtd)
            if len(ts) < self._min_epochs:
                raise InsufficientData(f"cell {cid}: {len(ts)} epochs, need {self._min_epochs}")
            a, b, sigma, sa, sb = _fit_line(ts, ys, floor_sigma=math.sqrt(2) * self._ts_sigma_ns)
            a = float(_wrap_subframe_ns(round(a)))
            out[cid] = Rtd(rtd_ns=a, std_ns=max(sa, self._min_std_ns), valid_at_gps_ns=t_last, drift_ns_per_s=b,
                           drift_std_ns_per_s=sb)
        return out


def geo_distance_m(a_lla, b_lla) -> float:
    d = geo.lla_to_ecef(*a_lla) - geo.lla_to_ecef(*b_lla)
    return float(math.sqrt(float(d @ d)))
