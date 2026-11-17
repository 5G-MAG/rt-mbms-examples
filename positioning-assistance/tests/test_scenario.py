import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

from bps import lab, scenario as sc, scenario_report as report, scenario_run as run

HERE = Path(__file__).resolve().parents[1]


def write(obj) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(obj, f)
    f.close()
    return f.name


class Loading(unittest.TestCase):
    def test_an_empty_scenario_is_the_labs_own(self):
        s = sc.load(write({}))
        self.assertEqual((s.name, s.cp, s.seconds, s.snr_db), ("lab", "extended", 150, None))
        cells, ue, powers = run.resolve(s)
        np.testing.assert_allclose(cells, lab.CELLS_ENU)
        np.testing.assert_allclose(ue, lab.UE_ENU)
        self.assertEqual(powers, {2: -6.0, 3: -6.0, 4: -6.0})

    def test_unknown_fields_and_bad_values_are_refused(self):
        for bad in ({"colour": 1}, {"cells_enu": [[0, 0, 0]]}, {"ue_enu": [1, 2]}, {"rtd": {"5": [0, 0]}}, {"rtd": {"2": [0]}}, {"cp": "long"},
                    {"path_loss_exponent": 0}, {"cfo_hz": {"1": 5}}, {"monitor_noise_ns": 0}):
            with self.assertRaises(ValueError, msg=str(bad)):
                sc.load(write(bad))

    def test_explicit_power_beats_the_path_loss_model(self):
        s = sc.load(write({"path_loss_exponent": 3.0, "power_db": {"2": -4}}))
        p = run.resolve(s)[2]
        self.assertEqual(p[2], -4.0)
        self.assertLess(p[3], -6.0)                      # cell 3 is farther from the UE than the real cell

    def test_path_loss_follows_the_distance_ratio(self):
        cells = [[0, 0, 0], [2000, 0, 0], [0, 4000, 0], [0, -1000, 0]]
        s = sc.Scenario(cells_enu=cells, ue_enu=[0, 0, 0], path_loss_exponent=2.0, tx_offset_db={"4": 3.0})
        p = s.received_power_db(cells, [100, 0, 0])
        d = [np.hypot(c[0] - 100, c[1]) for c in cells]
        self.assertAlmostEqual(p[2], -20 * np.log10(d[1] / d[0]), places=6)
        self.assertAlmostEqual(p[4], -20 * np.log10(d[3] / d[0]) + 3.0, places=6)


class AppliedToTheLab(unittest.TestCase):
    def test_the_lab_reads_the_scenario_named_by_the_environment(self):
        path = write({"name": "x", "ue_enu": [10, 20, 1.5], "cells_enu": [[0, 0, 30], [1000, 0, 30], [0, 1000, 30], [-1000, -1000, 30]], "rtd": {"3": [5000, 0.5]}})
        env = dict(os.environ, BPS_SCENARIO=path, PYTHONPATH=str(HERE))
        out = subprocess.run([sys.executable, "-c", "from bps import lab; print(lab.UE_ENU.tolist(), lab.CELLS_ENU[1].tolist(), lab.TRUE_RTD[3], lab.TRUE_RTD[2], lab.SCENARIO.name)"],
                             env=env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "[10.0, 20.0, 1.5] [1000.0, 0.0, 30.0] (5000.0, 0.5) (123456.0, 3.0) x")

    def test_the_monitors_noise_reaches_the_world_the_publisher_builds(self):
        env = dict(os.environ, BPS_SCENARIO=write({"monitor_noise_ns": 75}), PYTHONPATH=str(HERE))
        out = subprocess.run([sys.executable, "-c", "from bps import lab; print(lab.World(epoch_gps_ns=0).noise_ns)"], env=env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "75.0")

    def test_without_the_variable_nothing_changes(self):
        env = {k: v for k, v in os.environ.items() if k != "BPS_SCENARIO"}
        env["PYTHONPATH"] = str(HERE)
        out = subprocess.run([sys.executable, "-c", "from bps import lab; print(lab.SCENARIO, lab.UE_ENU.tolist(), lab.World(epoch_gps_ns=0).noise_ns)"], env=env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "None [2500.0, -3200.0, 1.5] 20.0")

    def test_the_environment_of_a_run(self):
        s = sc.Scenario(snr_db=15.0, cfo_hz={"3": 150}, cp="normal", power_db={"2": -8, "3": -11, "4": -9})
        env = run.environment(s, "scn.json", {})
        self.assertEqual((env["POS_RIG"], env["POS_MEASURE"], env["POS_RIG_CP"], env["POS_RIG_SNR_DB"], env["POS_CELL_CFO"]), ("1", "modem", "normal", "15.0", "3=150"))
        self.assertEqual(env["POS_CELL_POWER"], "2=-8.00 3=-11.00 4=-9.00")
        self.assertTrue(os.path.isabs(env["BPS_SCENARIO"]))


