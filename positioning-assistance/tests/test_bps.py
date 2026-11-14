import json
import math
import unittest

import numpy as np

from bps import geo
from bps import live
from bps.assistance import AssistanceDocument, Cell, Prs, Rtd, expected_rstd_field, expected_rstd_from_field, fill_expected_rstd
from bps.sim import CELLS_ENU, ORIGIN_LLA, PRS_INDEX, build_document, quantise_rstd_ts
from bps.solver import solve_rstd, ue_time_ns
from bps.timebase import (C_MPS, SUBFRAME_NS, TS_S, TimeAnchor, _PRS_TABLE, gps_ns_to_sib16_r15_fields, is_prs_occasion_start,
                          next_prs_occasion_start, prs_period_offset, sib16_r11_to_gps_ns,
                          sib16_r15_time_to_ns)


def load(text):
    """The envelope of a document as sent, and its IE view (what unwrap gives: schema, version, validity, reference, neighbours; the IE dicts are the ones inside the envelope)."""
    env = json.loads(text)
    return env, AssistanceDocument.unwrap(env)


class LppeCoding(unittest.TestCase):
    """The coding of the positions and of the RTD: OMA-TS-LPPe V2.0 (2020-08-04) rtdInfo and RelativeLocation, TS 23.032 V19.0.0 clauses 6.1 and 6.3."""

    def test_point_by_the_equations_of_23_032(self):
        from bps.assistance import encode_point, decode_point
        p = encode_point(46.2, 6.14, 429.7)
        self.assertEqual(p["degreesLatitude"], math.floor(46.2 * 2**23 / 90))      # N <= 2^23/90 X < N+1
        self.assertEqual(p["degreesLongitude"], math.floor(6.14 * 2**24 / 360))     # N <= 2^24/360 X < N+1
        self.assertEqual((p["latitudeSign"], p["altitudeDirection"], p["altitude"]), ("north", "height", 429))   # 1 m increments
        s = encode_point(-33.9, -151.2, -12.4)
        self.assertEqual((s["latitudeSign"], s["altitudeDirection"], s["altitude"]), ("south", "depth", 12))
        self.assertLess(s["degreesLongitude"], 0)                                   # two's complement, west is negative
        lat, lon, h = decode_point(s)
        self.assertAlmostEqual(lat, -33.9, delta=90 / 2**23)
        self.assertAlmostEqual(lon, -151.2, delta=360 / 2**24)
        self.assertAlmostEqual(h, -12.5, delta=1.0)
        for field, lim in (("degreesLatitude", 2**23 - 1), ("altitude", 2**15 - 1)):
            self.assertLessEqual(encode_point(90.0, 180.0, 1e9)[field], lim)
        with self.assertRaises(ValueError):
            encode_point(91.0, 0.0, 0.0)

    def test_relative_location_units_and_range(self):
        from bps.assistance import encode_relative, decode_relative
        ref = (46.0, 6.0, 400.0)
        near = geo.enu_to_lla(ref, (1500.0, -2300.0, 0.0))
        r = encode_relative(ref, (near[0], near[1], 412.34))
        self.assertEqual(r["units"], "cm")                          # the smallest unit that holds the numbers
        self.assertAlmostEqual(r["relativeNorth"] * 0.01, -2300.0, delta=0.02)
        self.assertAlmostEqual(r["relativeEast"] * 0.01, 1500.0, delta=0.02)
        self.assertEqual(r["relativeAltitude"]["geodeticRelativeAltitude"]["geodetic-height-depth"], 1234)
        far = geo.enu_to_lla(ref, (9000.0, 19000.0, 0.0))           # 19 km north: past 524287 cm, so decimetres
        r = encode_relative(ref, (far[0], far[1], 400.0))
        self.assertEqual(r["units"], "dm")
        lat, lon, h = decode_relative(ref, r)
        self.assertLess(math.hypot(*geo.lla_to_enu((far[0], far[1], 400.0), (lat, lon, h))[:2]), 0.2)
        with self.assertRaises(ValueError):                         # beyond the straight-line range the clause allows
            encode_relative(ref, geo.enu_to_lla(ref, (0.0, 21000.0, 0.0)))
        with self.assertRaises(ValueError):
            encode_relative(ref, geo.enu_to_lla(ref, (11000.0, 0.0, 0.0)))
        m = encode_relative(ref, (near[0], near[1], 400.0 + 4000.0))   # 4 km up: past 327 m in cm, 3.3 km in dm, so metres (no units field)
        self.assertNotIn("units", m)

    def test_rtd_info(self):
        from bps.assistance import Rtd, encode_rtd, decode_rtd
        r = encode_rtd(Rtd(-123_456.0, 5.0, 10**18, drift_ns_per_s=-2.4, drift_std_ns_per_s=0.2))
        self.assertEqual(r["fineRTD"], 87654)                            # (-123456 mod 1 ms) in units of 10 ns: 876544 ns rounded to 87654
        self.assertEqual(r["fineRTDdriftRate"], -2)
        self.assertEqual(r["fineRTDstd"], {"resolution": "ns-5", "quality": 1})
        back = decode_rtd(r)
        self.assertAlmostEqual(back.rtd_ns, -123_460.0, delta=1e-6)       # folded back to the closest subframe
        self.assertEqual(back.drift_ns_per_s, -2.0)
        self.assertEqual(back.valid_at_gps_ns, 10**18)
        self.assertNotIn("subframeOffset", r)                             # optional, not known here
        self.assertEqual(encode_rtd(Rtd(0.0, 300.0, 0))["fineRTDstd"], {"resolution": "ns-5", "quality": 60})
        self.assertEqual(encode_rtd(Rtd(0.0, 900.0, 0))["fineRTDstd"], {"resolution": "ns-10", "quality": 90})
        self.assertEqual(encode_rtd(Rtd(0.0, 9000.0, 0))["fineRTDstd"], {"resolution": "ns-100", "quality": 90})

    def test_rtd_edges_refused_or_wrapped(self):
        from bps.assistance import Rtd, encode_rtd, decode_rtd
        self.assertEqual(encode_rtd(Rtd(-4.0, 5.0, 0))["fineRTD"], 0)            # -4 ns rounds to the 0 step, not to 100000 (out of range)
        self.assertEqual(encode_rtd(Rtd(-5.1, 5.0, 0))["fineRTD"], 99999)
        self.assertEqual(decode_rtd(encode_rtd(Rtd(499_990.0, 5.0, 0))).rtd_ns, 499_990.0)
        self.assertEqual(decode_rtd(encode_rtd(Rtd(-500_000.0, 5.0, 0))).rtd_ns, -500_000.0)
        with self.assertRaises(ValueError):
            encode_rtd(Rtd(0.0, 13000.0, 0))                               # beyond 127 x 100 ns
        with self.assertRaises(ValueError):
            encode_rtd(Rtd(0.0, 5.0, 0, drift_ns_per_s=101.0))
        self.assertEqual(encode_rtd(Rtd(0.0, 5.0, 0, drift_ns_per_s=100.4))["fineRTDdriftRate"], 100)

    def test_the_wire_nests_the_lppe_ies(self):
        import json
        d = CellCyclicPrefix().doc()
        d.reference_sfn = 5                      # with the SFN the drift rate may be stated; without it it is not (see the publisher test)
        env, raw = load(d.to_json())
        self.assertEqual(raw["schema"], "bps-assistance/3-draft")
        ref = raw["reference"]
        self.assertEqual(set(ref), {"referenceCellInfo", "positionCalculationInfoRef"})         # OMA-LPPe-OTDOA-ReferenceCellInfo
        self.assertEqual(set(ref["referenceCellInfo"]), {"physCellId", "cpLength", "prsInfo"})
        self.assertEqual(ref["referenceCellInfo"]["prsInfo"], {"prs-Bandwidth": "n25", "prs-ConfigurationIndex": 2435, "numDL-Frames": "sf-1"})
        loc = ref["positionCalculationInfoRef"]["cellLocation"]["reference-point"]["referencePointGeographicLocation"]["location3D"]
        self.assertEqual(set(loc), {"latitudeSign", "degreesLatitude", "degreesLongitude", "altitudeDirection", "altitude"})
        n = raw["neighbours"][0]                                                                   # OMA-LPPe-OTDOA-NeighbourCellInfoElement-eNB
        self.assertEqual(set(n), {"relative-Location", "otdoa-eNB-CellDataList"})
        self.assertIn("relativeNorth", n["relative-Location"])
        cd = n["otdoa-eNB-CellDataList"][0]                                                        # OMA-LPPe-OTDOA-CellData
        self.assertEqual(set(cd), {"otdoa-NeighbourCellInfoElement", "rtdInfo"})
        self.assertEqual(set(cd["rtdInfo"]) - {"x_drift_std_ns_per_s", "x_valid_at_gps_ns"}, {"fineRTD", "fineRTDstd", "fineRTDdriftRate"})
        self.assertEqual(set(cd["otdoa-NeighbourCellInfoElement"]), {"physCellId", "expectedRSTD", "expectedRSTD-Uncertainty"})   # same prsInfo and cpLength: left out
        for old in ("latitude_deg", "rtd_ns", "std_ns", "height_m", "cp_length", "phys_cell_id"):
            self.assertNotIn('"%s"' % old, json.dumps(env))

    def test_the_file_is_the_envelope_of_the_deck(self):
        # slide 14, "Sample JSON file containing positioning assistance data"
        d = CellCyclicPrefix().doc()
        d.reference.ecgi = ("901", "56", 0x19B01)
        d.broadcast_area = "Area1"
        d.repeat_interval_s = 5.0
        d.created_gps_ns = 1_478_698_084 * 10**9 + 250_000_000
        d.valid_from_gps_ns, d.valid_until_gps_ns = 1_478_698_054 * 10**9, 1_478_698_174 * 10**9
        env = json.loads(d.to_json())
        m = env["MBMSDelivery"]
        self.assertEqual((m["serviceId"], m["serviceName"], m["deliveryMethod"], m["broadcastArea"]), ("PositioningService", "MBMS Assistance Data Delivery", "broadcast", "Area1"))
        self.assertEqual(m["fileSchedule"], {"startTime": "2026-11-14T13:27:16.000Z", "endTime": "2026-11-14T13:29:16.000Z", "repeatInterval": "PT5S"})
        self.assertEqual([t["textId"] for t in m["textData"]], ["eNBRefInfo", "eNBNeighbourInfo2"])
        ref = m["textData"][0]
        self.assertEqual(ref["title"], "Reference eNB information")
        self.assertEqual([(c["contentId"], c["content"]) for c in ref["contents"][:2]], [("PHY Reference Cell ID", "1"), ("Global Reference Cell ID", "901-56-105217")])
        self.assertTrue(all(c["author"] == "5G-MAG" for t in m["textData"] for c in t["contents"]))
        self.assertEqual(set(m["metadata"]) - {"x_valid_from_gps_ns", "x_valid_until_gps_ns", "x_leap_seconds"}, {"description", "version", "createdBy", "createdDate"})
        self.assertEqual(m["metadata"]["createdDate"], "2026-11-14T13:27:46.250Z")
        back = AssistanceDocument.from_json(d.to_json())
        self.assertEqual((back.valid_from_gps_ns, back.valid_until_gps_ns, back.broadcast_area, back.repeat_interval_s, back.created_gps_ns, back.service_id),
                         (d.valid_from_gps_ns, d.valid_until_gps_ns, "Area1", 5.0, d.created_gps_ns, "PositioningService"))
        for bad in ("narrowcast",):
            env2 = json.loads(d.to_json())
            env2["MBMSDelivery"]["deliveryMethod"] = bad
            with self.assertRaises(ValueError):
                AssistanceDocument.from_json(json.dumps(env2))
        env3 = json.loads(d.to_json())
        env3["MBMSDelivery"]["fileSchedule"]["repeatInterval"] = "5 seconds"
        with self.assertRaises(ValueError):
            AssistanceDocument.from_json(json.dumps(env3))
        env4 = json.loads(d.to_json())
        env4["MBMSDelivery"]["textData"] = env4["MBMSDelivery"]["textData"][1:]
        with self.assertRaises(ValueError):                                       # no reference cell
            AssistanceDocument.from_json(json.dumps(env4))

    def test_prs_info_as_the_asn1_names_it(self):
        from bps.assistance import Prs, encode_prs_info, decode_prs_info
        for prs, want in ((Prs(25, 2435, 1), {"prs-Bandwidth": "n25", "prs-ConfigurationIndex": 2435, "numDL-Frames": "sf-1"}),
                          (Prs(6, 0, 6, 300, "0110"), {"prs-Bandwidth": "n6", "prs-ConfigurationIndex": 0, "numDL-Frames": "sf-6", "prs-MutingInfo-r9": {"po4-r9": "0110"}, "prsID-r14": 300}),
                          (Prs(100, 2554, 4, None, "01" * 8), {"prs-Bandwidth": "n100", "prs-ConfigurationIndex": 2554, "numDL-Frames": "sf-4", "prs-MutingInfo-r9": {"po16-r9": "01" * 8}}),
                          (Prs(15, 5, 2, 0, "1" * 32), {"prs-Bandwidth": "n15", "prs-ConfigurationIndex": 5, "numDL-Frames": "sf-2", "prs-MutingInfo-r9": {"po32-v1420": "1" * 32}, "prsID-r14": 0})):
            self.assertEqual(encode_prs_info(prs), want)
            self.assertEqual(decode_prs_info(want), prs)

    def test_a_neighbour_with_another_prs_info_states_it(self):
        import json
        d = CellCyclicPrefix().doc()
        d.neighbours[0].prs = Prs(25, 2435, 1, 77, "10")
        env, raw = load(d.to_json())
        self.assertEqual(raw["neighbours"][0]["otdoa-eNB-CellDataList"][0]["otdoa-NeighbourCellInfoElement"]["prsInfo"]["prsID-r14"], 77)
        self.assertEqual(AssistanceDocument.from_json(d.to_json()).neighbours[0].prs, Prs(25, 2435, 1, 77, "10"))

    def test_ecgi(self):
        import json
        from bps.assistance import encode_ecgi, decode_ecgi
        self.assertEqual(encode_ecgi(("901", "56", 0x19B01)), {"mcc": [9, 0, 1], "mnc": [5, 6], "cellidentity": format(0x19B01, "028b")})
        self.assertEqual(decode_ecgi(encode_ecgi(("001", "010", 2**28 - 1))), ("001", "010", 2**28 - 1))
        for bad in (("90", "56", 1), ("901", "5", 1), ("901", "56", 2**28), ("9o1", "56", 1)):
            with self.assertRaises(ValueError):
                encode_ecgi(bad)
        d = CellCyclicPrefix().doc()
        d.reference.ecgi = ("901", "56", 0x19B01)
        env, raw = load(d.to_json())
        self.assertEqual(raw["reference"]["referenceCellInfo"]["cellGlobalId"]["mnc"], [5, 6])
        self.assertEqual(AssistanceDocument.from_json(d.to_json()).reference.ecgi, ("901", "56", 0x19B01))
        self.assertIsNone(AssistanceDocument.from_json(CellCyclicPrefix().doc().to_json()).reference.ecgi)

    def test_expected_rstd_coding(self):
        from bps.assistance import encode_expected, decode_expected
        unit = 3 * TS_S * 1e9
        self.assertEqual(encode_expected(0.0, 0.0), (8192, 0))
        self.assertEqual(encode_expected(unit, unit * 2.2), (8193, 3))                    # the uncertainty is rounded up: the window must still cover
        self.assertEqual(encode_expected(-unit * 8192, unit * 1023), (0, 1023))
        e, u = decode_expected(8195, 4)
        self.assertAlmostEqual(e, 3 * unit, delta=1e-6)
        self.assertAlmostEqual(u, 4 * unit, delta=1e-6)
        for bad in ((unit * 8192, 0.0), (-unit * 8193, 0.0), (0.0, unit * 1024)):
            with self.assertRaises(ValueError):
                encode_expected(*bad)

    def test_the_window_covers_a_ue_anywhere_in_the_service_area(self):
        # expectedRSTD +- uncertainty must contain the RSTD (geometry plus the RTD at any time in the validity window) of a UE anywhere within the stated radius
        from bps.assistance import encode_expected, decode_expected, fill_expected_rstd
        d = AssistanceDocument.from_json(CellCyclicPrefix().doc().to_json())
        cells = (d.reference, *d.neighbours)
        llas = [(c.latitude_deg, c.longitude_deg, c.height_m) for c in cells]
        centre = tuple(float(x) for x in geo.ecef_to_lla(np.mean([geo.lla_to_ecef(*p) for p in llas], axis=0)))
        radius = 5000.0
        d.valid_from_gps_ns, d.valid_until_gps_ns = 10**18 - 30 * 10**9, 10**18 + 60 * 10**9
        d.neighbours[0].rtd = Rtd(1000.0, 5.0, 10**18, 3.0, 0.4)
        fill_expected_rstd(d, centre, radius)
        e, u = decode_expected(*encode_expected(d.neighbours[0].expected_rstd_ns, d.neighbours[0].expected_rstd_uncertainty_ns))
        rng = np.random.default_rng(3)
        for _ in range(200):
            ang, r = rng.uniform(0, 2 * math.pi), radius * math.sqrt(rng.uniform())
            ue = geo.enu_to_lla(centre, (r * math.cos(ang), r * math.sin(ang), 0.0))
            for t in (d.valid_from_gps_ns, 10**18, d.valid_until_gps_ns):
                rstd = (np.linalg.norm(geo.lla_to_ecef(*ue) - geo.lla_to_ecef(*llas[1])) - np.linalg.norm(geo.lla_to_ecef(*ue) - geo.lla_to_ecef(*llas[0]))) / 299792458.0 * 1e9 \
                    + d.neighbours[0].rtd.at(t)
                self.assertLessEqual(abs(rstd - e), u, (ang, r, t))

    def test_the_default_service_area_is_the_one_bounded_by_the_cells(self):
        d = CellCyclicPrefix().doc()          # two cells about 11 km apart: radius about 5.5 km, so the window is about 2 x 5.5 km / c wide
        self.assertAlmostEqual(d.neighbours[0].expected_rstd_uncertainty_ns, 2 * 5560 / 0.299792458 + 3 * math.hypot(5.0, 0.0) + 0.0, delta=0.05 * 2 * 5560 / 0.299792458)

    def test_rtd_info_carries_subframe_offset_when_known(self):
        from bps.assistance import Rtd, encode_rtd, decode_rtd
        r = encode_rtd(Rtd(0.0, 5.0, 0, subframe_offset=0))
        self.assertEqual(r["subframeOffset"], 0)                                      # zero is a value, not an absent field
        self.assertEqual(decode_rtd(r).subframe_offset, 0)
        self.assertEqual(list(r)[0], "subframeOffset")
        self.assertIsNone(decode_rtd(encode_rtd(Rtd(0.0, 5.0, 0))).subframe_offset)
        with self.assertRaises(ValueError):
            encode_rtd(Rtd(0.0, 5.0, 0, subframe_offset=10230))

    def test_rtd_reference_std_and_slot_offset(self):
        import json
        d = CellCyclicPrefix().doc()
        d.reference_rtd_std_ns = 20.0
        d.neighbours[0].slot_number_offset = 0
        env, raw = load(d.to_json())
        self.assertEqual(raw["reference"]["positionCalculationInfoRef"]["rtdReferenceStd"], {"resolution": "ns-5", "quality": 4})
        self.assertEqual(raw["neighbours"][0]["otdoa-eNB-CellDataList"][0]["otdoa-NeighbourCellInfoElement"]["slotNumberOffset"], 0)
        back = AssistanceDocument.from_json(d.to_json())
        self.assertEqual((back.reference_rtd_std_ns, back.neighbours[0].slot_number_offset), (20.0, 0))
        d.neighbours[0].slot_number_offset = 20
        with self.assertRaises(ValueError):
            d.to_json()

    def test_expected_rstd_is_mandatory_on_a_neighbour(self):
        d = CellCyclicPrefix().doc()
        d.neighbours[0].expected_rstd_ns = None
        with self.assertRaises(ValueError):
            d.to_json()

    def test_co_located_cells_share_a_location(self):
        import json
        env, raw = load(CellCyclicPrefix().doc().to_json())
        el = raw["neighbours"][0]
        second = json.loads(json.dumps(el["otdoa-eNB-CellDataList"][0]))
        second["otdoa-NeighbourCellInfoElement"]["physCellId"] = 9
        el["otdoa-eNB-CellDataList"].append(second)
        back = AssistanceDocument.from_json(json.dumps(env))
        self.assertEqual([c.phys_cell_id for c in back.neighbours], [2, 9])
        self.assertEqual((back.neighbours[0].latitude_deg, back.neighbours[0].longitude_deg), (back.neighbours[1].latitude_deg, back.neighbours[1].longitude_deg))

    def test_sfn_is_a_10_bit_string_and_range_checked(self):
        import json
        d = CellCyclicPrefix().doc()
        d.reference_sfn = 1023
        self.assertEqual(load(d.to_json())[1]["reference"]["positionCalculationInfoRef"]["systemFrameNumber"], "1111111111")
        self.assertEqual(AssistanceDocument.from_json(d.to_json()).reference_sfn, 1023)
        d.reference_sfn = 0
        self.assertEqual(AssistanceDocument.from_json(d.to_json()).reference_sfn, 0)    # zero is a value, not an absent field
        for bad in (-1, 1024):
            d.reference_sfn = bad
            with self.assertRaises(ValueError):
                d.to_json()

    def test_sfn_from_the_modems_boundary(self):
        import http.server, json, threading
        from bps.live import ModemSfn

        class H(http.server.BaseHTTPRequestHandler):
            body = {"sib16": {"time_reference": {"time_ns": 10**18}, "boundary": {"boundary_sfn": 1000}}}

            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(H.body).encode())

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            f = ModemSfn(f"http://127.0.0.1:{srv.server_address[1]}/modem-api/sib_info")
            self.assertEqual(f(10**18), 1000)
            self.assertEqual(f(10**18 + 10_000_000 * 30), 6)                  # 30 frames later: 1030 mod 1024
            self.assertEqual(f(10**18 - 10_000_000 * 1001), 1023)             # before the boundary, wrapping the other way: -1
            H.body = {"sib16": {}}
            self.assertIsNone(f(10**18))                                      # no boundary known
        finally:
            srv.shutdown(); srv.server_close()
        self.assertIsNone(ModemSfn("http://127.0.0.1:9/none", timeout_s=0.2)(0))   # unreachable: None, not an exception

    def test_the_reader_refuses_what_the_writer_would_not_write(self):
        # every field of the document that the ASN.1 types with a range, an enumeration or a length is checked on reading as on writing
        import copy, json
        d = CellCyclicPrefix().doc()
        d.reference.ecgi = ("901", "56", 0x19B01)
        d.reference_sfn = 5
        d.neighbours[0].prs = Prs(25, 2435, 1, 77, "1010")
        d.neighbours[0].expected_rstd_ns = 1000.0
        genv, good = load(d.to_json())
        AssistanceDocument.from_json(json.dumps(genv))                       # the unmodified one reads

        def nb(raw):
            return raw["neighbours"][0]

        def cell(raw):
            return nb(raw)["otdoa-eNB-CellDataList"][0]

        def info(raw):
            return cell(raw)["otdoa-NeighbourCellInfoElement"]

        mutations = {
            "degreesLatitude too large": lambda r: r["reference"]["positionCalculationInfoRef"]["cellLocation"]["reference-point"]["referencePointGeographicLocation"]["location3D"].update(degreesLatitude=2**23),
            "degreesLongitude too small": lambda r: r["reference"]["positionCalculationInfoRef"]["cellLocation"]["reference-point"]["referencePointGeographicLocation"]["location3D"].update(degreesLongitude=-2**23 - 1),
            "altitude too large": lambda r: r["reference"]["positionCalculationInfoRef"]["cellLocation"]["reference-point"]["referencePointGeographicLocation"]["location3D"].update(altitude=2**15),
            "latitudeSign": lambda r: r["reference"]["positionCalculationInfoRef"]["cellLocation"]["reference-point"]["referencePointGeographicLocation"]["location3D"].update(latitudeSign="east"),
            "relativeNorth too large": lambda r: nb(r)["relative-Location"].update(relativeNorth=524288),
            "units": lambda r: nb(r)["relative-Location"].update(units="km"),
            "relative altitude": lambda r: nb(r)["relative-Location"]["relativeAltitude"]["geodeticRelativeAltitude"].update({"geodetic-height-depth": 40000}),
            "fineRTD 100000": lambda r: cell(r)["rtdInfo"].update(fineRTD=100000),
            "fineRTD a string": lambda r: cell(r)["rtdInfo"].update(fineRTD="12"),
            "fineRTDdriftRate 101": lambda r: cell(r)["rtdInfo"].update(fineRTDdriftRate=101),
            "subframeOffset 10230": lambda r: cell(r)["rtdInfo"].update(subframeOffset=10230),
            "quality 128": lambda r: cell(r)["rtdInfo"]["fineRTDstd"].update(quality=128),
            "resolution ns-7": lambda r: cell(r)["rtdInfo"]["fineRTDstd"].update(resolution="ns-7"),
            "expectedRSTD 16384": lambda r: info(r).update(expectedRSTD=16384),
            "expectedRSTD-Uncertainty -1": lambda r: info(r).update({"expectedRSTD-Uncertainty": -1}),
            "expectedRSTD a string": lambda r: info(r).update(expectedRSTD="8192"),
            "prs-Bandwidth n20": lambda r: info(r)["prsInfo"].update({"prs-Bandwidth": "n20"}),
            "numDL-Frames sf-3": lambda r: info(r)["prsInfo"].update({"numDL-Frames": "sf-3"}),
            "prsID-r14 4096": lambda r: info(r)["prsInfo"].update({"prsID-r14": 4096}),
            "prs-ConfigurationIndex 4096": lambda r: info(r)["prsInfo"].update({"prs-ConfigurationIndex": 4096}),
            "muting alternative and length differ": lambda r: info(r)["prsInfo"].update({"prs-MutingInfo-r9": {"po8-r9": "1010"}}),
            "muting alternative unknown": lambda r: info(r)["prsInfo"].update({"prs-MutingInfo-r9": {"po3-r9": "101"}}),
            "muting two alternatives": lambda r: info(r)["prsInfo"].update({"prs-MutingInfo-r9": {"po2-r9": "10", "po4-r9": "1010"}}),
            "muting bits": lambda r: info(r)["prsInfo"].update({"prs-MutingInfo-r9": {"po4-r9": "10x0"}}),
            "ECGI mcc 2 digits": lambda r: r["reference"]["referenceCellInfo"]["cellGlobalId"].update(mcc=[9, 0]),
            "ECGI mnc digit 10": lambda r: r["reference"]["referenceCellInfo"]["cellGlobalId"].update(mnc=[5, 10]),
            "ECGI cell identity 27 bits": lambda r: r["reference"]["referenceCellInfo"]["cellGlobalId"].update(cellidentity="1" * 27),
            "systemFrameNumber 9 bits": lambda r: r["reference"]["positionCalculationInfoRef"].update(systemFrameNumber="1" * 9),
            "systemFrameNumber digits": lambda r: r["reference"]["positionCalculationInfoRef"].update(systemFrameNumber="2" * 10),
        }
        for name, mutate in mutations.items():
            env2 = copy.deepcopy(genv)
            mutate(AssistanceDocument.unwrap(env2))
            with self.assertRaises(ValueError, msg=name):
                AssistanceDocument.from_json(json.dumps(env2))

    def test_the_publisher_log_line_reads_the_version_from_the_file_it_sent(self):
        from bps.apps import _sent_version
        d = CellCyclicPrefix().doc()
        d.version = 1478698084
        self.assertEqual(_sent_version(d.to_json().encode()), 1478698084)

    def test_the_file_store_replaces_the_file_whole(self):
        import os, tempfile
        from bps.apps import _file_store
        with tempfile.TemporaryDirectory() as d:
            store = _file_store(os.path.join(d, "www", "positioning"), "http://127.0.0.1:3005/positioning/assistance.json")
            store(b"one")
            store(b"two")
            self.assertEqual(open(os.path.join(d, "www", "positioning", "assistance.json"), "rb").read(), b"two")
            self.assertEqual(os.listdir(os.path.join(d, "www", "positioning")), ["assistance.json"])    # no temporary file left
        with self.assertRaises(SystemExit):
            _file_store("", "http://x/a.json")

    def test_the_old_schema_is_refused(self):
        import json
        env, raw = load(CellCyclicPrefix().doc().to_json())
        raw["schema"] = "bps-assistance/0-draft"
        with self.assertRaises(ValueError):
            AssistanceDocument.from_json(json.dumps(raw))


