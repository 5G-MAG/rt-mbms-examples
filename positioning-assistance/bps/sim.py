"""Software-in-the-loop run of the whole application chain, with simulated radio measurements. [design]

What is simulated: the geometry, each cell's transmit grid offset (RTD, with drift), the UE's RSTD measurement (Gaussian error,
then the reporting quantisation of TS 36.133 V19.6.0 clause 9.1.10.3: 1 Ts for |RSTD| <= 4096 Ts), and the SIB16 time label
(0.25 us granularity, network-referenced). What is real: the assistance document round trip, PRS occasion arithmetic, the
solver and the time recovery. Nothing here exercises a modem or PRS on the air.
"""
from __future__ import annotations

import argparse
import math

import numpy as np

from . import geo
from .assistance import AssistanceDocument, Cell, Prs, Rtd
from .solver import solve_rstd, ue_time_ns
from .timebase import (C_MPS, SUBFRAME_NS, TS_S, TimeAnchor, gps_ns_to_sib16_r15_fields, next_prs_occasion_start,
                       sib16_r15_time_to_ns)

ORIGIN_LLA = (46.2044, 6.1432, 400.0)  # arbitrary lab origin (Geneva area); synthetic, not a real deployment
# Synthetic ENU layout (m): reference cell and three neighbours, 6 to 12 km apart.
CELLS_ENU = np.array([[0.0, 0.0, 30.0], [9000.0, 2500.0, 45.0], [-4000.0, 8000.0, 35.0], [1500.0, -10500.0, 50.0]])
PRS_INDEX = [2400, 2401, 2402, 2403]  # T_PRS = 5 subframes, staggered offsets (Table 6.10.4.3-1)


def quantise_rstd_ts(rstd_s: float) -> float:
    """[spec] TS 36.133 V19.6.0 clause 9.1.10.3: reporting range -15391..15391 Ts, 1 Ts resolution for |RSTD| <= 4096 Ts and
    5 Ts above. Returns the centre of the reported bin, in seconds. (Bin edge inclusivity is irrelevant at this precision.)
    """
    x = rstd_s / TS_S
    if abs(x) > 15391:
        raise ValueError("|RSTD| > 15391 Ts is outside the reporting range")
    if abs(x) <= 4096:
        return (math.ceil(x) - 0.5) * TS_S
    k = math.ceil((abs(x) - 4096) / 5)
    centre = 4096 + 5 * k - 2.5
    return math.copysign(centre, x) * TS_S


def build_document(true_rtd_ns, rtd_error_ns, drift_ns_s, drift_error_ns_s, std_ns, t0_gps_ns, rng) -> AssistanceDocument:
    cells = []
    for i, p in enumerate(CELLS_ENU):
        lat, lon, h = geo.enu_to_lla(ORIGIN_LLA, p)
        rtd = None
        if i > 0:
            rtd = Rtd(true_rtd_ns[i] + rng.normal(0, rtd_error_ns), std_ns, t0_gps_ns, drift_ns_s[i] + rng.normal(0, drift_error_ns_s), drift_error_ns_s)
        cells.append(Cell(i + 1, lat, lon, h, Prs(25, PRS_INDEX[i], 1), rtd))
    return AssistanceDocument(1, t0_gps_ns, t0_gps_ns + 3600 * 10**9, cells[0], cells[1:])


def measure_and_solve(doc_json: str, ue_enu, true_rtd_ns, drift_ns_s, t0_gps_ns, elapsed_s, noise_ns, rng, fix_up=True):
    """UE side: parse the document, measure RSTDs at time t0+elapsed, solve. Returns (horizontal error m, sigma m, solution)."""
    doc = AssistanceDocument.from_json(doc_json)
    cells_lla = [doc.reference, *doc.neighbours]
    cells = np.array([geo.lla_to_enu(ORIGIN_LLA, (c.latitude_deg, c.longitude_deg, c.height_m)) for c in cells_lla])
    t_meas = t0_gps_ns + int(elapsed_s * 1e9)
    d = np.linalg.norm(CELLS_ENU - ue_enu, axis=1)
    rstd = np.zeros(len(cells))
    rtd = np.zeros(len(cells))
    sigma = np.full(len(cells), 1.0)
    for i in range(1, len(cells)):
        truth = (d[i] - d[0]) / C_MPS + (true_rtd_ns[i] + drift_ns_s[i] * elapsed_s) * 1e-9
        rstd[i] = quantise_rstd_ts(truth + rng.normal(0, noise_ns) * 1e-9)
        rtd[i] = doc.neighbours[i - 1].rtd.at(t_meas) * 1e-9  # drift applied with the SIB16-derived time
        sigma[i] = math.hypot(noise_ns * 1e-9, doc.neighbours[i - 1].rtd.std_at(t_meas) * 1e-9, TS_S / math.sqrt(12))
    sol = solve_rstd(cells, rstd, rtd, sigma, ref=0, fix_up_m=ue_enu[2] if fix_up else None)
    err = float(np.linalg.norm(sol.position_enu[:2] - ue_enu[:2]))
    return err, sol.horizontal_sigma_m, sol


