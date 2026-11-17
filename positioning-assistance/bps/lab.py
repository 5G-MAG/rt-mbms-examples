"""A synthetic world for running the option 1 chain against the real broadcast stack in the lab. [design]

The demo radio has one eNB. The assistance document describes a virtual four-cell network around it, so the parts that are live are
the ones option 1 is about: the document travelling over the air in its own MBMS session, and the network time the UE takes from
SIB16. The RSTDs the UE would measure are computed here from a known UE position, the true RTDs (a base value and a drift) and the
network time the UE derived, so a wrong network time or a missing drift shows up as a position error.
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import geo
from .assistance import Prs
from .publisher import CellSite, Network
from .rtd_monitor import Epoch, geo_distance_m
from .timebase import C_MPS, TS_S
from .prs import PrsCell, measure_rstd
from .sim import quantise_rstd_ts, rstd_step_ts
from .ue import Measurement

ORIGIN = (46.2044, 6.1432, 400.0)
CELLS_ENU = np.array([[0.0, 0.0, 30.0], [9000.0, 2500.0, 45.0], [-4000.0, 8000.0, 35.0], [1500.0, -10500.0, 50.0]])
MONITOR_ENU = (1000.0, 500.0, 10.0)
# RTD of cells 2..4 at EPOCH_ns (ns) and its drift (ns per second)
TRUE_RTD = {2: (123_456.0, 3.0), 3: (-80_000.0, -2.0), 4: (40_000.0, 1.5)}
UE_ENU = np.array([2500.0, -3200.0, 1.5])
MONITOR_NOISE_NS = 20.0   # 1-sigma of one arrival timestamp of the broadcaster's monitoring receiver (the RTD in the document is estimated from these)
PRS_INDEX = 2435
UNIX_TO_GPS_NS = 315_964_800 * 10**9  # GPS epoch in Unix time; leap seconds are applied by the caller when they matter

from . import scenario as _scenario

SCENARIO = _scenario.apply_to_lab(sys.modules[__name__])   # BPS_SCENARIO: another geometry for the same chain; None when it is not set


def network(cell_cp: Optional[dict] = None, reference_cp: str = "extended", reference_ecgi: Optional[tuple[str, str, int]] = None) -> Network:
    """The four-cell network of the lab. `reference_cp` is the cyclic prefix of the real cell (the document must state it); `cell_cp` maps a cell identity to \"normal\" or
    \"extended\" for a neighbour that has the other one. The cells share one frame structure (the rig builds every emulated cell on the real cell's cell acquisition
    subframes), so every subframe offset is 0."""
    cell_cp = dict(cell_cp or {})
    cell_cp.setdefault(1, reference_cp)
    sites = []
    for i, p in enumerate(CELLS_ENU):
        lat, lon, h = geo.enu_to_lla(ORIGIN, p)
        # Index 2435 is T_PRS 40, Delta_PRS 0: PRS in subframe 0 of the frames with n_f mod 4 = 0, which on this carrier (MBMS-dedicated,
        # MBSFN subframes at 1.25 kHz) are the only 15 kHz subframes, the cell acquisition subframes. PRS is defined for 15 kHz only
        # (SPEC-CLAIMS.md C28). All cells share it and are told apart by N_ID^PRS. That PRS is allowed there is not established.
        sites.append(CellSite(i + 1, lat, lon, h, Prs(25, PRS_INDEX, 1), cp_length=cell_cp.get(i + 1), ecgi=reference_ecgi if i == 0 else None,
                              subframe_offset=None if i == 0 else 0))
    return Network(sites[0], tuple(sites[1:]))


@dataclass
class World:
    epoch_gps_ns: int           # when TRUE_RTD holds
    noise_ns: float = MONITOR_NOISE_NS
    seed: int = 1

    def __post_init__(self):
        self._rng = np.random.default_rng(self.seed)

    def true_rtd_ns(self, cell_id: int, t_gps_ns: int) -> float:
        base, drift = TRUE_RTD[cell_id]
        return base + drift * (t_gps_ns - self.epoch_gps_ns) / 1e9

    def monitor_epoch(self, t_gps_ns: int) -> Epoch:
        """What a monitoring receiver at MONITOR_ENU would timestamp at t: each cell's frame boundary arrival."""
        net = network()
        lla = net.lla()
        mon = geo.enu_to_lla(ORIGIN, MONITOR_ENU)
        arr = {}
        for cid, p in lla.items():
            d_ns = geo_distance_m(mon, p) / C_MPS * 1e9
            off = 0.0 if cid == 1 else self.true_rtd_ns(cid, t_gps_ns)
            # integer arithmetic: a GPS time in nanoseconds (about 1.5e18) is beyond float64's resolution of 128 ns
            arr[cid] = t_gps_ns + int(round(d_ns + off + self._rng.normal(0, self.noise_ns)))
        return Epoch(t_gps_ns, arr)

    def measurement(self, t_net_ns: int, sample_clock_ns: int, noise_ns: float = 30.0) -> Measurement:
        """The RSTDs a UE at UE_ENU would report at network time t_net (TS 36.214 clause 5.1.12: closest subframe, so the RTD is folded)."""
        d = np.linalg.norm(CELLS_ENU - UE_ENU, axis=1)
        rstd = {}
        for cid in (2, 3, 4):
            wrapped = (self.true_rtd_ns(cid, t_net_ns) + 500_000) % 1_000_000 - 500_000
            rstd[cid] = (d[cid - 1] - d[0]) / C_MPS + wrapped * 1e-9 + self._rng.normal(0, noise_ns) * 1e-9
        return Measurement(sample_clock_ns, rstd, sigma_s=noise_ns * 1e-9)


    def emulated_delay_s(self, cell_id: int, t_gps_ns: int) -> float:
        """Arrival delay of cell `cell_id`'s subframe grid at the lab UE against the reference cell's: propagation difference plus the RTD at network time
        `t_gps_ns`, folded to the closest subframe as TS 36.214 clause 5.1.12 defines the RSTD. What a mixer (bps.rig) puts in the stream."""
        d = np.linalg.norm(CELLS_ENU - UE_ENU, axis=1) / C_MPS
        rtd = 0.0 if cell_id == 1 else (self.true_rtd_ns(cell_id, t_gps_ns) + 500_000) % 1_000_000 - 500_000
        return float(d[cell_id - 1] - d[0] + rtd * 1e-9)

    def measurement_from_prs(self, t_net_ns: int, sample_clock_ns: int, rng: np.random.Generator, snr_db: float = 0.0,
                             estimator_sigma_ns: float = 5.0, nrb: int = 25) -> Measurement:
        """As `measurement`, but each RSTD is estimated from simulated PRS waveforms (bps.prs) and reported with the resolution of
        TS 36.133 V19.6.0 clause 9.1.10.3 (1 Ts up to 4096 Ts, 5 Ts above). N_ID^PRS is the cell identity (clause 6.10.4.1: unless
        configured by higher layers). The weight of each RSTD is the estimator's error and the uniform error of its reporting step,
        step / sqrt(12), added in quadrature. `estimator_sigma_ns` is the rms error measured on this estimator in simulation with the four cells
        in one cell acquisition subframe: 2.4, 2.5 and 5.6 ns at 10, 0 and -10 dB per sample (25 PRB, one path, 90 estimates each); a
        different channel needs a different value."""
        d = np.linalg.norm(CELLS_ENU - UE_ENU, axis=1) / C_MPS
        net = network()
        cells = []
        for site in (net.reference, *net.neighbours):
            cid = site.phys_cell_id
            rtd = 0.0 if cid == 1 else (self.true_rtd_ns(cid, t_net_ns) + 500_000) % 1_000_000 - 500_000
            cells.append(PrsCell(cid, site.prs.configuration_index, d[cid - 1] - d[0] + rtd * 1e-9))
        reported = {cid: quantise_rstd_ts(r) for cid, r in zip((2, 3, 4), measure_rstd(cells, nrb, snr_db, rng, n_f=0))}
        sigma = {cid: math.hypot(estimator_sigma_ns * 1e-9, rstd_step_ts(r) * TS_S / math.sqrt(12)) for cid, r in reported.items()}
        return Measurement(sample_clock_ns, reported, sigma_s=max(sigma.values()), sigma_by_cell=sigma)


def host_gps_ns(leap_seconds: int = 18) -> int:
    """The host clock as network (GPS) time, the way rt-mbms-tx stamps SIB16: GPS = UTC + leapSeconds."""
    return time.time_ns() - UNIX_TO_GPS_NS + leap_seconds * 10**9
