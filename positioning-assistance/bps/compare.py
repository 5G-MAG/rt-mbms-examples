"""Experiments behind the numbers in bps.sync and the README. [design]  `python3 -m bps.compare sigma` and `python3 -m bps.compare fixes`.

sigma: rms error of the time difference estimated from the PSS and SSS, by SNR per resource element, with the four lab cells overlapping at equal power and random
relative delays within +-3 us of the lab's. fixes: position error of the lab UE from PRS waveforms and from the PSS and SSS, under the same conditions: one path,
white noise, no reporting step (a UE-internal estimate), same solver and same weighting rule (the estimator's own error).
"""
from __future__ import annotations

import argparse
import math

import numpy as np

from . import lab
from .prs import PrsCell, measure_rstd, prs_subframe
from .solver import solve_rstd
from .sync import SyncCell, measure_rstd_sync
from .timebase import C_MPS
from .ue import Measurement


def _lab_delays(world: lab.World, t_ns: int) -> list[float]:
    d = np.linalg.norm(lab.CELLS_ENU - lab.UE_ENU, axis=1) / C_MPS
    return [0.0] + [d[c - 1] - d[0] + ((world.true_rtd_ns(c, t_ns) + 500_000) % 1_000_000 - 500_000) * 1e-9 for c in (2, 3, 4)]


def sigma_table(snrs=(30.0, 20.0, 10.0, 0.0), draws: int = 14, seed: int = 77, cancel: bool = True) -> dict[float, float]:
    world = lab.World(epoch_gps_ns=1_500_000_000 * 10**9, seed=2)
    base = _lab_delays(world, world.epoch_gps_ns + 40 * 10**9)
    out = {}
    for snr in snrs:
        rng = np.random.default_rng(seed)
        errs = []
        for _ in range(draws):
            dl = [0.0] + [x + rng.uniform(-3, 3) * 1e-6 for x in base[1:]]
            est = measure_rstd_sync([SyncCell(i + 1, dl[i]) for i in range(4)], 25, snr, rng, cancel=cancel)
            errs += [est[i - 1] - dl[i] for i in (1, 2, 3)]
        out[snr] = float(np.sqrt(np.mean(np.square(errs))) * 1e9)
    return out


def _fix_error(world, make, n, step_s):
    errs, claimed = [], []
    for k in range(n):
        t = world.epoch_gps_ns + int((40 + step_s * k) * 1e9)
        m = make(t, np.random.default_rng(k))
        rstd = np.array([0.0] + [m.rstd_s[c] for c in (2, 3, 4)])
        rtd = np.array([0.0] + [((world.true_rtd_ns(c, t) + 500_000) % 1_000_000 - 500_000) * 1e-9 for c in (2, 3, 4)])
        sg = np.array([1.0] + [m.sigma_by_cell[c] for c in (2, 3, 4)])
        sol = solve_rstd(lab.CELLS_ENU, rstd, rtd, sg, ref=0, fix_up_m=lab.UE_ENU[2])
        errs.append(float(np.linalg.norm(sol.position_enu[:2] - lab.UE_ENU[:2])))
        claimed.append(sol.horizontal_sigma_m)
    return math.sqrt(np.mean(np.square(errs))), math.sqrt(np.mean(np.square(claimed))), max(errs)


def prs_unquantised(world: lab.World, t_ns: int, rng, snr_db: float, sigma_ns: float) -> Measurement:
    """The PRS measurement without the TS 36.133 reporting step, for a like-for-like comparison with the sync path."""
    cells = []
    d = _lab_delays(world, t_ns)
    net = lab.network()
    for i, site in enumerate((net.reference, *net.neighbours)):
        cells.append(PrsCell(site.phys_cell_id, site.prs.configuration_index, d[i]))
    est = dict(zip((2, 3, 4), measure_rstd(cells, 25, snr_db, rng, n_f=0)))
    return Measurement(0, est, sigma_s=sigma_ns * 1e-9, sigma_by_cell={c: sigma_ns * 1e-9 for c in est})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sigma")
    s.add_argument("--draws", type=int, default=14)
    s.add_argument("--seed", type=int, default=77)
    s.add_argument("--no-cancel", action="store_true")
    f = sub.add_parser("fixes")
    f.add_argument("--fixes", type=int, default=30)
    a = ap.parse_args()
    if a.cmd == "sigma":
        t = sigma_table(draws=a.draws, seed=a.seed, cancel=not a.no_cancel)
        print(f"time difference from PSS and SSS, cancel={not a.no_cancel}, {a.draws} draws of 3 cells per SNR, seed {a.seed}")
        for snr, v in t.items():
            print(f"  {snr:5.1f} dB per resource element: rms {v:6.1f} ns")
        return
    world = lab.World(epoch_gps_ns=1_500_000_000 * 10**9, seed=2)
    unit = float(np.mean(np.abs(prs_subframe(2, 1, 25)) ** 2))
    print(f"lab UE, {a.fixes} fixes, one path, no reporting step. PRS 0 dB per sample is {10 * math.log10(1 / unit):.1f} dB per resource element.")
    print(f"{'measurement':<46}{'rmse m':>8}{'claimed m':>11}{'max m':>8}")
    rows = [("PRS, 0 dB per sample (12.5 dB/RE), sigma 5 ns", lambda t, r: prs_unquantised(world, t, r, 0.0, 5.0)),
            ("PRS, -10 dB per sample (2.5 dB/RE), sigma 6 ns", lambda t, r: prs_unquantised(world, t, r, -10.0, 6.0)),
            ("PRS with the TS 36.133 reporting step, 0 dB", lambda t, r: world.measurement_from_prs(t, 0, r, snr_db=0.0)),
            ("PSS/SSS cancelling, 20 dB per RE", lambda t, r: world.measurement_from_sync(t, 0, r, snr_db=20.0)),
            ("PSS/SSS cancelling, 10 dB per RE", lambda t, r: world.measurement_from_sync(t, 0, r, snr_db=10.0)),
            ("PSS/SSS cancelling, 0 dB per RE", lambda t, r: world.measurement_from_sync(t, 0, r, snr_db=0.0))]
    for label, make in rows:
        rmse, claimed, worst = _fix_error(world, make, a.fixes, 4)
        print(f"{label:<46}{rmse:8.1f}{claimed:11.1f}{worst:8.1f}")


if __name__ == "__main__":
    main()
