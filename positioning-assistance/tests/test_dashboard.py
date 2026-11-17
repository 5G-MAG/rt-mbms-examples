import http.client
import json
import os
import signal
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from bps import dashboard

HERE = Path(__file__).resolve().parents[1]

# stands in for bps.scenario_run: it writes the progress file, the UE's records and a result the way the runner does, without the platform
STUB = textwrap.dedent('''
    import json, os, signal, sys, time
    sys.path.insert(0, %r)
    from bps import scenario as sc, scenario_run as run
    scn, out, status_path, pace = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
    s = sc.load(scn)
    base = os.path.join(out, s.name + "-20260101-000000")
    st = run.Status(status_path, s.name, s.seconds, base + ".log", base + ".records.jsonl")
    def stop(*_):
        st.set("stopped"); sys.exit(143)
    signal.signal(signal.SIGTERM, stop)
    log = open(base + ".log", "w")
    log.write("demo up\\n"); log.flush()
    st.set("warmup"); time.sleep(pace)
    st.set("measuring")
    recs = []
    t0 = time.time()
    with open(base + ".records.jsonl", "w") as fh:
        def put(r):
            r["t"] = time.time(); recs.append(r); fh.write(json.dumps(r) + "\\n"); fh.flush()
        put({"kind": "document", "verdict": "accepted"})
        put({"kind": "no_fix", "reason": "waiting for a PRS measurement (too few cells)"})
        for k in range(3):
            put({"kind": "fix", "error_m": 3.0 + k, "claimed_m": 6.0, "east_err_m": 1.0, "north_err_m": 2.0 + k, "version": 7, "cells": [2, 3, 4], "rstd_error_ns": {"2": 1.0, "3": -1.0, "4": 0.5}})
            log.write("fix %%d\\n" %% k); log.flush(); time.sleep(pace)
    cells, ue, powers = run.resolve(s)
    result = {"scenario": json.load(open(scn)), "name": s.name, "started": "20260101-000000", "ue_seconds": s.seconds, "revisions": {"examples": "stub"}, "cells_enu": cells, "ue_enu": ue,
              "received_power_db": powers, "summary": run.summarise(recs), "records": [r for r in recs if r["kind"] in ("fix", "no_fix")]}
    path = base + ".json"
    open(path, "w").write(json.dumps(result))
    st.set("done", result=path)
''' % str(HERE))