REASONS: dict[str, int] = {}


def run(trials, noise_ns, rtd_error_ns, drift_error_ns_s, elapsed_s, seed, use_drift_model=True):
    rng = np.random.default_rng(seed)
    t0 = sib16_r15_time_to_ns(15000, 40000, 123, 7)  # synthetic GPS-time instant
    errs, sigmas, fails = [], [], 0
    for _ in range(trials):
        ue = np.array([rng.uniform(-3000, 6000), rng.uniform(-6000, 6000), 1.5])
        true_rtd = np.concatenate([[0.0], rng.uniform(-200e3, 200e3, 3)])  # unsynchronised grids, up to +-0.2 ms
        drift = np.concatenate([[0.0], rng.normal(0, 5.0, 3)])  # ns per second
        doc = build_document(true_rtd, rtd_error_ns, drift if use_drift_model else np.zeros(4), drift_error_ns_s, rtd_error_ns, t0, rng)
        try:
            err, sig, sol = measure_and_solve(doc.to_json(), ue, true_rtd, drift, t0, elapsed_s, noise_ns, rng)
        except ValueError as exc:
            fails += 1
            REASONS[str(exc)[:60]] = REASONS.get(str(exc)[:60], 0) + 1
            continue
        if sol.converged:
            errs.append(err)
            sigmas.append(sig)
        else:
            fails += 1
            REASONS["solver did not converge"] = REASONS.get("solver did not converge", 0) + 1
    e = np.array(errs)
    return {"n": len(e), "fail": fails, "rmse_m": float(np.sqrt(np.mean(e**2))), "p50_m": float(np.percentile(e, 50)),
            "p95_m": float(np.percentile(e, 95)), "mean_claimed_sigma_m": float(np.mean(sigmas))}


def alignment_check(seed=1):
    """SIB16 anchor -> time of a PRS occasion -> UE time after the fix, with 0.25 us labels and uncompensated delay."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(1000):
        true_start_ns = int(rng.integers(10**18, 2 * 10**18))
        # true_start_ns is the true network instant of the SFN boundary, not aligned to the 0.25 us label grid
        label_ns = sib16_r15_time_to_ns(*gps_ns_to_sib16_r15_fields(true_start_ns))  # label truncated to 0.25 us
        anchor = TimeAnchor(abs_subframe=40960, gps_ns=label_ns)
        ue_pos = np.array([rng.uniform(-3000, 6000), rng.uniform(-6000, 6000), 1.5])
        dist = float(np.linalg.norm(CELLS_ENU[0] - ue_pos))
        k = next_prs_occasion_start(40960 + 1234, PRS_INDEX[0])  # a later PRS occasion of the reference cell
        true_rx = true_start_ns + (k - 40960) * SUBFRAME_NS + round(dist / C_MPS * 1e9)
        est_rx = ue_time_ns(anchor, k, dist)
        out.append(est_rx - true_rx)
    out = np.array(out)
    return float(out.min()), float(out.max())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    print("Option 1 (one SFN'ed SIB16, RTD in the document): 4 cells, 2D fix with known height, synthetic layout 6 to 12 km apart")
    print(f"{'noise ns':>9} {'rtd err ns':>11} {'drift err ns/s':>15} {'elapsed s':>10} {'drift model':>12} | {'n':>4} {'fail':>4} {'rmse m':>8} {'p50 m':>7} {'p95 m':>7} {'claimed 1s m':>13}")
    cases = [(30, 0, 0, 0, True), (30, 20, 0, 0, True), (30, 100, 0, 0, True), (100, 20, 0, 0, True),
             (30, 20, 0.5, 600, True), (30, 20, 0.5, 600, False)]
    for noise, rerr, derr, el, model in cases:
        r = run(a.trials, noise, rerr, derr, el, a.seed, model)
        print(f"{noise:>9} {rerr:>11} {derr:>15} {el:>10} {str(model):>12} | {r['n']:>4} {r['fail']:>4} {r['rmse_m']:>8.1f} {r['p50_m']:>7.1f} {r['p95_m']:>7.1f} {r['mean_claimed_sigma_m']:>13.1f}")
    print("failure reasons:", REASONS)
    lo, hi = alignment_check()
    print(f"\nSIB16 alignment: UE-time error after the fix, 1000 draws: min {lo} ns, max {hi} ns (label truncation gives -249..0 ns, delay rounded to 1 ns)")


if __name__ == "__main__":
    main()
