"""Option 1 end to end in software: monitor -> publisher -> document -> UE store/clock -> position. No radio."""
import http.server
import json
import threading
import unittest

import numpy as np

from bps import geo
from bps.assistance import AssistanceDocument, Prs
from bps.publisher import CellSite, Network, Publisher
from bps.rtd_monitor import Epoch, InsufficientData, RtdMonitor, geo_distance_m
from bps.sim import CELLS_ENU, ORIGIN_LLA
from bps.timebase import C_MPS, TS_S
from bps.ue import AssistanceStore, Measurement, NetworkClock, Positioner, StaleDocument
from bps.xmb import PortalClient, SessionSpec, XmbError, XmbUClient

T0 = 10**18  # network time of the first monitoring epoch, GPS ns (synthetic)


def make_network():
    sites = []
    for i, p in enumerate(CELLS_ENU):
        lat, lon, h = geo.enu_to_lla(ORIGIN_LLA, p)
        sites.append(CellSite(i + 1, lat, lon, h, Prs(25, 2400 + i, 1)))
    return Network(sites[0], tuple(sites[1:]))


def true_rtd_ns(truth, cell_index, t_ns):
    """True RTD of cell `cell_index` (1..3) at network time t_ns: value at T0 plus drift."""
    base, drift = truth[cell_index]
    return base + drift * (t_ns - T0) / 1e9


def monitoring(net, truth, n_epochs=60, spacing_s=10, noise_ns=20.0, seed=3):
    rng = np.random.default_rng(seed)
    lla = net.lla()
    monitor_lla = geo.enu_to_lla(ORIGIN_LLA, [1000.0, 500.0, 10.0])
    mon = RtdMonitor(monitor_lla, lla, reference_id=1)
    ids = sorted(lla)
    for k in range(n_epochs):
        t = T0 + k * spacing_s * 10**9
        arr = {}
        for idx, cid in enumerate(ids):
            d_ns = geo_distance_m(monitor_lla, lla[cid]) / C_MPS * 1e9
            off = 0.0 if idx == 0 else true_rtd_ns(truth, idx, t)
            arr[cid] = t + int(round(d_ns + off + rng.normal(0, noise_ns)))  # integer arithmetic: float64 cannot hold ns at 1e18
        mon.add(Epoch(t, arr))
    return mon, T0 + (n_epochs - 1) * spacing_s * 10**9


def measure(truth, ue_enu, t_net_ns, sample_clock_ns, noise_ns=30.0, seed=5):
    rng = np.random.default_rng(seed)
    d = np.linalg.norm(CELLS_ENU - ue_enu, axis=1)
    rstd = {}
    for idx in (1, 2, 3):
        wrapped = (true_rtd_ns(truth, idx, t_net_ns) + 500_000) % 1_000_000 - 500_000
        rstd[idx + 1] = (d[idx] - d[0]) / C_MPS + wrapped * 1e-9 + rng.normal(0, noise_ns) * 1e-9
    return Measurement(sample_clock_ns, rstd, sigma_s=noise_ns * 1e-9)


TRUTH = {1: (123_456.0, 3.0), 2: (-80_000.0, -2.0), 3: (40_000.0, 1.5)}  # RTD ns at T0, drift ns/s
UE = np.array([2500.0, -3200.0, 1.5])


