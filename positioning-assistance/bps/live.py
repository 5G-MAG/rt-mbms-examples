"""Read the modem's decoded SIB16 over its REST API and turn it into the time anchor of the prototype. [design]

Needs a running rt-mbms-modem built with the SIB16 timeReferenceInfo decode (rt-mbms-modem, uncommitted change of 2026-11-13).
Local reference: the host clock at the moment the modem decoded the SI message. That is lab grade: it includes the modem's
processing delay and, with the ZMQ software radio, the radio's own pacing. The sample-clock anchor that a real receiver needs is
not implemented in the modem yet (SdrReader ignores the receive timestamp).
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

from .timebase import SUBFRAME_NS, TimeAnchor

GPS_EPOCH_UNIX_S = 315_964_800


def fetch_sib16(url: str) -> dict | None:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r).get("sib16")


def anchor_from_sib16(s: dict) -> tuple[TimeAnchor, float] | None:
    """Return (anchor, host_ms_of_boundary_estimate) or None when the SIB16 lacks what the alignment needs.

    [spec] the time names the start of the SFN boundary (TS 36.331 V19.4.0 clause 6.3.2); the modem reports which SFN
    (`boundary`), and the label in 0.25 us units from the GPS epoch (`time_reference`, origin GPS unless local_clock).
    """
    tr, b = s.get("time_reference"), s.get("boundary")
    if not tr or not b or tr.get("local_clock"):
        return None
    anchor = TimeAnchor(abs_subframe=b["boundary_sfn"] * 10, gps_ns=int(tr["time_ns"]))
    frames_ahead = (b["boundary_sfn"] - s["rx_sfn"]) % 1024
    host_boundary_ms = s["last_received_at"] + frames_ahead * 10  # decode time + whole frames to the boundary
    return anchor, host_boundary_ms


def anchor_residual_ns(prev: dict, cur: dict) -> int:
    """How far the receiver's sample clock disagrees with the frame structure between two anchors.

    Each anchor ties the start of subframe 0 of a boundary SFN to a sample-clock time. Between two anchors the clock must advance
    by exactly 10 ms per frame, so `sample_clock_ns` differences minus 10 ms times the SFN difference should be zero, up to whole
    SFN cycles (10.24 s) when anchors were missed. The result is that difference folded into [-5.12 s, 5.12 s). A real receiver
    should show microseconds; anything larger means the subframe-to-clock mapping is wrong. [design]
    """
    cycle = 1024 * 10_000_000
    delta = cur["sample_clock_ns"] - prev["sample_clock_ns"]
    expected = ((cur["boundary_sfn"] - prev["boundary_sfn"]) % 1024) * 10_000_000
    return (delta - expected + cycle // 2) % cycle - cycle // 2


def label_unix_ms(anchor: TimeAnchor, leap_seconds: int) -> float:
    """The label as Unix milliseconds: GPS - leapSeconds = UTC [spec, TS 36.331 clause 6.3.1], POSIX time ignores leap seconds."""
    return (anchor.gps_ns / 1e6) + GPS_EPOCH_UNIX_S * 1000 - leap_seconds * 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://10.80.0.2:3010/modem-api/sib_info")
    ap.add_argument("--seconds", type=float, default=20.0)
    a = ap.parse_args()
    seen, end, prev = set(), time.time() + a.seconds, None
    print(f"{'boundary':>8} {'label - host est (ms)':>22} {'sample clock s':>15} {'clock - frames (us)':>20} {'label - clock (ms)':>19}")
    while time.time() < end:
        s = fetch_sib16(a.url)
        anc = (s or {}).get("anchor")
        if s and anc and anc["sample_clock_ns"] not in seen:
            seen.add(anc["sample_clock_ns"])
            got = anchor_from_sib16(s)
            host_diff = ""
            if got:
                anchor, host_ms = got
                host_diff = f"{label_unix_ms(anchor, s.get('leap_seconds', 0)) - host_ms:.1f}"
            res = "" if prev is None else f"{anchor_residual_ns(prev, anc) / 1000:.1f}"
            label_minus_clock = (anc["label_ns"] - anc["sample_clock_ns"]) / 1e6
            print(f"{anc['boundary_sfn']:>8} {host_diff:>22} {anc['sample_clock_ns'] / 1e9:>15.6f} {res:>20} {label_minus_clock:>19.1f}")
            prev = anc
        time.sleep(0.5)
    if not seen:
        print("no SIB16 anchor received in the window")
    _ = SUBFRAME_NS


if __name__ == "__main__":
    main()
