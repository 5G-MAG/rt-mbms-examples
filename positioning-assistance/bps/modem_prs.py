"""The UE side of slide 15 of the broadcast positioning proposal: push the PRS information of the assistance document down to the modem's PHY, and take the
time-of-arrival measurements it makes back. [design]

"Once the FLUTE receiver on the UE reconstructs the JSON file, the PRS information, RTD, and gNB locations need to be pushed down to the PHY layer so that UE can
receive and process the PRSs to perform ToA measurements; UE can use the RTD and gNB locations along with the ToA measurements to estimate its position." The
modem (rt-mbms-modem, src/PrsToa.h) takes the PRS information over its REST interface and returns, per PRS occasion, each cell's time of arrival against its own
subframe grid; the RSTD of a neighbour is the difference to the reference cell's, which removes the grid offset they share. The RTD and the cell locations stay
here, in the positioning code, which is where the proposal puts them (they are not PHY information).

The modem measures; what counts as a detection is decided here: a cell is used only if its correlation peak stands out of the background by `ratio_min` (the
modem reports the peak over the median elsewhere; 25 for a cell sent at full power in the lab, 12 to 13 for one 6 dB down, 5 to 7 for a cell that is not there).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from .assistance import AssistanceDocument
from .ue import Measurement


class ModemRefused(Exception):
    """The modem did not accept the PRS information; the message is its reason."""


class ModemPrs:
    def __init__(self, base_url: str, ratio_min: float = 9.0, sigma_ns: float = 15.0, timeout_s: float = 5.0, signal: str = "prs"):
        """`base_url` is the modem's REST root, e.g. http://10.80.0.2:3010/modem-api. `sigma_ns` is the error given to each RSTD: measured live with the rig (8.7, 11.2 and 7.1 ns
        standard deviation and -5 to -9.5 ns mean error over 32 occasions, 25 PRB, one path), combined in quadrature. `signal` is what the modem takes the time of
        arrival from: "prs" (the PRS of the assistance data) or "pss_sss" (the PSS and SSS of each cell's cell acquisition subframe, the proposal's alternative where
        those subframes are not SFN'ed, slide 13 Note 1); the value is sent with the assistance data."""
        if signal not in ("prs", "pss_sss"):
            raise ValueError("signal is prs or pss_sss")
        self.signal = signal
        self.base = base_url.rstrip("/")
        self.ratio_min = ratio_min
        self.sigma_ns = sigma_ns
        self.timeout_s = timeout_s
        self._pushed: Optional[str] = None
        self._pushed_cells: Optional[str] = None   # what was pushed without the search windows: the part whose change makes the modem drop its results
        self._last_clock = -1
        self.last_reject = ""

    # -- the PRS information of a document, as the modem takes it
    @staticmethod
    def assistance_json(doc: AssistanceDocument) -> dict:
        cells = []
        for c in (doc.reference, *doc.neighbours):
            cells.append({"cell_id": c.phys_cell_id, "cp_length": c.cp_length or doc.reference.cp_length,
                          "subframe_offset": (c.rtd.subframe_offset or 0) if c.rtd is not None else 0, "expected_rstd_ns": c.expected_rstd_ns, "expected_rstd_uncertainty_ns": c.expected_rstd_uncertainty_ns,
                          "prs": {"bandwidth_prb": c.prs.bandwidth_prb, "configuration_index": c.prs.configuration_index,
                                  "num_dl_frames": c.prs.num_dl_frames, "prs_id": c.prs.prs_id, "muting": c.prs.muting}})
        return {"reference_cell_id": doc.reference.phys_cell_id, "cells": cells}

    @staticmethod
    def _without_windows(body: dict) -> dict:
        return {**body, "cells": [{k: v for k, v in c.items() if not k.startswith("expected_rstd")} for c in body["cells"]]}

    def _body(self, doc: AssistanceDocument) -> dict:
        body = self.assistance_json(doc)
        if self.signal != "prs":
            body["signal"] = self.signal
        return body

    def _call(self, method: str, path: str, body: Optional[dict] = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"null")
            except ValueError:
                payload = None
            return e.code, payload

    def push(self, doc: AssistanceDocument) -> None:
        """Pushes the PRS information of the document to the modem. Raises ModemRefused with the modem's reason if it will not take it."""
        body = self._body(doc)
        status, payload = self._call("PUT", "/prs_assistance", body)
        if status != 200:
            reason = (payload or {}).get("error", f"HTTP {status}") if isinstance(payload, dict) else f"HTTP {status}"
            raise ModemRefused(reason)
        self._pushed = json.dumps(body, sort_keys=True)
        cells = json.dumps(self._without_windows(body), sort_keys=True)
        if cells != self._pushed_cells:       # the modem keeps what it measured when only the windows were refreshed, so the ones already used must stay marked used
            self._last_clock = -1
        self._pushed_cells = cells

    def ensure(self, doc: AssistanceDocument) -> bool:
        """Pushes the document's PRS information unless the modem already has exactly that. Returns True if it pushed. A modem that has restarted (no assistance)
        gets it again."""
        want = json.dumps(self._body(doc), sort_keys=True)
        if want == self._pushed:
            status, payload = self._call("GET", "/prs")
            if status == 200 and isinstance(payload, dict) and payload.get("assistance") is not None:
                return False
        self.push(doc)
        return True

    def next(self, doc: AssistanceDocument) -> Optional[Measurement]:
        """The newest measurement not used before in which every cell of the document was detected, as RSTDs against the reference cell; None if there is none.
        The reason a newer one was passed over is in `last_reject`."""
        status, payload = self._call("GET", "/prs")
        if status != 200 or not isinstance(payload, dict):
            self.last_reject = f"the modem answered HTTP {status}"
            return None
        self.last_reject = ""
        ref = doc.reference.phys_cell_id
        wanted = [c.phys_cell_id for c in (doc.reference, *doc.neighbours)]
        for m in reversed(payload.get("measurements", [])):
            if m["sample_clock_ns"] <= self._last_clock:
                break
            cells = {c["cell_id"]: c for c in m["cells"]}
            missing = [c for c in wanted if c not in cells or not cells[c]["present"]]
            if missing:
                self.last_reject = self.last_reject or f"cell(s) {missing} muted or not measured at tti {m['tti']}"   # the newest rejection is the one reported
                continue
            weak = [c for c in wanted if cells[c]["ratio"] < self.ratio_min]
            if weak:
                self.last_reject = self.last_reject or "cell(s) %s not detected at tti %d (peak over background %s, needs %.0f)" % (
                    weak, m["tti"], ", ".join("%.1f" % cells[c]["ratio"] for c in weak), self.ratio_min)
                continue
            self._last_clock = m["sample_clock_ns"]
            rstd = {c: (cells[c]["toa_ns"] - cells[ref]["toa_ns"]) * 1e-9 for c in wanted if c != ref}
            sigma = self.sigma_ns * 1e-9
            return Measurement(int(m["sample_clock_ns"]), rstd, sigma_s=sigma, sigma_by_cell={c: sigma for c in rstd})
        return None


__all__ = ["ModemPrs", "ModemRefused"]