class Chain(unittest.TestCase):
    def setUp(self):
        self.net = make_network()
        self.mon, self.t_last = monitoring(self.net, TRUTH)
        self.sent = []
        self.pub = Publisher(self.net, self.mon, self.sent.append, validity_s=90)

    def ue(self, doc_bytes, anchor_label, anchor_clock):
        store = AssistanceStore()
        self.assertEqual(store.ingest(doc_bytes), "accepted")
        clock = NetworkClock()
        clock.update(anchor_label, anchor_clock)
        return clock, store

    def test_fix_with_drift_applied_at_the_measurement_time(self):
        body = self.pub.publish_once(self.t_last)
        self.assertEqual(self.sent, [body])
        # The SIB16 anchor says the boundary at sample clock 5 s is network time t_last + 2 s; the UE measures 40 s later.
        clock, store = self.ue(body, self.t_last + 2 * 10**9, 5 * 10**9)
        sample_clock = 45 * 10**9
        t_net = clock.network_time_ns(sample_clock)
        self.assertEqual(t_net, self.t_last + 42 * 10**9)
        fix = Positioner(clock, store, height_m=geo.enu_to_lla(ORIGIN_LLA, UE)[2]).solve(measure(TRUTH, UE, t_net, sample_clock))
        err = np.linalg.norm(geo.lla_to_enu(ORIGIN_LLA, (fix.latitude_deg, fix.longitude_deg, 1.5))[:2] - UE[:2])
        self.assertLess(err, 45.0)
        # the UE's own time is the network time plus propagation to the reference cell
        d_ref = np.linalg.norm(UE - CELLS_ENU[0])
        self.assertAlmostEqual((fix.time_gps_ns - t_net) / 1e9, d_ref / C_MPS, delta=2e-6)

    def test_per_cell_sigma_down_weights_a_bad_rstd(self):
        body = self.pub.publish_once(self.t_last)
        clock, store = self.ue(body, self.t_last + 2 * 10**9, 5 * 10**9)
        sample_clock = 45 * 10**9
        t_net = clock.network_time_ns(sample_clock)
        good = measure(TRUTH, UE, t_net, sample_clock, noise_ns=1.0)
        bad = dict(good.rstd_s)
        bad[2] += 2e-6   # 600 m of range difference on one cell
        h = geo.enu_to_lla(ORIGIN_LLA, UE)[2]

        def error(m):
            fix = Positioner(clock, store, height_m=h).solve(m)
            return float(np.linalg.norm(geo.lla_to_enu(ORIGIN_LLA, (fix.latitude_deg, fix.longitude_deg, 1.5))[:2] - UE[:2]))

        same_weight = error(Measurement(sample_clock, bad, sigma_s=30e-9))
        weighted = error(Measurement(sample_clock, bad, sigma_s=30e-9, sigma_by_cell={2: 1e-4}))
        self.assertGreater(same_weight, 100.0)
        self.assertLess(weighted, 25.0)
        # a cell missing from the map keeps sigma_s
        self.assertEqual(error(Measurement(sample_clock, good.rstd_s, sigma_s=30e-9, sigma_by_cell={})),
                         error(Measurement(sample_clock, good.rstd_s, sigma_s=30e-9)))

    def test_claimed_uncertainty_is_calibrated(self):
        errs, sigs = [], []
        for seed in range(30):
            mon, t_last = monitoring(self.net, TRUTH, seed=seed + 100)
            body = Publisher(self.net, mon, lambda b: None, validity_s=90).publish_once(t_last)
            clock, store = self.ue(body, t_last + 2 * 10**9, 5 * 10**9)
            sc = 45 * 10**9
            fix = Positioner(clock, store, height_m=geo.enu_to_lla(ORIGIN_LLA, UE)[2]).solve(
                measure(TRUTH, UE, clock.network_time_ns(sc), sc, seed=seed))
            errs.append(np.linalg.norm(geo.lla_to_enu(ORIGIN_LLA, (fix.latitude_deg, fix.longitude_deg, 1.5))[:2] - UE[:2]))
            sigs.append(fix.horizontal_sigma_m)
        rmse, claimed = float(np.sqrt(np.mean(np.square(errs)))), float(np.mean(sigs))
        self.assertLess(rmse, 1.5 * claimed)       # not optimistic
        self.assertGreater(rmse, 0.5 * claimed)    # not wildly pessimistic

    def test_drift_in_the_document_matters(self):
        body = self.pub.publish_once(self.t_last)
        doc = json.loads(body)
        for c in doc["neighbours"]:
            c["rtd_info"]["fineRTDdriftRate"] = 0
            c["rtd_info"]["x_drift_std_ns_per_s"] = 0.0
        zeroed = json.dumps(doc).encode()
        errs = []
        for raw in (body, zeroed):
            clock, store = self.ue(raw, self.t_last, 0)
            sc = 80 * 10**9  # 80 s after the document was made: drift of 3 ns/s is 240 ns, about 70 m
            fix = Positioner(clock, store, height_m=geo.enu_to_lla(ORIGIN_LLA, UE)[2]).solve(
                measure(TRUTH, UE, clock.network_time_ns(sc), sc))
            errs.append(np.linalg.norm(geo.lla_to_enu(ORIGIN_LLA, (fix.latitude_deg, fix.longitude_deg, 1.5))[:2] - UE[:2]))
        self.assertLess(errs[0], 45.0)
        self.assertGreater(errs[1], 2 * errs[0])

    def test_rtd_near_the_wrap_point(self):
        truth = {1: (499_900.0, 2.0), 2: (-499_900.0, -2.0), 3: (0.0, 0.0)}  # cells 2 and 3 cross +-0.5 ms as time passes
        mon, t_last = monitoring(self.net, truth, seed=9)
        pub = Publisher(self.net, mon, lambda b: None, validity_s=300)
        body = pub.publish_once(t_last)
        clock, store = self.ue(body, t_last, 0)
        sc = 120 * 10**9  # the drift has carried the first cell across +0.5 ms
        fix = Positioner(clock, store, height_m=geo.enu_to_lla(ORIGIN_LLA, UE)[2]).solve(measure(truth, UE, t_last + sc, sc))
        err = np.linalg.norm(geo.lla_to_enu(ORIGIN_LLA, (fix.latitude_deg, fix.longitude_deg, 1.5))[:2] - UE[:2])
        self.assertLess(err, 60.0)

    def test_validity_window_is_enforced(self):
        body = self.pub.publish_once(self.t_last)
        clock, store = self.ue(body, self.t_last, 0)
        pos = Positioner(clock, store, height_m=1.5)
        with self.assertRaises(StaleDocument):
            sc = 91 * 10**9
            pos.solve(measure(TRUTH, UE, self.t_last + sc, sc))     # after valid_until (90 s)
        with self.assertRaises(StaleDocument):
            sc = -31 * 10**9
            pos.solve(measure(TRUTH, UE, self.t_last + sc, sc))     # before valid_from (30 s backdating)

    def test_no_anchor_no_fix(self):
        store = AssistanceStore()
        store.ingest(self.pub.publish_once(self.t_last))
        with self.assertRaises(StaleDocument):
            Positioner(NetworkClock(), store).solve(Measurement(0, {2: 0, 3: 0, 4: 0}, 1e-8))

    def test_store_keeps_the_newest_valid_document(self):
        store = AssistanceStore()
        first = self.pub.publish_once(self.t_last)
        second = self.pub.publish_once(self.t_last + 10**9)
        self.assertEqual(store.ingest(second), "accepted")
        self.assertTrue(store.ingest(first).startswith("ignored"))
        self.assertTrue(store.ingest(second).startswith("ignored"))        # same version again
        self.assertTrue(store.ingest(b"{not json").startswith("rejected"))
        broken = json.loads(second)
        broken["version"] += 5
        broken["neighbours"][0]["prs"]["configuration_index"] = 3000          # reserved index
        self.assertTrue(store.ingest(json.dumps(broken).encode()).startswith("rejected"))
        self.assertEqual(store.current(self.t_last + 2 * 10**9).version, json.loads(second)["version"])

    def test_versions_increase_even_within_one_second(self):
        a = json.loads(self.pub.publish_once(self.t_last))["version"]
        b = json.loads(self.pub.publish_once(self.t_last + 10))["version"]
        self.assertGreater(b, a)

    def test_validity_starts_before_the_document_was_made(self):
        body = self.pub.publish_once(self.t_last)
        clock, store = self.ue(body, self.t_last, 0)
        sc = -20 * 10**9  # a UE whose anchor is a few seconds behind the document
        Positioner(clock, store, height_m=geo.enu_to_lla(ORIGIN_LLA, UE)[2]).solve(measure(TRUTH, UE, self.t_last + sc, sc))

    def test_nothing_is_sent_without_monitoring_data(self):
        mon = RtdMonitor(geo.enu_to_lla(ORIGIN_LLA, [0, 0, 0]), self.net.lla(), 1)
        sent = []
        self.assertIsNone(Publisher(self.net, mon, sent.append).publish_once(T0))
        self.assertEqual(sent, [])


