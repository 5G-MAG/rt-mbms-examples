"""Runs a scenario (bps.scenario) on the real platform and stores what the UE got. [design]

    python3 -m bps.scenario_run scenario.json [--out DIR] [--seconds N] [--warmup S]

For each run this resets the demo (`./demo down`, `./demo up` with the rig on), starts the assistance publisher, runs the UE for the scenario's duration with the
modem measuring the PRS it receives, and stops everything. What runs is the real chain: rt-mbms-tx sends the real cell, the mixer adds the emulated cells at the powers,
delays, noise and carrier offsets of the scenario, rt-mbms-modem measures the time differences, the assistance document and SIB16 time travel over the air, and the UE
solves a position against the position the scenario gives it. The result is written as JSON next to the scenario's name; bps.scenario_report turns it into a page.

The run takes the time the platform takes (about a minute to start, then the UE's duration, a fix every 2 s at best): it is real time, not a fast simulation.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import scenario as sc

ROOT = Path(__file__).resolve().parents[2]            # rt-mbms-examples
DEMO_DIR = ROOT / "scripts" / "mbms-broadcast-demo"
STATE_HINT = Path.home() / ".local" / "state" / "mbms-broadcast-tutorial"


def resolve(s: sc.Scenario) -> tuple[list, list, dict]:
    """The cells, the UE position and the received powers the run uses: the scenario's, and the lab's for what it leaves out."""
    from . import lab
    cells = s.cells_enu if s.cells_enu is not None else lab.CELLS_ENU.tolist()
    ue = s.ue_enu if s.ue_enu is not None else lab.UE_ENU.tolist()
    return cells, ue, s.received_power_db(cells, ue)


def environment(s: sc.Scenario, scenario_path: str, base: dict | None = None) -> dict:
    """The environment the demo scripts, the publisher, the mixer and the UE run in for this scenario."""
    _, _, powers = resolve(s)
    env = dict(base if base is not None else os.environ)
    env.update({"BPS_SCENARIO": os.path.abspath(scenario_path), "POS_RIG": "1", "POS_MEASURE": "modem", "POS_RIG_CP": s.cp,
                "POS_CELL_POWER": " ".join(f"{c}={powers[c]:.2f}" for c in sc.EMULATED)})
    if s.snr_db is not None:
        env["POS_RIG_SNR_DB"] = str(s.snr_db)
    if s.cfo_hz:
        env["POS_CELL_CFO"] = " ".join(f"{c}={hz}" for c, hz in sorted(s.cfo_hz.items()))
    return env


def _percentile(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else float("nan")


def summarise(records: list[dict], modem_stats: dict | None = None) -> dict:
    """Accuracy figures from the UE's records (one JSON object per fix, refusal or document)."""
    fixes = [r for r in records if r["kind"] == "fix"]
    refusals: dict[str, int] = {}
    for r in records:
        if r["kind"] == "no_fix":
            key = r["reason"].split(" (")[0]
            refusals[key] = refusals.get(key, 0) + 1
    errs = [f["error_m"] for f in fixes]
    out = {"fixes": len(fixes), "refusals": refusals, "documents": sum(1 for r in records if r["kind"] == "document" and r["verdict"] == "accepted")}
    if errs:
        e = np.array(errs)
        east = np.array([f["east_err_m"] for f in fixes])   # the fix against the true position: east and north error in metres
        north = np.array([f["north_err_m"] for f in fixes])
        out.update({"rms_m": float(math.sqrt(np.mean(e ** 2))), "mean_m": float(e.mean()), "p50_m": _percentile(errs, 50), "p95_m": _percentile(errs, 95),
                    "max_m": float(e.max()), "claimed_mean_m": float(np.mean([f["claimed_m"] for f in fixes])),
                    "claimed_ratio": float(np.mean([f["error_m"] / f["claimed_m"] for f in fixes if f["claimed_m"] > 0])),
                    "east_err_mean_m": float(east.mean()), "north_err_mean_m": float(north.mean()), "east_err_sd_m": float(east.std()), "north_err_sd_m": float(north.std())})
    per_cell: dict[str, list[float]] = {}
    for f in fixes:
        for cid, v in f.get("rstd_error_ns", {}).items():
            per_cell.setdefault(cid, []).append(v)
    out["rstd_error_ns"] = {cid: {"n": len(v), "mean": float(np.mean(v)), "sd": float(np.std(v)), "rms": float(math.sqrt(np.mean(np.square(v))))} for cid, v in sorted(per_cell.items())}
    cells_used: dict[str, int] = {}
    for f in fixes:
        key = " ".join(str(c) for c in f["cells"])
        cells_used[key] = cells_used.get(key, 0) + 1
    out["cells_used"] = cells_used
    if modem_stats is not None:
        out["modem"] = modem_stats
    return out


