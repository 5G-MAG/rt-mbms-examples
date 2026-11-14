"""Broadcaster side of option 1: build the assistance document from the network description and the RTD monitor, and push it. [design]"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .assistance import AssistanceDocument, Cell, Prs
from .rtd_monitor import InsufficientData, RtdMonitor


@dataclass(frozen=True)
class CellSite:
    phys_cell_id: int
    latitude_deg: float
    longitude_deg: float
    height_m: float
    prs: Prs
    cp_length: Optional[str] = None   # "normal" or "extended", carried in the document when set

    def as_cell(self) -> Cell:
        return Cell(self.phys_cell_id, self.latitude_deg, self.longitude_deg, self.height_m, self.prs, cp_length=self.cp_length)


@dataclass(frozen=True)
class Network:
    reference: CellSite
    neighbours: tuple[CellSite, ...]

    def lla(self) -> dict[int, tuple[float, float, float]]:
        return {c.phys_cell_id: (c.latitude_deg, c.longitude_deg, c.height_m) for c in (self.reference, *self.neighbours)}


class Publisher:
    """Turns the monitor's current RTD estimates into a document and puts it on the air.

    `validity_s` is how long the UE may use a document after it was made. The RTD it carries ages with the drift uncertainty of
    each cell (the UE evaluates `Rtd.std_at`), so a shorter validity bounds the position error; the model in
    results/decision-option1-vs-option2-2026-11-13.txt gives about 14-18 m at document ages up to 60 s and 100 m at 600 s.
    `backdate_s` starts the validity that long before the document was made: the RTD is a fitted line that is as good a little before
    its reference time as after (the UE's uncertainty grows with the distance in time either way), and the UE's network time comes from
    SIB16 anchors that can be a few seconds old. The version is the creation time in whole GPS seconds, so it increases across restarts and the UE can ignore an older document.
    """

    def __init__(self, network: Network, monitor: RtdMonitor, push: Callable[[bytes], None], validity_s: int = 90,
                 backdate_s: int = 30):
        if validity_s <= 0:
            raise ValueError("validity_s must be positive")
        self._network, self._monitor, self._push, self._validity_ns = network, monitor, push, int(validity_s * 1e9)
        self._backdate_ns = int(backdate_s * 1e9)
        self._last_version = 0

    def build(self, now_gps_ns: int) -> AssistanceDocument:
        rtds = self._monitor.estimate()
        missing = [c.phys_cell_id for c in self._network.neighbours if c.phys_cell_id not in rtds]
        if missing:
            raise InsufficientData(f"no RTD estimate for cells {missing}")
        version = max(int(now_gps_ns // 1_000_000_000), self._last_version + 1)
        neighbours = []
        for site in self._network.neighbours:
            cell = site.as_cell()
            cell.rtd = rtds[site.phys_cell_id]
            neighbours.append(cell)
        doc = AssistanceDocument(version, now_gps_ns - self._backdate_ns, now_gps_ns + self._validity_ns, self._network.reference.as_cell(), neighbours)
        doc.validate()
        return doc

    def publish_once(self, now_gps_ns: int) -> Optional[bytes]:
        """Build and push one document. Returns the bytes sent, or None when there is not enough monitoring data yet (nothing is
        sent: an absent document is better than one made of guesses)."""
        try:
            doc = self.build(now_gps_ns)
        except InsufficientData:
            return None
        body = doc.to_json().encode()
        self._push(body)
        self._last_version = doc.version
        return body