class Monitor(unittest.TestCase):
    def test_estimates_track_truth(self):
        net = make_network()
        mon, t_last = monitoring(net, TRUTH, noise_ns=20.0)
        est = mon.estimate()
        for idx in (1, 2, 3):
            r = est[idx + 1]
            self.assertAlmostEqual(r.rtd_ns, true_rtd_ns(TRUTH, idx, t_last), delta=25.0)
            self.assertAlmostEqual(r.drift_ns_per_s, TRUTH[idx][1], delta=0.15)
            self.assertEqual(r.valid_at_gps_ns, t_last)
            self.assertGreaterEqual(r.std_ns, 5.0)  # the floor

    def test_few_epochs_do_not_understate_the_uncertainty(self):
        net = make_network()
        lla = net.lla()
        mon_lla = geo.enu_to_lla(ORIGIN_LLA, [1000.0, 500.0, 10.0])
        worst_ratio = []
        for seed in range(40):
            rng = np.random.default_rng(seed)
            mon = RtdMonitor(mon_lla, lla, 1, min_epochs=5, min_std_ns=0.0, timestamp_sigma_ns=20.0)
            for k in range(5):
                t = T0 + k * 5 * 10**9
                arr = {cid: t + int(round(geo_distance_m(mon_lla, p) / C_MPS * 1e9 + (0 if cid == 1 else true_rtd_ns(TRUTH, cid - 1, t))
                                          + rng.normal(0, 20.0))) for cid, p in lla.items()}
                mon.add(Epoch(t, arr))
            est = mon.estimate()
            t_last = T0 + 4 * 5 * 10**9
            for cid in (2, 3, 4):
                worst_ratio.append(abs(est[cid].rtd_ns - true_rtd_ns(TRUTH, cid - 1, t_last)) / est[cid].std_ns)
        self.assertLess(float(np.mean(np.array(worst_ratio) > 2.5)), 0.05)  # about 1 % beyond 2.5 sigma is normal

    def test_nanosecond_timestamps_at_gps_scale_lose_nothing(self):
        # GPS time in ns is about 1.5e18, beyond float64's 128 ns resolution; a noise-free monitor must still recover the RTD to the ns.
        net = make_network()
        mon, t_last = monitoring(net, TRUTH, noise_ns=0.0)
        est = mon.estimate()
        for idx in (1, 2, 3):
            self.assertAlmostEqual(est[idx + 1].rtd_ns, true_rtd_ns(TRUTH, idx, t_last), delta=1.0)
            self.assertAlmostEqual(est[idx + 1].drift_ns_per_s, TRUTH[idx][1], delta=0.01)

    def test_input_checks(self):
        net = make_network()
        mon, _ = monitoring(net, TRUTH, n_epochs=3)
        with self.assertRaises(InsufficientData):
            mon.estimate()
        with self.assertRaises(ValueError):
            mon.add(Epoch(T0, {1: 0, 2: 0}))                 # not later than the last epoch
        with self.assertRaises(ValueError):
            mon.add(Epoch(T0 + 10**12, {2: 0, 3: 0}))        # no reference cell
        with self.assertRaises(ValueError):
            RtdMonitor((0, 0, 0), {2: (0, 0, 0)}, reference_id=1)