def _sh(cmd: list[str], env: dict, cwd: Path, log, timeout: int | None = None) -> int:
    return subprocess.run(cmd, env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout).returncode


def run(scenario_path: str, out_dir: Path, seconds: int | None = None, warmup: int = 20) -> Path:
    s = sc.load(scenario_path)
    seconds = seconds or s.seconds
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"{s.name}-{stamp}"
    env = environment(s, scenario_path)
    env["POS_UE_JSON"] = str(base) + ".records.jsonl"
    log = open(str(base) + ".log", "w")
    modem_stats = None
    try:
        _sh(["./demo", "down"], env, ROOT, log)
        if _sh(["./demo", "up"], env, ROOT, log, timeout=900) != 0:
            raise RuntimeError(f"demo up failed, see {base}.log")
        if _sh(["./09-start-positioning.sh"], env, DEMO_DIR, log, timeout=300) != 0:
            raise RuntimeError(f"positioning did not start, see {base}.log")
        time.sleep(warmup)
        _sh(["./10-run-positioning-ue.sh", str(seconds)], env, DEMO_DIR, log, timeout=seconds + 120)
        try:
            import urllib.request
            modem_stats = json.load(urllib.request.urlopen("http://10.80.0.2:3010/modem-api/prs", timeout=5)).get("stats")
        except (OSError, ValueError):
            modem_stats = None
    finally:
        _sh(["./demo", "down"], env, ROOT, log)
        log.close()
    records = []
    if os.path.exists(env["POS_UE_JSON"]):
        records = [json.loads(line) for line in open(env["POS_UE_JSON"]) if line.strip()]
    rev = {}
    for name, path in (("examples", ROOT), ("modem", ROOT.parent / "rt-mbms-modem"), ("tx", ROOT.parent / "rt-mbms-tx")):
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        rev[name] = r.stdout.strip() or "unknown"
    result = {"scenario": json.load(open(scenario_path)), "name": s.name, "started": stamp, "ue_seconds": seconds, "revisions": rev,
              "cells_enu": resolve(s)[0], "ue_enu": resolve(s)[1], "received_power_db": resolve(s)[2],
              "summary": summarise(records, modem_stats), "records": [r for r in records if r["kind"] in ("fix", "no_fix")]}
    path = Path(str(base) + ".json")
    path.write_text(json.dumps(result, indent=1))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenario")
    ap.add_argument("--out", default=str(ROOT / "positioning-assistance" / "results" / "scenarios"))
    ap.add_argument("--seconds", type=int)
    ap.add_argument("--warmup", type=int, default=20, help="seconds between the publisher's start and the UE's (the document and the modem's lock need them)")
    a = ap.parse_args()
    path = run(a.scenario, Path(a.out), a.seconds, a.warmup)
    s = json.load(open(path))["summary"]
    print(f"{path}\nfixes {s['fixes']}  rms {s.get('rms_m', float('nan')):.1f} m  p95 {s.get('p95_m', float('nan')):.1f} m  claimed {s.get('claimed_mean_m', float('nan')):.1f} m  refusals {s['refusals']}")


if __name__ == "__main__":
    main()