class Base(unittest.TestCase):
    pace = 0.3
    platform = {"mbs_demo_up": False, "demo_up": False}

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.stub = self.dir / "stub.py"
        self.stub.write_text(STUB)
        self.results = self.dir / "results"
        self.results.mkdir()
        self.plat = dict(self.platform)
        app = dashboard.Dashboard(results=self.results, platform=lambda: dict(self.plat),
                                  command=lambda scn: [sys.executable, str(self.stub), str(scn), str(self.results), str(self.results / ".run-status.json"), str(self.pace)])
        self.server = dashboard.make_server(app, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.close)

    def close(self):
        try:
            os.killpg(json.loads((self.results / ".run-status.json").read_text())["pid"], signal.SIGKILL)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.server.shutdown()
        self.server.server_close()

    def req(self, method, path, body=None, headers=None, raw=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"} if method == "POST" else {}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if (body is not None and not raw) else body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        ctype = r.getheader("Content-Type", "")
        return r.status, (json.loads(data) if ctype.startswith("application/json") else data.decode()), ctype

    def wait(self, cond, seconds=15):
        end = time.time() + seconds
        while time.time() < end:
            v = cond()
            if v:
                return v
            time.sleep(0.1)
        self.fail("timed out")


class Serving(Base):
    def test_the_page_and_its_files_are_served_and_nothing_outside_them(self):
        st, body, ctype = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("5G Broadcast Positioning Emulator", body)
        self.assertEqual(self.req("GET", "/static/app.js")[0], 200)
        self.assertIn("javascript", self.req("GET", "/static/app.js")[2])
        for bad in ("/static/../bps/dashboard.py", "/static/%2e%2e/bps/dashboard.py", "/static/images", "/static/nothing.js", "/nothing"):
            self.assertEqual(self.req("GET", bad)[0], 404, bad)

    def test_a_request_not_for_this_address_is_refused(self):
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": f"localhost:{self.port}"})[0], 200)

    def test_changes_are_taken_only_as_json_from_the_page_itself(self):
        self.assertEqual(self.req("POST", "/api/scenario/check", {"scenario": {}}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/scenario/check", {"scenario": {}}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/scenario/check", {"scenario": {}}, headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)
        self.assertEqual(self.req("POST", "/api/scenario/check", "{not json", raw=True)[0], 400)
        self.assertEqual(self.req("POST", "/api/nothing", {})[0], 404)

    def test_the_presets_are_the_scenario_files_resolved(self):
        st, d, _ = self.req("GET", "/api/presets")
        self.assertEqual(st, 200)
        names = [p["name"] for p in d["presets"]]
        self.assertEqual(sorted(names), sorted(p.stem for p in (HERE / "scenarios").glob("*.json")))
        self.assertIn("lab-default", names)
        lab = next(p for p in d["presets"] if p["name"] == "lab-default")["resolved"]
        self.assertEqual((len(lab["cells"]), len(lab["ue"]), sorted(lab["rtd"]), lab["power"]), (4, 3, ["2", "3", "4"], {"2": -6.0, "3": -6.0, "4": -6.0}))
        self.assertAlmostEqual(next(p for p in d["presets"] if p["name"] == "pathloss-n3")["resolved"]["power"]["3"], -15.1, places=1)
        self.assertEqual(len(d["lab"]["cells"]), 4)


class Checking(Base):
    def check(self, scenario):
        return self.req("POST", "/api/scenario/check", {"scenario": scenario})[1]

    def test_a_valid_scenario_gives_its_received_powers(self):
        r = self.check({"name": "x", "path_loss_exponent": 2.0, "seconds": 90})
        self.assertTrue(r["ok"])
        self.assertEqual(r["seconds"], 90)
        self.assertAlmostEqual(r["power"]["3"], -10.07, places=1)

    def test_what_the_runner_would_refuse_is_refused_with_the_reason(self):
        for bad, word in (({"name": "x", "colour": 1}, "colour"), ({"name": "x", "monitor_noise_ns": 0}, "monitor"), ({"name": "x", "cp": "long"}, "cp"),
                          ({"name": "../x"}, "name"), ({"name": ""}, "name"), ({"name": "x" * 61}, "name"), ({}, "name"), ("not an object", "object"),
                          ({"name": "x", "rtd": "no"}, ""), ({"name": "x", "cells_enu": [[0, 0, 0]]}, "four")):
            r = self.check(bad)
            self.assertFalse(r["ok"], bad)
            self.assertIn(word, r["error"], bad)
        self.assertEqual([p for p in self.results.iterdir() if p.name.startswith(".check")], [])


class Running(Base):
    scenario = {"name": "run1", "seconds": 40}

    def start(self, scenario=None):
        return self.req("POST", "/api/run", {"scenario": scenario or self.scenario})

    def run_state(self):
        return self.req("GET", "/api/state")[1]["run"]

    def test_a_run_is_refused_while_the_mbs_demo_or_another_demo_is_up_and_for_a_bad_scenario(self):
        self.plat["mbs_demo_up"] = True
        st, body, _ = self.start()
        self.assertEqual(st, 409)
        self.assertIn("MBS demo", body["error"])
        self.plat.update(mbs_demo_up=False, demo_up=True)
        self.assertEqual(self.start()[0], 409)
        self.plat["demo_up"] = False
        st, body, _ = self.start({"name": "bad name"})
        self.assertEqual(st, 400)
        self.assertEqual(self.run_state(), {"state": "idle"})
        self.assertFalse((self.results / ".run-status.json").exists())

    def test_a_run_goes_from_start_to_a_stored_result_and_its_fixes_arrive_as_they_are_made(self):
        self.pace = 0.5
        self.assertEqual(self.start()[0], 200)
        self.assertEqual(self.run_state()["state"], "running")
        self.assertEqual(self.start()[0], 409)                                                    # one run at a time
        first = self.wait(lambda: (lambda d: d if d["n"] >= 1 else None)(self.req("GET", "/api/run/fixes?after=0")[1]))
        self.assertEqual(first["documents"], 1)
        self.assertEqual(first["refusals"], {"waiting for a PRS measurement": 1})
        row = first["fixes"][0]
        self.assertEqual((row["err"], row["east"], row["north"], row["rstd"]), (3.0, 1.0, 2.0, {"2": 1.0, "3": -1.0, "4": 0.5}))
        self.assertGreaterEqual(row["t"], 0)
        state = self.wait(lambda: (lambda s: s if s["state"] != "running" else None)(self.run_state()))
        self.assertEqual((state["state"], state["phase"], state["name"]), ("finished", "done", "run1"))
        later = self.req("GET", f"/api/run/fixes?after={first['n']}")[1]
        self.assertEqual((later["n"], len(later["fixes"])), (3, 3 - first["n"]))                  # only what is new
        self.assertIn("fix 2", self.req("GET", "/api/log")[1]["log"])
        listing = self.req("GET", "/api/results")[1]["results"]
        self.assertEqual([r["name"] for r in listing], ["run1"])
        self.assertEqual(listing[0]["summary"]["fixes"], 3)
        full = self.req("GET", "/api/results/" + listing[0]["file"])[1]
        self.assertEqual((len(full["fixes"]), "records" in full), (3, False))
        st, html, ctype = self.req("POST", "/api/report", {"files": [listing[0]["file"]]})
        self.assertEqual((st, ctype.split(";")[0]), (200, "text/html"))
        self.assertIn("run1", html)
        self.assertEqual(self.start({"name": "run2", "seconds": 40})[0], 200)                    # a finished run does not block the next

    def test_a_run_can_be_stopped(self):
        self.pace = 30
        self.assertEqual(self.start()[0], 200)
        self.wait(lambda: self.run_state().get("phase") == "measuring" or self.run_state().get("phase") == "warmup")
        time.sleep(0.3)
        self.assertEqual(self.req("POST", "/api/run/stop", {})[0], 200)
        state = self.wait(lambda: (lambda s: s if s["state"] != "running" else None)(self.run_state()))
        self.assertEqual(state["state"], "stopped")
        self.assertEqual(self.req("POST", "/api/run/stop", {})[0], 409)                           # nothing to stop

    def test_a_runner_that_is_gone_is_a_failed_run_not_a_running_one(self):
        (self.results / ".run-status.json").write_text(json.dumps({"pid": 2 ** 22 + 12345, "name": "gone", "seconds": 40, "phase": "measuring", "started": time.time(), "updated": time.time(),
                                                                   "log": None, "records": None, "result": None, "error": None}))
        s = self.run_state()
        self.assertEqual((s["state"], s["phase"]), ("failed", "failed"))
        self.assertIn("without a final status", s["error"])
        self.assertEqual(self.start()[0], 200)                                                    # and it does not block a new run

    def test_results_are_named_only_from_the_list(self):
        (self.results / "good-1.json").write_text("{}")
        for bad in ("../secret.json", "..%2fsecret.json", ".hidden.json", "good-1.json%00", "nothing.json", "a/b.json"):
            self.assertEqual(self.req("GET", "/api/results/" + bad)[0], 404, bad)
        self.assertEqual(self.req("POST", "/api/report", {"files": ["../x.json"]})[0], 400)
        self.assertEqual(self.req("POST", "/api/report", {"files": []})[0], 400)


if __name__ == "__main__":
    unittest.main()
