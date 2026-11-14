"""UE side of option 1: take the assistance document, put its RTD at the time of the measurement, and solve. [design]

Time. The document gives each cell's RTD at a network time (GPS time) together with a drift, so the UE has to know the network time
of its own measurement. The cell tells it through SIB16: the time of one frame boundary (TS 36.331 V19.4.0 clause 6.3.2), which
the modem ties to the receiver's sample clock (rt-mbms-modem, `sib16.anchor`). Network time at any later sample-clock reading is
the label plus the sample-clock time elapsed since the anchor. The label is "referenced at the network, i.e., without compensating
for RF propagation delay": it names when the cell transmitted, not when the UE received, so once the position is known the distance
to the reference cell is added back to get the UE's own time.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from . import geo
from .assistance import POINTER_SCHEMA, AssistanceDocument
from .solver import Solution, solve_rstd
from .timebase import C_MPS, SUBFRAME_NS, TS_S


class StaleDocument(RuntimeError):
    pass


def _http_fetch(url: str, max_bytes: int, timeout_s: float = 5.0) -> bytes:
    """GET `url` (http or https only); more than `max_bytes` is refused, not truncated."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http and https URLs are fetched")
    with urllib.request.urlopen(url, timeout=timeout_s) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"the file is larger than {max_bytes} bytes")
    return data


class NetworkClock:
    """Network (GPS) time of a sample-clock reading, from the latest SIB16 anchor."""

    def __init__(self) -> None:
        self._label_ns: Optional[int] = None
        self._clock_ns: Optional[int] = None

    def update(self, label_gps_ns: int, sample_clock_ns: int) -> None:
        if self._clock_ns is None or sample_clock_ns >= self._clock_ns:
            self._label_ns, self._clock_ns = label_gps_ns, sample_clock_ns

    def update_from_modem(self, sib16: dict) -> bool:
        """Take the anchor of the modem's `sib16` REST object. False when it carries none usable (no anchor, or a label whose
        origin is a local clock)."""
        a = (sib16 or {}).get("anchor")
        if not a or a.get("label_local_clock"):
            return False
        self.update(int(a["label_ns"]), int(a["sample_clock_ns"]))
        return True

    @property
    def ready(self) -> bool:
        return self._clock_ns is not None

    def network_time_ns(self, sample_clock_ns: int) -> int:
        if not self.ready:
            raise StaleDocument("no SIB16 time anchor yet")
        return self._label_ns + (sample_clock_ns - self._clock_ns)


class AssistanceStore:
    """Holds the newest valid assistance document the UE has received."""

    def __init__(self, fetch: Optional[Callable[[str, int], bytes]] = None, max_bytes: int = 1 << 20) -> None:
        """`fetch(url, max_bytes)` gets the assistance file when what arrives over the air is a pointer to it (the deck, slide 18); the default is an HTTP GET.
        `max_bytes` bounds what is accepted from the URL: there is no clause for it, so it is a parameter (default 1 MiB, about 200 times the file the lab sends)."""
        self._doc: Optional[AssistanceDocument] = None
        self._fetch = fetch or _http_fetch
        self._max_bytes = max_bytes

    def ingest(self, raw: bytes) -> str:
        """The file that came over the air: the document, or a pointer to it. A pointer to a document no newer than the current one is not fetched."""
        try:
            is_pointer = json.loads(raw.decode()).get("schema") == POINTER_SCHEMA
        except (ValueError, AttributeError, UnicodeDecodeError):
            is_pointer = False
        if not is_pointer:
            return self._ingest_document(raw)
        try:
            url, version = AssistanceDocument.parse_pointer(raw.decode())
        except (ValueError, KeyError, TypeError) as e:
            return f"rejected: {e}"
        if self._doc is not None and version <= self._doc.version:
            return "ignored: not newer than the current document"
        try:
            body = self._fetch(url, self._max_bytes)
        except (OSError, ValueError) as e:
            return f"rejected: the assistance file at {url} could not be fetched: {e}"
        return self._ingest_document(body)

    def _ingest_document(self, raw: bytes) -> str:
        """Returns a short verdict: accepted, ignored (not newer) or rejected (with the reason). A document that does not validate
        never replaces a good one."""
        try:
            doc = AssistanceDocument.from_json(raw.decode())
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as e:
            return f"rejected: {e}"
        for c in doc.neighbours:
            # The UE measures at the reference cell's PRS occasions with the grids folded to the closest subframe; a neighbour whose frames or slots are offset from the
            # reference's would have its PRS elsewhere, and the folding would be wrong. Refused rather than guessed.
            if c.rtd.subframe_offset not in (None, 0) and c.rtd.subframe_offset % 40 != 0:
                return (f"rejected: cell {c.phys_cell_id} has subframeOffset {c.rtd.subframe_offset}: only a multiple of 40 subframes is supported (PRS is only in the cell acquisition "
                        "subframes of an MBMS-dedicated carrier, which recur every 4 frames, so any other offset puts this cell's one elsewhere than the reference cell's)")
            if c.slot_number_offset not in (None, 0):
                return f"rejected: cell {c.phys_cell_id} has slotNumberOffset {c.slot_number_offset}: a slot offset between cells is not supported"
        if self._doc is not None and doc.version <= self._doc.version:
            return "ignored: not newer than the current document"
        self._doc = doc
        return "accepted"

    def current(self, network_time_ns: int) -> AssistanceDocument:
        if self._doc is None:
            raise StaleDocument("no assistance document received")
        if network_time_ns < self._doc.valid_from_gps_ns:
            raise StaleDocument("the measurement is %.1f s before the assistance document's validity starts"
                                % ((self._doc.valid_from_gps_ns - network_time_ns) / 1e9))
        if network_time_ns >= self._doc.valid_until_gps_ns:
            raise StaleDocument("the measurement is %.1f s after the assistance document's validity ended"
                                % ((network_time_ns - self._doc.valid_until_gps_ns) / 1e9))
        return self._doc


