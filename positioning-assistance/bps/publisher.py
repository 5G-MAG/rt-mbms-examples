"""Broadcaster side of option 1: build the assistance document from the network description and the RTD monitor, and push it. [design]"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional

from .assistance import AssistanceDocument, Cell, Prs, fill_expected_rstd
from .rtd_monitor import InsufficientData, RtdMonitor


@dataclass(frozen=True)
class CellSite:
    phys_cell_id: int
    latitude_deg: float
    longitude_deg: float
    height_m: float
    prs: Prs
    cp_length: Optional[str] = None   # "normal" or "extended": mandatory on the reference cell; on a neighbour only when it is not the reference cell's
    ecgi: Optional[tuple[str, str, int]] = None   # (mcc, mnc, cell identity), carried as cellGlobalId when set
    subframe_offset: Optional[int] = None   # whole subframes between SFN=0 of the reference cell and of this cell, when known (0: the frame structures coincide)

    def as_cell(self) -> Cell:
        return Cell(self.phys_cell_id, self.latitude_deg, self.longitude_deg, self.height_m, self.prs, cp_length=self.cp_length, ecgi=self.ecgi)


@dataclass(frozen=True)
class Network:
    reference: CellSite
    neighbours: tuple[CellSite, ...]
    service_centre_lla: Optional[tuple[float, float, float]] = None   # the a-priori UE position behind expectedRSTD; default: the mean of the cell positions
    service_radius_m: Optional[float] = None   # how far from it a UE may be; default: the largest distance from the centre to a cell

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
                 backdate_s: int = 30, sfn_at: Optional[Callable[[int], Optional[int]]] = None,
                 repeat_interval_s: Optional[float] = None, broadcast_area: Optional[str] = None, leap_seconds: int = 18,
                 file_url: Optional[str] = None, store_file: Optional[Callable[[bytes], None]] = None):
        if validity_s <= 0:
            raise ValueError("validity_s must be positive")
        self._network, self._monitor, self._push, self._validity_ns = network, monitor, push, int(validity_s * 1e9)
        self._backdate_ns = int(backdate_s * 1e9)
        self._envelope = {"repeat_interval_s": repeat_interval_s, "broadcast_area": broadcast_area, "leap_seconds": leap_seconds}   # how the file's envelope (the deck's slide 14) is filled
        if (file_url is None) != (store_file is None):
            raise ValueError("file_url and store_file go together: the file is stored where the URL points, and the URL is sent")
        self._file_url, self._store_file = file_url, store_file   # the deck, slide 18: send the URL of the file instead of the file
        self._sfn_at = sfn_at   # the reference cell's SFN at a network time, if the broadcaster can say; the document then carries it with the RTDs
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
            cell.rtd = replace(rtds[site.phys_cell_id], subframe_offset=site.subframe_offset)
            neighbours.append(cell)
        sfn = None
        valid_at = {c.rtd.valid_at_gps_ns for c in neighbours}
        if self._sfn_at is not None and len(valid_at) == 1:   # one SFN states one instant: only if every RTD holds at the same one
            sfn = self._sfn_at(valid_at.pop())
        doc = AssistanceDocument(version, now_gps_ns - self._backdate_ns, now_gps_ns + self._validity_ns, self._network.reference.as_cell(), neighbours, sfn,
                                 self._monitor.reference_std_ns)
        doc.created_gps_ns = now_gps_ns
        for k, v in self._envelope.items():
            setattr(doc, k, v)
        fill_expected_rstd(doc, self._network.service_centre_lla, self._network.service_radius_m)
        doc.validate()
        return doc

    def publish_once(self, now_gps_ns: int) -> Optional[bytes]:
        """Build and push one document. Returns the document made (what is on the air is that, or with a file URL its pointer), or None when there is not enough monitoring data yet (nothing is
        sent: an absent document is better than one made of guesses)."""
        try:
            doc = self.build(now_gps_ns)
        except InsufficientData:
            return None
        body = doc.to_json().encode()
        if self._file_url is not None:
            self._store_file(body)       # a pointer to a file that is not there is worse than nothing: if this fails nothing is sent
            sent = doc.pointer_json(self._file_url).encode()
        else:
            sent = body
        self._push(sent)
        self._last_version = doc.version
        return body