class Summary(unittest.TestCase):
    @staticmethod
    def fix(err, east, north, claimed=5.0, cells=(2, 3, 4), rstd=None):
        return {"kind": "fix", "error_m": err, "east_err_m": east, "north_err_m": north, "claimed_m": claimed, "cells": list(cells), "rstd_error_ns": rstd or {}}

    def test_figures_from_records(self):
        recs = [{"kind": "document", "verdict": "accepted", "bytes": 5000},
                self.fix(3.0, 1.0, 2.0, rstd={"2": 10.0, "3": -10.0}), self.fix(4.0, 2.0, 2.0, rstd={"2": 30.0, "3": 10.0}), self.fix(5.0, 3.0, 2.0, cells=(2, 3)),
                {"kind": "no_fix", "reason": "waiting for a PRS measurement (too few cells)"}, {"kind": "no_fix", "reason": "waiting for a PRS measurement"}]
        s = run.summarise(recs, {"windows": 7})
        self.assertEqual((s["fixes"], s["documents"], s["refusals"]), (3, 1, {"waiting for a PRS measurement": 2}))
        self.assertAlmostEqual(s["rms_m"], np.sqrt((9 + 16 + 25) / 3))
        self.assertAlmostEqual(s["p50_m"], 4.0)
        self.assertAlmostEqual(s["east_err_mean_m"], 2.0)
        self.assertAlmostEqual(s["claimed_ratio"], (3 + 4 + 5) / 3 / 5.0)
        self.assertEqual(s["rstd_error_ns"]["2"], {"n": 2, "mean": 20.0, "sd": 10.0, "rms": np.sqrt(500)})
        self.assertEqual(s["cells_used"], {"2 3 4": 2, "2 3": 1})
        self.assertEqual(s["modem"], {"windows": 7})

    def test_no_fixes_is_a_summary_not_an_error(self):
        s = run.summarise([{"kind": "no_fix", "reason": "no assistance document received"}])
        self.assertEqual(s["fixes"], 0)
        self.assertNotIn("rms_m", s)


class Report(unittest.TestCase):
    """The page is built from made-up records: these tests check the page, not the platform's accuracy."""

    @staticmethod
    def result(name, errors):
        s = sc.load(write({"name": name}))
        cells, ue, powers = run.resolve(s)
        recs = [Summary.fix(e, e * 0.6, e * 0.8, claimed=5.0, rstd={"2": e, "3": -e}) for e in errors]
        recs.append({"kind": "no_fix", "reason": "waiting for a PRS measurement"})
        return {"scenario": {"name": name}, "name": name, "started": "x", "ue_seconds": 60, "revisions": {"examples": "abc"}, "cells_enu": cells, "ue_enu": ue,
                "received_power_db": powers, "summary": run.summarise(recs), "records": recs}

    def test_one_result_gives_every_section(self):
        h = report.page([self.result("a", [2.0, 3.0, 4.0])])
        for part in ("<svg", "east error (m)", "Summary", "Distribution of the error", "Time-difference error per cell", "true position", "waiting for a PRS measurement"):
            self.assertIn(part, h)
        self.assertEqual(h.count("<polyline"), 1)
        self.assertEqual(h.count("<ellipse"), 1)

    def test_several_results_are_compared_in_one_plot(self):
        h = report.page([self.result("a", [2.0, 3.0]), self.result("b", [8.0, 9.0, 10.0])])
        self.assertEqual(h.count("<polyline"), 2)
        self.assertEqual(h.count("<h2>"), 4)

    def test_a_run_without_fixes_still_makes_a_page(self):
        r = self.result("empty", [])
        r["summary"] = run.summarise(r["records"])
        h = report.page([r])
        self.assertIn("No fix, so no time differences", h)
        self.assertNotIn("nan", h.lower().replace("none", ""))

    def test_a_fix_without_error_is_drawn_on_the_true_position(self):
        r = self.result("a", [])
        r["records"] = [{"kind": "fix", "error_m": 0.0, "east_err_m": 0.0, "north_err_m": 0.0, "claimed_m": 5.0, "cells": [2, 3, 4], "rstd_error_ns": {}}]
        svg = report.plan_view(r, "#000")
        cx, cy = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)"', svg).groups()
        x0, y0 = re.search(r'd="M([\d.]+) ([\d.]+) h12', svg).groups()
        self.assertEqual((float(cx), float(cy)), (float(x0) + 6, float(y0)))

    def test_names_are_escaped(self):
        self.assertNotIn("<script>", report.page([self.result("<script>x</script>", [1.0])]))


