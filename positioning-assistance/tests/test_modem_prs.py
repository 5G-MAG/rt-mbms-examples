import http.server
import json
import threading
import unittest

import numpy as np

from bps import geo
from bps.assistance import AssistanceDocument, Cell, Prs, Rtd
from bps.modem_prs import ModemPrs, ModemRefused
from bps.publisher import Publisher
from bps.ue import AssistanceStore, NetworkClock, Positioner

try:
    import test_option1_chain as chain   # the fixtures of the option 1 chain tests (python3 -m unittest discover -s tests)
except ImportError:
    from tests import test_option1_chain as chain   # (python3 -m unittest tests.test_modem_prs)


class _Modem(http.server.BaseHTTPRequestHandler):
    """A stand-in for the modem's REST interface: PUT /prs_assistance and GET /prs."""
    state: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        st = _Modem.state
        st["puts"].append(body)
        if st.get("refuse"):
            self._send(400, {"ok": False, "error": st["refuse"]})
        else:
            st["assistance"] = body
            self._send(200, {"ok": True})

    def do_GET(self):
        st = _Modem.state
        if self.path == "/modem-api/prs":
            self._send(st.get("status", 200), {"assistance": st.get("assistance"), "stats": {}, "measurements": st["measurements"]})
        else:
            self._send(404, None)


def cell_entry(cell_id, toa_ns, ratio=25.0, present=True):
    return {"cell_id": cell_id, "prs_id": cell_id, "present": present, "toa_ns": toa_ns, "ratio": ratio, "coherence": 0.2}


def measurement(clock_ns, cells, tti=0):
    return {"tti": tti, "sample_clock_ns": clock_ns, "sf_len": 7680, "sample_rate_hz": 7.68e6, "cells": cells}


def small_doc(version=1, muting=None, prs_id=None, bandwidth=25):
    prs = Prs(bandwidth, 2435, 1, prs_id=prs_id, muting=muting)
    ref = Cell(1, 46.0, 6.0, 400.0, prs)
    n1 = Cell(2, 46.05, 6.1, 400.0, Prs(25, 2435, 1), Rtd(1000.0, 5.0, 10**18))
    n2 = Cell(3, 46.1, 6.0, 400.0, Prs(25, 2435, 1), Rtd(-2000.0, 5.0, 10**18))
    return AssistanceDocument(version, 0, 2 * 10**18, ref, [n1, n2])


class ModemPrsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Modem)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/modem-api"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Modem.state = {"puts": [], "measurements": [], "assistance": None}
        self.prs = ModemPrs(self.base)

    # -- what is pushed
    def test_the_json_the_modem_takes(self):
        j = ModemPrs.assistance_json(small_doc(muting="0110", prs_id=300))
        self.assertEqual(j["reference_cell_id"], 1)
        self.assertEqual([c["cell_id"] for c in j["cells"]], [1, 2, 3])
        self.assertEqual(j["cells"][0]["prs"], {"bandwidth_prb": 25, "configuration_index": 2435, "num_dl_frames": 1, "prs_id": 300, "muting": "0110"})
        self.assertIsNone(j["cells"][1]["prs"]["prs_id"])               # the cell identity: left for the modem to resolve
        self.assertIsNone(j["cells"][1]["prs"]["muting"])
        json.dumps(j)                                                    # serialisable as it is

    def test_the_cyclic_prefix_of_each_cell_goes_down(self):
        doc = small_doc()
        doc.neighbours[0].cp_length = "normal"
        doc.reference.cp_length = "extended"
        j = ModemPrs.assistance_json(doc)
        self.assertEqual([c["cp_length"] for c in j["cells"]], ["extended", "normal", None])   # absent: the serving cell's, left for the modem to resolve

    def test_no_position_or_timing_goes_to_the_modem(self):
        text = json.dumps(ModemPrs.assistance_json(small_doc()))
        for word in ("latitude", "longitude", "height", "rtd", "valid"):
            self.assertNotIn(word, text)                                 # only PHY information goes down; location and RTD stay in the positioning code

    def test_push_and_refusal(self):
        self.prs.push(small_doc())
        self.assertEqual(len(_Modem.state["puts"]), 1)
        _Modem.state["refuse"] = "cell 2: only one PRS subframe per occasion (N_PRS 1) is supported"
        with self.assertRaises(ModemRefused) as cm:
            self.prs.push(small_doc())
        self.assertIn("N_PRS", str(cm.exception))

    def test_ensure_pushes_when_needed_only(self):
        doc = small_doc()
        self.assertTrue(self.prs.ensure(doc))
        self.assertFalse(self.prs.ensure(doc))                           # the modem has it
        self.assertEqual(len(_Modem.state["puts"]), 1)
        # a new document that changes only RTD and positions: nothing to push
        doc2 = small_doc(version=2)
        doc2.neighbours[0].rtd = Rtd(1100.0, 5.0, 10**18)
        self.assertFalse(self.prs.ensure(doc2))
        # a change in the PRS information: pushed
        self.assertTrue(self.prs.ensure(small_doc(version=3, muting="01")))
        self.assertEqual(len(_Modem.state["puts"]), 2)
        # the modem restarts and has nothing: pushed again
        _Modem.state["assistance"] = None
        self.assertTrue(self.prs.ensure(small_doc(version=3, muting="01")))
        self.assertEqual(len(_Modem.state["puts"]), 3)

    # -- what is taken back
    def test_rstd_against_the_reference_cell_in_seconds(self):
        # every cell carries the same grid offset (here 7.3 ns); the RSTD is the difference
        _Modem.state["measurements"] = [measurement(5000, [cell_entry(1, 7.3), cell_entry(2, 7.3 + 138800.0), cell_entry(3, 7.3 - 50400.0)])]
        m = self.prs.next(small_doc())
        self.assertEqual(m.sample_clock_ns, 5000)
        self.assertAlmostEqual(m.rstd_s[2], 138800.0e-9, places=15)
        self.assertAlmostEqual(m.rstd_s[3], -50400.0e-9, places=15)
        self.assertNotIn(1, m.rstd_s)
        self.assertAlmostEqual(m.sigma_s, 15e-9)
        self.assertEqual(set(m.sigma_by_cell), {2, 3})

    def test_a_measurement_is_used_once_and_the_newest_first(self):
        _Modem.state["measurements"] = [measurement(1000, [cell_entry(1, 0), cell_entry(2, 10), cell_entry(3, 20)]),
                                        measurement(2000, [cell_entry(1, 0), cell_entry(2, 11), cell_entry(3, 21)])]
        m = self.prs.next(small_doc())
        self.assertEqual(m.sample_clock_ns, 2000)
        self.assertIsNone(self.prs.next(small_doc()))                    # nothing newer
        _Modem.state["measurements"].append(measurement(3000, [cell_entry(1, 0), cell_entry(2, 12), cell_entry(3, 22)]))
        self.assertEqual(self.prs.next(small_doc()).sample_clock_ns, 3000)

    def test_a_cell_that_is_not_detected_or_is_muted_is_not_used(self):
        good = measurement(1000, [cell_entry(1, 0), cell_entry(2, 10), cell_entry(3, 20)])
        weak = measurement(2000, [cell_entry(1, 0), cell_entry(2, 10, ratio=5.1), cell_entry(3, 20)])
        muted = measurement(3000, [cell_entry(1, 0), cell_entry(2, 0, present=False), cell_entry(3, 20)])
        _Modem.state["measurements"] = [good, weak, muted]
        m = self.prs.next(small_doc())
        self.assertEqual(m.sample_clock_ns, 1000)                        # the newer two were passed over, the older good one used
        self.assertIn("muted", self.prs.last_reject)
        _Modem.state["measurements"] = [weak]
        self.prs = ModemPrs(self.base)
        self.assertIsNone(self.prs.next(small_doc()))
        self.assertIn("not detected", self.prs.last_reject)
        self.assertIn("5.1", self.prs.last_reject)

    def test_the_detection_threshold_is_a_parameter(self):
        _Modem.state["measurements"] = [measurement(1000, [cell_entry(1, 0), cell_entry(2, 10, ratio=7.0), cell_entry(3, 20, ratio=7.0)])]
        self.assertIsNone(ModemPrs(self.base).next(small_doc()))
        self.assertIsNotNone(ModemPrs(self.base, ratio_min=6.0).next(small_doc()))

    def test_a_cell_missing_from_the_modems_answer_is_not_used(self):
        _Modem.state["measurements"] = [measurement(1000, [cell_entry(1, 0), cell_entry(2, 10)])]
        self.assertIsNone(self.prs.next(small_doc()))
        self.assertIn("[3]", self.prs.last_reject)

    def test_a_modem_error_is_reported_not_raised(self):
        _Modem.state["status"] = 500
        self.assertIsNone(self.prs.next(small_doc()))
        self.assertIn("500", self.prs.last_reject)

    def test_nothing_without_measurements(self):
        self.assertIsNone(self.prs.next(small_doc()))

    # -- the whole of slide 15, on the fixtures of the option 1 chain
    def test_document_down_measurements_back_position(self):
        net = chain.make_network()
        mon, t_last = chain.monitoring(net, chain.TRUTH)
        sent = []
        pub = Publisher(net, mon, sent.append, validity_s=90)
        body = pub.publish_once(t_last)
        store = AssistanceStore()
        self.assertEqual(store.ingest(body), "accepted")
        clock = NetworkClock()
        clock.update(t_last + 2 * 10**9, 5 * 10**9)
        sample_clock = 45 * 10**9
        t_net = clock.network_time_ns(sample_clock)
        doc = store.current(t_net)
        truth = chain.measure(chain.TRUTH, chain.UE, t_net, sample_clock, noise_ns=0.0)
        rng = np.random.default_rng(4)
        offset = 7.3                                                      # the grid offset every cell's ToA contains
        cells = [cell_entry(1, offset + rng.normal(0, 8))]
        for cid in (2, 3, 4):
            cells.append(cell_entry(cid, offset + truth.rstd_s[cid] * 1e9 + rng.normal(0, 8), ratio=13.0))
        _Modem.state["measurements"] = [measurement(sample_clock, cells)]
        self.assertTrue(self.prs.ensure(doc))                             # the PRS information goes down ...
        pushed = _Modem.state["puts"][-1]
        self.assertEqual([c["cell_id"] for c in pushed["cells"]], [1, 2, 3, 4])
        m = self.prs.next(doc)                                            # ... the measurements come back ...
        self.assertIsNotNone(m)
        fix = Positioner(clock, store, height_m=geo.enu_to_lla(chain.ORIGIN_LLA, chain.UE)[2]).solve(m)   # ... and the RTD and locations from the document give the position
        err = np.linalg.norm(geo.lla_to_enu(chain.ORIGIN_LLA, (fix.latitude_deg, fix.longitude_deg, 1.5))[:2] - chain.UE[:2])
        self.assertLess(err, 30.0)
        self.assertLess(err, 3.0 * fix.horizontal_sigma_m)


if __name__ == "__main__":
    unittest.main()
