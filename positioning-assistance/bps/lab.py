"""A synthetic world for running the option 1 chain against the real broadcast stack in the lab. [design]

The demo radio has one eNB. The assistance document describes a virtual four-cell network around it, so the parts that are live are
the ones option 1 is about: the document travelling over the air in its own MBMS session, and the network time the UE takes from
SIB16. The RSTDs the UE would measure are computed here from a known UE position, the true RTDs (a base value and a drift) and the
network time the UE derived, so a wrong network time or a missing drift shows up as a position error.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from . import geo
from .assistance import Prs
from .publisher import CellSite, Network
from .rtd_monitor import Epoch, geo_distance_m
from .timebase import C_MPS, TS_S
from .ue import Measurement

ORIGIN = (46.2044, 6.1432, 400.0)
CELLS_ENU = np.array([[0.0, 0.0, 30.0], [9000.0, 2500.0, 45.0], [-4000.0, 8000.0, 35.0], [1500.0, -10500.0, 50.0]])
MONITOR_ENU = (1000.0, 500.0, 10.0)
# RTD of cells 2..4 at EPOCH_ns (ns) and its drift (ns per second)
TRUE_RTD = {2: (123_456.0, 3.0), 3: (-80_000.0, -2.0), 4: (40_000.0, 1.5)}
UE_ENU = np.array([2500.0, -3200.0, 1.5])
UNIX_TO_GPS_NS = 315_964_800 * 10**9  # GPS epoch in Unix time; leap seconds are applied by the caller when they matter


def network() -> Network:
    sites = []
    for i, p in enumerate(CELLS_ENU):
        lat, lon, h = geo.enu_to_lla(ORIGIN, p)
        sites.append(CellSite(i + 1, lat, lon, h, Prs(25, 2400 + i, 1)))
    return Network(sites[0], tuple(sites[1:]))


@dataclass
class World:
    epoch_gps_ns: int           # when TRUE_RTD holds
    noise_ns: float = 20.0
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


def host_gps_ns(leap_seconds: int = 18) -> int:
    """The host clock as network (GPS) time, the way rt-mbms-tx stamps SIB16: GPS = UTC + leapSeconds."""
    return time.time_ns() - UNIX_TO_GPS_NS + leap_seconds * 10**9