@dataclass(frozen=True)
class Measurement:
    """RSTDs taken at one instant: the sample-clock time of the reference cell's subframe, and each cell's RSTD against it."""

    sample_clock_ns: int
    rstd_s: dict[int, float]   # cell id -> RSTD in seconds (TS 36.214 clause 5.1.12)
    sigma_s: float             # 1-sigma of one RSTD (reporting resolution and noise)
    sigma_by_cell: Optional[dict[int, float]] = None   # cell id -> 1-sigma of that cell's RSTD; overrides sigma_s where present


@dataclass
class Fix:
    latitude_deg: float
    longitude_deg: float
    horizontal_sigma_m: float
    time_gps_ns: int           # the UE's own GPS time at the reference cell's subframe: network time plus propagation delay
    document_version: int
    solution: Solution


def _wrap_ns(x: float) -> float:
    return (x + SUBFRAME_NS / 2) % SUBFRAME_NS - SUBFRAME_NS / 2


class Positioner:
    def __init__(self, clock: NetworkClock, store: AssistanceStore, height_m: Optional[float] = None):
        """`height_m`: the UE's height when known (a 2D fix needs 3 cells, a 3D fix 4)."""
        self._clock, self._store, self._height = clock, store, height_m

    def solve(self, m: Measurement) -> Fix:
        t_net = self._clock.network_time_ns(m.sample_clock_ns)
        doc = self._store.current(t_net)
        cells = [doc.reference, *doc.neighbours]
        origin = (doc.reference.latitude_deg, doc.reference.longitude_deg, doc.reference.height_m)
        enu = np.array([geo.lla_to_enu(origin, (c.latitude_deg, c.longitude_deg, c.height_m)) for c in cells])
        rstd = np.zeros(len(cells))
        rtd = np.zeros(len(cells))
        sigma = np.full(len(cells), 1.0)
        for i, c in enumerate(cells[1:], start=1):
            if c.phys_cell_id not in m.rstd_s:
                raise ValueError(f"no RSTD measured for cell {c.phys_cell_id}")
            rstd[i] = m.rstd_s[c.phys_cell_id]
            rtd[i] = _wrap_ns(c.rtd.at(t_net)) * 1e-9  # evaluated at the network time of the measurement, folded to +-0.5 ms
            own = (m.sigma_by_cell or {}).get(c.phys_cell_id, m.sigma_s)
            sigma[i] = float(np.hypot(own, c.rtd.std_at(t_net) * 1e-9))
        fix_up = None if self._height is None else self._height - origin[2]
        sol = solve_rstd(enu, rstd, rtd, sigma, ref=0, fix_up_m=fix_up)
        if not sol.converged:
            raise ValueError("the solver did not converge")
        lat, lon, _ = geo.enu_to_lla(origin, sol.position_enu)
        d_ref = float(np.linalg.norm(sol.position_enu - enu[0]))
        return Fix(lat, lon, sol.horizontal_sigma_m, t_net + round(d_ref / C_MPS * 1e9), doc.version, sol)


__all__ = ["NetworkClock", "AssistanceStore", "Measurement", "Positioner", "Fix", "StaleDocument", "TS_S"]