class CellCyclicPrefix(unittest.TestCase):
    """The cyclic prefix is stated per cell (TS 37.355 cpLength): mandatory on the reference cell, on a neighbour only when it is not the reference cell's."""

    def doc(self, ref_cp="extended", n_cp=None):
        prs = Prs(25, 2435, 1)
        d = AssistanceDocument(1, 0, 10**18, Cell(1, 46.0, 6.0, 400.0, prs, cp_length=ref_cp),
                               [Cell(2, 46.1, 6.0, 400.0, prs, Rtd(1000.0, 5.0, 10**18), n_cp)])
        fill_expected_rstd(d)
        return d

    def test_round_trip(self):
        back = AssistanceDocument.from_json(self.doc("extended", "normal").to_json())
        self.assertEqual((back.reference.cp_length, back.neighbours[0].cp_length), ("extended", "normal"))

    def test_a_neighbour_with_the_reference_prefix_leaves_it_out(self):
        import json
        env, raw = load(self.doc("extended", "extended").to_json())
        self.assertNotIn("cpLength", raw["neighbours"][0]["otdoa-eNB-CellDataList"][0]["otdoa-NeighbourCellInfoElement"])   # NotSameAsRef1: otherwise not present
        self.assertEqual(AssistanceDocument.from_json(json.dumps(env)).neighbours[0].cp_length, "extended")      # read as the reference cell's
        env, raw = load(self.doc("extended", "normal").to_json())
        self.assertEqual(raw["neighbours"][0]["otdoa-eNB-CellDataList"][0]["otdoa-NeighbourCellInfoElement"]["cpLength"], "normal")

    def test_the_reference_cell_must_state_it(self):
        with self.assertRaises(ValueError):
            self.doc(ref_cp=None).to_json()

    def test_another_value_is_refused(self):
        with self.assertRaises(ValueError):
            self.doc(n_cp="short").to_json()

    def test_the_lab_network_states_the_prefixes_it_is_given(self):
        from bps import lab
        net = lab.network({3: "normal"})
        cps = {s.as_cell().phys_cell_id: s.as_cell().cp_length for s in (net.reference, *net.neighbours)}
        self.assertEqual(cps, {1: "extended", 2: None, 3: "normal", 4: None})       # the reference cell always states one, a neighbour only if it differs
        self.assertEqual(lab.network(reference_cp="normal").reference.cp_length, "normal")

    def test_the_command_line_form(self):
        from bps.apps import _cell_cp
        self.assertEqual(_cell_cp(["3=normal", "4=extended"]), {3: "normal", 4: "extended"})
        self.assertEqual(_cell_cp(None), {})
        for bad in ("3", "x=normal", "3=short", "=normal"):
            with self.assertRaises(SystemExit):
                _cell_cp([bad])


