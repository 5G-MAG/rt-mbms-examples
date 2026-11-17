"""The 5G Broadcast Positioning Emulator dashboard: a page to set up a scenario, start a run on the real platform, watch it and compare stored results. [design]

    python3 -m bps.dashboard [--port N]

A local web server (standard library only) for the scenario runner (bps.scenario_run). It reads and writes only the scenario files, the results and the run's progress
file under `positioning-assistance/results/scenarios`, starts the runner as a process of its own (so a stopped or restarted server does not end a run), and serves the page in
`dashboard/static`. It binds to the loopback address, answers only requests whose Host header names that address, and takes changes only as JSON requests from the page's own
origin. Nothing the page sends is used as a path: a scenario is data, a result is named from a list the server made.

One run at a time. A run is refused while the MBS demo (open5gs, srsue, mbs-client) is up, because it shares the radio ports and the memory, and while another positioning demo
is up.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import scenario as sc
from . import scenario_report, scenario_run

HERE = Path(__file__).resolve().parents[1]            # positioning-assistance
STATIC = HERE / "dashboard" / "static"
PRESETS = HERE / "scenarios"
RESULTS = HERE / "results" / "scenarios"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
RESULT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
MAX_BODY = 1_000_000
STARTING_GRACE_S = 60          # a run whose runner has not written its first status yet is taken as starting for this long


def process_names() -> set[str]:
    """The names (comm) of the running processes."""
    names = set()
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                names.add((d / "comm").read_text().strip())
            except OSError:
                pass
    return names


def platform_state() -> dict:
    names = process_names()
    return {"mbs_demo_up": any(n.startswith("open5gs-") or n in ("srsue", "mbs-client") for n in names),
            "demo_up": any(n in ("srsenb", "bmsc", "mbms-gw", "srsepc") for n in names)}


def pid_alive(pid) -> bool:
    try:
        return Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, ValueError, TypeError, IndexError):
        return False


def fix_row(rec: dict, t0: float) -> dict:
    return {"t": round(rec["t"] - t0, 1), "err": rec["error_m"], "claimed": rec["claimed_m"], "east": rec["east_err_m"], "north": rec["north_err_m"], "v": rec.get("version"),
            "rstd": {str(c): round(v, 2) for c, v in rec.get("rstd_error_ns", {}).items()}}


class Dashboard:
    """What the server does, apart from HTTP: it is also what the tests call."""

    def __init__(self, results: Path = RESULTS, presets: Path = PRESETS, platform=platform_state, command=None):
        self.results, self.presets, self.platform = Path(results), Path(presets), platform
        self.status_path = self.results / ".run-status.json"
        self.lock = threading.Lock()
        self.command = command or self._runner_command

    def _runner_command(self, scenario_file: Path) -> list[str]:
        return [sys.executable, "-u", "-m", "bps.scenario_run", str(scenario_file), "--out", str(self.results), "--status-file", str(self.status_path)]

    # -- presets
    def presets_list(self) -> dict:
        from . import lab
        out = []
        for p in sorted(self.presets.glob("*.json")):
            s = sc.load(str(p))
            cells, ue, powers = scenario_run.resolve(s)
            rtd = {str(c): list(lab.TRUE_RTD[c]) for c in sc.EMULATED}
            rtd.update({str(k): list(v) for k, v in s.rtd.items()})
            out.append({"name": s.name, "note": s.note, "seconds": s.seconds, "scenario": json.loads(p.read_text()),
                        "resolved": {"cells": [list(map(float, c)) for c in cells], "ue": [float(v) for v in ue], "rtd": rtd, "power": {str(k): v for k, v in powers.items()}}})
        return {"presets": out, "lab": {"cells": lab.CELLS_ENU.tolist(), "ue": lab.UE_ENU.tolist(), "rtd": {str(c): list(v) for c, v in lab.TRUE_RTD.items()}}}

    # -- checking and starting
    def check(self, raw: dict) -> dict:
        """The scenario as the runner would load it, or why it would not."""
        if not isinstance(raw, dict):
            return {"ok": False, "error": "a scenario is an object"}
        try:
            name = raw.get("name", "")
            if not isinstance(name, str) or not NAME_RE.match(name):
                raise ValueError("the name is 1 to 60 letters, digits, dots, dashes or underscores, starting with a letter or digit")
            self.results.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(suffix=".json", dir=self.results, prefix=".check-")
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump(raw, fh)
                s = sc.load(tmp)
            finally:
                os.unlink(tmp)
            _, _, powers = scenario_run.resolve(s)
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "power": {str(k): v for k, v in powers.items()}, "seconds": s.seconds}

    def state(self) -> dict:
        """The run's state: idle, or running with its phase, or how the last run ended."""
        try:
            st = json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return {"state": "idle"}
        phase, now = st.get("phase"), time.time()
        end = now if phase in scenario_run.ACTIVE else st.get("updated", now)         # a run that has ended stops counting
        out = {"name": st.get("name"), "seconds": st.get("seconds"), "phase": phase, "started": st.get("started"), "elapsed": round(end - st.get("started", end), 1),
               "error": st.get("error"), "result": Path(st["result"]).name if st.get("result") else None}
        if phase in scenario_run.ACTIVE:
            alive = pid_alive(st["pid"]) if st.get("pid") else now - st.get("started", 0) < STARTING_GRACE_S
            if alive:
                return {**out, "state": "running"}
            return {**out, "state": "failed", "phase": "failed", "error": "the runner ended without a final status"}
        return {**out, "state": {"done": "finished", "failed": "failed", "stopped": "stopped"}.get(phase, "idle")}

    def start(self, raw: dict) -> tuple[int, dict]:
        checked = self.check(raw)
        if not checked["ok"]:
            return HTTPStatus.BAD_REQUEST, {"error": checked["error"]}
        with self.lock:
            if self.state()["state"] == "running":
                return HTTPStatus.CONFLICT, {"error": "a run is already in progress"}
            plat = self.platform()
            if plat["mbs_demo_up"]:
                return HTTPStatus.CONFLICT, {"error": "the MBS demo is up; it shares the radio ports and the memory. Stop it first."}
            if plat["demo_up"]:
                return HTTPStatus.CONFLICT, {"error": "a positioning demo is already up. Stop it first (./demo down)."}
            folder = self.results / "_scenarios"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{raw['name']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
            path.write_text(json.dumps(raw, indent=1))
            self.status_path.write_text(json.dumps({"pid": None, "name": raw["name"], "seconds": int(raw.get("seconds", sc.Scenario.seconds)), "phase": "starting",
                                                    "started": time.time(), "updated": time.time(), "log": None, "records": None, "result": None, "error": None}))
            log = open(self.results / ".runner.log", "w")
            try:
                proc = subprocess.Popen(self.command(path), cwd=HERE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            finally:
                log.close()
            threading.Thread(target=proc.wait, daemon=True).start()          # reaps the runner when it ends; the run itself does not depend on this server
        return HTTPStatus.OK, {"started": raw["name"]}

    def stop(self) -> tuple[int, dict]:
        st = self.state()
        if st["state"] != "running":
            return HTTPStatus.CONFLICT, {"error": "no run is in progress"}
        try:
            pid = json.loads(self.status_path.read_text()).get("pid")
        except (OSError, ValueError):
            pid = None
        if not pid:
            return HTTPStatus.CONFLICT, {"error": "the run has not started its runner yet; try again in a moment"}
        try:
            os.killpg(int(pid), signal.SIGTERM)       # the runner leads its own group: it brings the demo down and writes `stopped`
        except ProcessLookupError:
            return HTTPStatus.CONFLICT, {"error": "the runner is gone"}
        return HTTPStatus.OK, {"stopping": True}

    # -- what a run produces
    def _status(self) -> dict:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return {}

    def fixes(self, after: int = 0) -> dict:
        """The fixes the UE has recorded so far, from the `after`-th on, with the refusals and documents counted."""
        path = self._status().get("records")
        out = {"fixes": [], "n": 0, "documents": 0, "first_fix_s": None, "startup_refusals": {}, "refusals": {}, "last_refusal": None}
        if not path or not os.path.exists(path):
            return out
        recs = []
        with open(path) as fh:
            for line in fh:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass                             # the last line may be half written
        if not recs:
            return out
        t0 = recs[0]["t"]
        fixes = [r for r in recs if r["kind"] == "fix"]
        out["n"] = len(fixes)
        out["fixes"] = [fix_row(r, t0) for r in fixes[max(after, 0):]]
        out["documents"] = sum(1 for r in recs if r["kind"] == "document" and r.get("verdict") == "accepted")
        out["startup_refusals"], out["refusals"] = scenario_run.split_refusals(recs)
        out["first_fix_s"] = scenario_run.first_fix_seconds(recs)
        reasons = [r["reason"].split(" (")[0] for r in recs if r["kind"] == "no_fix"]
        out["last_refusal"] = reasons[-1] if reasons else None
        return out

    def log_tail(self, lines: int = 200) -> str:
        path = self._status().get("log")
        if not path or not os.path.exists(path):
            return ""
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            data = fh.read().decode("utf-8", "replace")
        return "\n".join(data.splitlines()[-lines:])

    # -- stored results
    def results_list(self) -> list:
        out = []
        for p in sorted(self.results.glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
            if not RESULT_RE.match(p.name):
                continue
            try:
                r = json.loads(p.read_text())
                out.append({"file": p.name, "name": r["name"], "started": r["started"], "seconds": r["ue_seconds"], "summary": r["summary"], "scenario": r["scenario"],
                            "revisions": r.get("revisions", {})})
            except (OSError, ValueError, KeyError):
                continue
        return out

    def result(self, file: str) -> dict | None:
        if not RESULT_RE.match(file) or not (self.results / file).is_file():
            return None
        r = json.loads((self.results / file).read_text())
        t0 = min((x["t"] for x in r["records"]), default=0)
        r["fixes"] = [fix_row(x, t0) for x in r["records"] if x["kind"] == "fix"]
        del r["records"]
        return r

    def report(self, files: list) -> str | None:
        runs = []
        for f in files:
            if not isinstance(f, str) or not RESULT_RE.match(f) or not (self.results / f).is_file():
                return None
            runs.append(json.loads((self.results / f).read_text()))
        return scenario_report.page(runs) if runs else None


class Handler(BaseHTTPRequestHandler):
    app: Dashboard
    port: int = 0
    server_version = "positioning-dashboard"

    def log_message(self, fmt, *args):             # keep the terminal quiet
        pass

    # -- plumbing
    def _hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _allowed(self) -> bool:
        if self.headers.get("Host", "") not in self._hosts():
            self._json(HTTPStatus.FORBIDDEN, {"error": "this server answers only on its own address"})
            return False
        return True

    # -- GET
    def do_GET(self) -> None:
        if not self._allowed():
            return
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)
        try:
            if path == "/":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/presets":
                return self._json(HTTPStatus.OK, self.app.presets_list())
            if path == "/api/state":
                return self._json(HTTPStatus.OK, {"platform": self.app.platform(), "run": self.app.state()})
            if path == "/api/run/fixes":
                return self._json(HTTPStatus.OK, self.app.fixes(int(q.get("after", ["0"])[0])))
            if path == "/api/log":
                return self._json(HTTPStatus.OK, {"log": self.app.log_tail()})
            if path == "/api/results":
                return self._json(HTTPStatus.OK, {"results": self.app.results_list()})
            if path.startswith("/api/results/"):
                r = self.app.result(path[len("/api/results/"):])
                return self._json(HTTPStatus.OK, r) if r else self._json(HTTPStatus.NOT_FOUND, {"error": "no such result"})
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _static(self, rel: str) -> None:
        target = (STATIC / rel).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))

    # -- POST
    def do_POST(self) -> None:
        if not self._allowed():
            return
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] not in self._hosts():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "requests come from the page itself"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON only"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "not JSON"})
        path = urlparse(self.path).path
        if path == "/api/scenario/check":
            return self._json(HTTPStatus.OK, self.app.check(body.get("scenario")))
        if path == "/api/run":
            status, out = self.app.start(body.get("scenario"))
            return self._json(status, out)
        if path == "/api/run/stop":
            status, out = self.app.stop()
            return self._json(status, out)
        if path == "/api/report":
            html = self.app.report(body.get("files") or [])
            return self._send(HTTPStatus.OK, html.encode(), "text/html; charset=utf-8") if html else self._json(HTTPStatus.BAD_REQUEST, {"error": "no such results"})
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def make_server(app: Dashboard, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), type("H", (Handler,), {"app": app}))
    server.RequestHandlerClass.port = server.server_address[1]
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=0, help="default: the first free port from 8765")
    ap.add_argument("--results", default=str(RESULTS), help="the folder of the scenario files, results and the run's progress file")
    ap.add_argument("--host", default="127.0.0.1", help="the loopback address (the page can start runs: do not expose it)")
    a = ap.parse_args()
    ports = [a.port] if a.port else range(8765, 8785)
    server = None
    for p in ports:
        try:
            server = make_server(Dashboard(results=Path(a.results)), a.host, p)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("no free port")
    print(f"5G Broadcast Positioning Emulator: http://{a.host}:{server.server_address[1]}/  (Ctrl-C to stop; a run in progress goes on)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