class RunProgress(unittest.TestCase):
    """The runner's status file and its behaviour when it fails or is stopped, with the demo scripts replaced by a recorder (the platform is not started)."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.scn = write({"name": "t"})
        self.status = self.dir / "status.json"
        self.calls, self.phases = [], []
        real_set = run.Status.set

        def record(status, phase, **extra):
            self.phases.append(phase)
            real_set(status, phase, **extra)

        patches = [mock.patch.object(run.Status, "set", record), mock.patch.object(run.time, "sleep", lambda s: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def sh(self, outcome=None):
        def fake(cmd, env, cwd, log, timeout=None):
            self.calls.append(cmd[:2])
            if outcome and cmd[0] == outcome[0]:
                if isinstance(outcome[1], BaseException):
                    raise outcome[1]
                return outcome[1]
            return 0
        return mock.patch.object(run, "_sh", fake)

    def test_a_run_goes_through_the_phases_and_ends_done_with_its_result(self):
        with self.sh():
            path = run.run(self.scn, self.dir / "out", 30, 1, str(self.status))
        self.assertEqual(self.phases, ["starting", "warmup", "measuring", "stopping", "done"])
        st = json.loads(self.status.read_text())
        self.assertEqual((st["phase"], st["result"], st["name"], st["seconds"]), ("done", str(path), "t", 30))
        self.assertTrue(path.exists() and st["records"].endswith(".records.jsonl"))
        self.assertEqual(self.calls[0], ["./demo", "down"])
        self.assertEqual(self.calls[-1], ["./demo", "down"])

    def test_a_demo_that_does_not_start_is_a_failed_run_and_the_demo_is_brought_down(self):
        fail_up = mock.patch.object(run, "_sh", lambda cmd, env, cwd, log, timeout=None: (self.calls.append(cmd[:2]), 1 if cmd[:2] == ["./demo", "up"] else 0)[1])
        with fail_up:
            with self.assertRaises(RuntimeError):
                run.run(self.scn, self.dir / "out", 30, 1, str(self.status))
        st = json.loads(self.status.read_text())
        self.assertEqual(st["phase"], "failed")
        self.assertIn("demo up failed", st["error"])
        self.assertEqual(self.calls[-1], ["./demo", "down"])

    def test_a_stop_request_ends_the_run_stopped_and_brings_the_demo_down(self):
        with self.sh(("./10-run-positioning-ue.sh", run.Stopped())):
            with self.assertRaises(run.Stopped):
                run.run(self.scn, self.dir / "out", 30, 1, str(self.status))
        self.assertEqual(json.loads(self.status.read_text())["phase"], "stopped")
        self.assertEqual(self.calls[-1], ["./demo", "down"])
        self.assertFalse(list((self.dir / "out").glob("*.json")))     # no result for a run that was cut

    def test_what_an_interrupted_command_started_is_ended_with_it(self):
        pidfile = self.dir / "pid"
        with self.assertRaises(subprocess.TimeoutExpired):
            run._sh(["bash", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], dict(os.environ), self.dir, subprocess.DEVNULL, timeout=1)
        pid = int(pidfile.read_text())

        def alive() -> bool:
            try:
                return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"      # a zombie has ended
            except OSError:
                return False
        deadline = time.time() + 3
        while alive() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive())


if __name__ == "__main__":
    unittest.main()