class _Fake(http.server.BaseHTTPRequestHandler):
    log = []
    push_status = 204

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, code, obj=None, headers=()):
        raw = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self._body())
        _Fake.log.append(("POST", self.path, body, self.headers.get("Authorization")))
        if self.path == "/api/xmb/services":
            self._send(201, {"ok": True, "id": "svc-9"})
        else:
            self._send(201, None, headers=[("Location", "/api/xmb/services/svc-9/sessions/sess-9")])

    def do_GET(self):
        _Fake.log.append(("GET", self.path, None, None))
        self._send(200, {"ok": True, "id": "sess-9", "state": "Idle", "files": {"ingestMode": "Push", "fileList": [], "pushURL": "", "carouselMode": "none"}})

    def do_PUT(self):
        body = self._body()
        _Fake.log.append(("PUT", self.path, body if self.path.startswith("/xmb-u") else json.loads(body), self.headers.get("Content-Type")))
        if self.path.startswith("/xmb-u"):
            self.send_response(_Fake.push_status)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(200, {"ok": True})

    def do_DELETE(self):
        _Fake.log.append(("DELETE", self.path, None, None))
        self._send(204)

    def log_message(self, *a):
        pass


class Xmb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _Fake.log.clear()
        _Fake.push_status = 204

    def test_provisioning_sequence(self):
        spec = SessionSpec("PNT assistance", tmgi_service_id=22, tsi=5, mcast_addr="239.255.0.2", port=2155, c_teid=0xCCCD,
                           display_base_url="positioning")
        portal = PortalClient(self.url, "admin", "secret")
        svc, sess = portal.create_push_session(spec)
        self.assertEqual((svc, sess), ("svc-9", "sess-9"))
        portal.set_state(svc, sess, "Active")
        methods = [(m, p) for m, p, _, _ in _Fake.log]
        self.assertEqual(methods, [("POST", "/api/xmb/services"), ("POST", "/api/xmb/services/svc-9/sessions"),
                                   ("GET", "/api/xmb/services/svc-9/sessions/sess-9"),
                                   ("PUT", "/api/xmb/services/svc-9/sessions/sess-9")])
        session_body = _Fake.log[1][2]
        self.assertEqual(session_body["sessionType"], "Files")
        self.assertEqual(session_body["files"], {"ingestMode": "Push", "carouselMode": "none", "displayBaseURL": "positioning"})
        self.assertEqual(session_body["mbmsBearer"]["cTeid"], 0xCCCD)
        self.assertTrue(_Fake.log[0][3].startswith("Basic "))
        activate = _Fake.log[3][2]
        self.assertEqual(activate["state"], "Active")
        self.assertNotIn("pushURL", activate["files"])
        self.assertNotIn("fileList", activate["files"])
        self.assertNotIn("ok", activate)

    def test_push(self):
        XmbUClient(self.url).push("sess-9", "positioning/assistance.json", b'{"a":1}')
        method, path, body, ctype = _Fake.log[-1]
        self.assertEqual((method, path, body, ctype), ("PUT", "/xmb-u/push/sess-9/positioning/assistance.json", b'{"a":1}', "application/json"))

    def test_push_failure_raises(self):
        _Fake.push_status = 400
        with self.assertRaises(XmbError):
            XmbUClient(self.url).push("sess-9", "x.json", b"{}")


if __name__ == "__main__":
    unittest.main()