class PrsOccasions(unittest.TestCase):
    def test_table_6_10_4_3_1_rows(self):
        # one point per row of the table, as read from the rendered TS 36.211 V19.3.0 page 175
        expected = {0: (160, 0), 159: (160, 159), 160: (320, 0), 479: (320, 319), 480: (640, 0), 1119: (640, 639),
                    1120: (1280, 0), 2399: (1280, 1279), 2400: (5, 0), 2404: (5, 4), 2405: (10, 0), 2414: (10, 9),
                    2415: (20, 0), 2434: (20, 19), 2435: (40, 0), 2474: (40, 39), 2475: (80, 0), 2554: (80, 79)}
        for index, value in expected.items():
            self.assertEqual(prs_period_offset(index), value, index)

    def test_reserved_indices_rejected(self):
        for index in (2555, 4095, -1, 4096):
            with self.assertRaises(ValueError):
                prs_period_offset(index)

    def test_every_period_divides_the_sfn_wrap(self):
        for _, _, period in _PRS_TABLE:
            self.assertEqual(10240 % period, 0, period)

    def test_formula_and_next_occasion_agree(self):
        for index in (0, 160, 480, 1120, 2402, 2410, 2420, 2440, 2480, 2554):
            hits = [a for a in range(0, 20480) if is_prs_occasion_start((a // 10) % 1024, a % 10, index)]
            period, _ = prs_period_offset(index)
            self.assertTrue(all(b - a == period for a, b in zip(hits, hits[1:])))
            for start in (0, 1, 7, 333, 10239, 10240, 12345):
                nxt = next_prs_occasion_start(start, index)
                self.assertGreaterEqual(nxt, start)
                self.assertTrue(is_prs_occasion_start((nxt // 10) % 1024, nxt % 10, index))
                self.assertFalse(any(is_prs_occasion_start((a // 10) % 1024, a % 10, index) for a in range(start, nxt)))

    def test_known_occasions(self):
        # I_PRS = 2402 -> T=5, Delta=2: subframes 2 and 7 of every frame
        self.assertEqual([s for s in range(10) if is_prs_occasion_start(0, s, 2402)], [2, 7])
        with self.assertRaises(ValueError):
            is_prs_occasion_start(1024, 0, 0)


class Sib16Time(unittest.TestCase):
    def test_r11_gps_epoch_matches_note_1(self):
        # TS 36.331 V19.4.0 clause 6.3.1 NOTE 1: GPS = timeInfoUTC(s) - 2,524,953,600 + leapSeconds
        self.assertEqual(sib16_r11_to_gps_ns(2_524_953_600 * 100, 0), 0)
        self.assertEqual(sib16_r11_to_gps_ns(2_524_953_600 * 100 + 1, 0), 10_000_000)
        self.assertEqual(sib16_r11_to_gps_ns(2_524_953_600 * 100, 18), 18_000_000_000)
        with self.assertRaises(ValueError):
            sib16_r11_to_gps_ns(549_755_813_888, 0)

    def test_r15_unit_arithmetic(self):
        self.assertEqual(sib16_r15_time_to_ns(0, 0, 0, 1), 250)
        self.assertEqual(sib16_r15_time_to_ns(0, 0, 0, 3999), 999_750)
        self.assertEqual(sib16_r15_time_to_ns(0, 0, 1, 0), 1_000_000)
        self.assertEqual(sib16_r15_time_to_ns(0, 1, 0, 0), 1_000_000_000)
        self.assertEqual(sib16_r15_time_to_ns(1, 0, 0, 0), 86_400 * 10**9)
        for bad in ((72999 + 1, 0, 0, 0), (0, 86400, 0, 0), (0, 0, 1000, 0), (0, 0, 0, 4000)):
            with self.assertRaises(ValueError):
                sib16_r15_time_to_ns(*bad)

    def test_r15_round_trip_truncates_to_quarter_microsecond(self):
        rng = np.random.default_rng(3)
        for _ in range(500):
            ns = int(rng.integers(0, 72999 * 86400 * 10**9))
            self.assertEqual(sib16_r15_time_to_ns(*gps_ns_to_sib16_r15_fields(ns)), ns - ns % 250)

    def test_anchor_advances_by_subframes(self):
        a = TimeAnchor(100, 5_000)
        self.assertEqual(a.tx_time_ns(100), 5_000)
        self.assertEqual(a.tx_time_ns(110), 5_000 + 10 * SUBFRAME_NS)


class Quantisation(unittest.TestCase):
    def test_one_ts_bins_up_to_4096(self):
        self.assertAlmostEqual(quantise_rstd_ts(0.4 * TS_S) / TS_S, 0.5)
        self.assertAlmostEqual(quantise_rstd_ts(1.0 * TS_S) / TS_S, 0.5)
        self.assertAlmostEqual(quantise_rstd_ts(1.01 * TS_S) / TS_S, 1.5)
        self.assertAlmostEqual(quantise_rstd_ts(-0.4 * TS_S) / TS_S, -0.5)
        self.assertAlmostEqual(quantise_rstd_ts(4096 * TS_S) / TS_S, 4095.5)

    def test_five_ts_bins_above_4096(self):
        self.assertAlmostEqual(quantise_rstd_ts(4097 * TS_S) / TS_S, 4098.5)
        self.assertAlmostEqual(quantise_rstd_ts(4101 * TS_S) / TS_S, 4098.5)
        self.assertAlmostEqual(quantise_rstd_ts(4101.5 * TS_S) / TS_S, 4103.5)
        self.assertAlmostEqual(quantise_rstd_ts(-4097 * TS_S) / TS_S, -4098.5)
        with self.assertRaises(ValueError):
            quantise_rstd_ts(15392 * TS_S)


class AssistanceDoc(unittest.TestCase):
    def doc(self):
        rng = np.random.default_rng(0)
        return build_document(np.array([0, 1000.0, -2000.0, 3000.0]), 0.0, np.zeros(4), 0.0, 5.0, 10**18, rng)

    def test_round_trip_is_the_coded_value(self):
        # what a UE reads is the document as coded: positions within the coding's range, the RTD within its 10 ns step, the drift rounded to 1 ns/s
        d = self.doc()
        again = AssistanceDocument.from_json(d.to_json())
        for a, b in zip((again.reference, *again.neighbours), (d.reference, *d.neighbours)):
            self.assertEqual((a.phys_cell_id, a.prs, a.cp_length), (b.phys_cell_id, b.prs, b.cp_length or d.reference.cp_length))
            e, n, u = geo.lla_to_enu((b.latitude_deg, b.longitude_deg, b.height_m), (a.latitude_deg, a.longitude_deg, a.height_m))
            self.assertLess(math.hypot(e, n), 1.0 if a is not again.reference else 3.0, a.phys_cell_id)   # TS 23.032 clause 6.1: under 3 m for the point
            self.assertLess(abs(a.height_m - b.height_m), 1.0)
        for a, b in zip(again.neighbours, d.neighbours):
            self.assertLessEqual(abs((a.rtd.rtd_ns - b.rtd.rtd_ns + 500_000) % 1_000_000 - 500_000), 5.0 + 1e-6)
            self.assertLessEqual(abs(a.rtd.drift_ns_per_s - b.rtd.drift_ns_per_s), 0.5 + 1e-9)
            self.assertGreaterEqual(a.rtd.std_ns, b.rtd.std_ns)             # the coding error is added, never taken off
        self.assertEqual((again.version, again.valid_from_gps_ns, again.valid_until_gps_ns), (d.version, d.valid_from_gps_ns, d.valid_until_gps_ns))

    def test_rejections(self):
        d = self.doc()
        d.neighbours[0].prs.configuration_index = 3000
        with self.assertRaises(ValueError):
            d.validate()
        d = self.doc()
        d.neighbours[1].phys_cell_id = d.reference.phys_cell_id
        with self.assertRaises(ValueError):
            d.validate()
        d = self.doc()
        d.neighbours[0].rtd.rtd_ns = 600_000
        with self.assertRaises(ValueError):
            d.validate()
        d = self.doc()
        d.reference.rtd = Rtd(0, 1, 0)
        with self.assertRaises(ValueError):
            d.validate()
        with self.assertRaises(ValueError):
            AssistanceDocument.from_json('{"schema": "other"}')

    def test_rtd_drift_and_uncertainty_growth(self):
        r = Rtd(100.0, 3.0, 10**18, drift_ns_per_s=2.0, drift_std_ns_per_s=0.5)
        self.assertAlmostEqual(r.at(10**18 + 10 * 10**9), 120.0)
        self.assertAlmostEqual(r.std_at(10**18 + 10 * 10**9), math.hypot(3.0, 5.0))

    def test_expected_rstd_field_semantics(self):
        self.assertEqual(expected_rstd_field(0.0), 8192)
        self.assertEqual(expected_rstd_field(3 * TS_S), 8193)
        self.assertEqual(expected_rstd_field(-3 * TS_S), 8191)
        self.assertAlmostEqual(expected_rstd_from_field(8193), 3 * TS_S)
        with self.assertRaises(ValueError):
            expected_rstd_field(9000 * 3 * TS_S)


class Solver(unittest.TestCase):
    def exact(self, ue, rtd_s, cells=CELLS_ENU):
        d = np.linalg.norm(cells - ue, axis=1)
        return (d - d[0]) / C_MPS + rtd_s

    def test_noise_free_2d_recovers_position(self):
        rtd = np.array([0, 40e-6, -70e-6, 12e-6])
        for ue in ([100.0, 200.0, 1.5], [5000.0, -5000.0, 1.5], [-3000.0, 6000.0, 1.5], [0.0, 0.0, 1.5]):
            ue = np.array(ue)
            sol = solve_rstd(CELLS_ENU, self.exact(ue, rtd), rtd, np.full(4, 1e-9), fix_up_m=ue[2])
            self.assertTrue(sol.converged)
            self.assertLess(np.linalg.norm(sol.position_enu[:2] - ue[:2]), 0.01, ue)

    def test_noise_free_3d_needs_four_cells(self):
        ue = np.array([2000.0, -1000.0, 80.0])
        cells = np.vstack([CELLS_ENU, [[-7000.0, -3000.0, 200.0]]])
        rtd = np.zeros(5)
        sol = solve_rstd(cells, self.exact(ue, rtd, cells), rtd, np.full(5, 1e-9))
        self.assertLess(np.linalg.norm(sol.position_enu - ue), 0.05)

    def test_too_few_cells_rejected(self):
        with self.assertRaises(ValueError):
            solve_rstd(CELLS_ENU[:2], np.zeros(2), np.zeros(2), np.ones(2), fix_up_m=0.0)
        with self.assertRaises(ValueError):
            solve_rstd(CELLS_ENU[:3], np.zeros(3), np.zeros(3), np.ones(3))  # 3D needs 4 cells

    def test_wrong_rtd_moves_the_answer(self):
        ue = np.array([1000.0, 1000.0, 1.5])
        rtd = np.array([0, 40e-6, -70e-6, 12e-6])
        wrong = rtd + np.array([0, 1e-6, 0, 0])  # 1 us is about 300 m of range
        sol = solve_rstd(CELLS_ENU, self.exact(ue, rtd), wrong, np.full(4, 1e-9), fix_up_m=ue[2])
        self.assertGreater(np.linalg.norm(sol.position_enu[:2] - ue[:2]), 100)

    def test_ue_time_adds_back_propagation_delay(self):
        a = TimeAnchor(1000, 10**18)
        self.assertEqual(ue_time_ns(a, 1000, 0.0), 10**18)
        self.assertEqual(ue_time_ns(a, 1010, C_MPS * 1e-6), 10**18 + 10 * SUBFRAME_NS + 1000)


class LiveModemData(unittest.TestCase):
    # A SIB16 object as rt-mbms-modem reported it on 2026-11-13 (sib_info REST), second SI message, 40 ms window.
    SIB16 = {
        "has_time_info": True, "leap_seconds": 18, "last_received_at": 1794594417060, "rx_sfn": 516,
        "time_info_utc_unix_ms": 1794594417090,
        "time_reference": {"local_clock": False, "ref_days": 17113, "ref_seconds": 63491, "ref_milli_seconds": 61,
                           "ref_quarter_micro_seconds": 3867, "time_ns": 1478626691061966750, "uncertainty": 12},
        "boundary": {"boundary_sfn": 520, "si_message_index": 2, "si_periodicity_rf": 256, "si_window_ms": 40},
    }

    def test_anchor_and_label(self):
        got = live.anchor_from_sib16(self.SIB16)
        self.assertIsNotNone(got)
        anchor, host_ms = got
        self.assertEqual(anchor.abs_subframe, 5200)            # boundary SFN 520
        self.assertEqual(anchor.gps_ns, 1478626691061966750)
        self.assertEqual(host_ms, 1794594417060 + 4 * 10)       # decode time plus 4 frames to the boundary
        # GPS - leapSeconds = UTC; POSIX time carries no leap seconds
        self.assertAlmostEqual(live.label_unix_ms(anchor, 18), 1478626691061.96675 + 315964800 * 1000 - 18000, places=3)

    def test_missing_or_local_clock_gives_none(self):
        no_r15 = {k: v for k, v in self.SIB16.items() if k != "time_reference"}
        self.assertIsNone(live.anchor_from_sib16(no_r15))
        local = dict(self.SIB16, time_reference=dict(self.SIB16["time_reference"], local_clock=True))
        self.assertIsNone(live.anchor_from_sib16(local))
        no_boundary = {k: v for k, v in self.SIB16.items() if k != "boundary"}
        self.assertIsNone(live.anchor_from_sib16(no_boundary))

    def test_anchor_residual(self):
        a = {"boundary_sfn": 8, "sample_clock_ns": 1_000_000_000}
        b = {"boundary_sfn": 264, "sample_clock_ns": 1_000_000_000 + 256 * 10_000_000}  # 256 frames later, exact
        self.assertEqual(live.anchor_residual_ns(a, b), 0)
        c = {"boundary_sfn": 264, "sample_clock_ns": b["sample_clock_ns"] + 7_000}        # 7 us late
        self.assertEqual(live.anchor_residual_ns(a, c), 7_000)
        wrap = {"boundary_sfn": 8, "sample_clock_ns": 1_000_000_000 + 1024 * 10_000_000 - 3_000}  # a whole SFN cycle, 3 us early
        self.assertEqual(live.anchor_residual_ns(a, wrap), -3_000)

    def test_boundary_across_the_sfn_wrap(self):
        s = dict(self.SIB16, rx_sfn=1020, boundary={"boundary_sfn": 2})
        _, host_ms = live.anchor_from_sib16(s)
        self.assertEqual(host_ms, 1794594417060 + ((2 - 1020) % 1024) * 10)


class Geo(unittest.TestCase):
    def test_round_trip_and_axes(self):
        for enu in ([0, 0, 0], [9000, 2500, 45], [-4000, 8000, 35]):
            lla = geo.enu_to_lla(ORIGIN_LLA, enu)
            self.assertLess(np.linalg.norm(geo.lla_to_enu(ORIGIN_LLA, lla) - np.array(enu)), 1e-3)
        north = geo.enu_to_lla(ORIGIN_LLA, [0, 1000, 0])
        self.assertGreater(north[0], ORIGIN_LLA[0])
        self.assertAlmostEqual(north[1], ORIGIN_LLA[1], places=6)
        self.assertEqual(len(PRS_INDEX), len(CELLS_ENU))


if __name__ == "__main__":
    unittest.main()
