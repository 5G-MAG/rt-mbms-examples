"""The assistance document a broadcaster publishes over the user plane. [design]

Field names follow the TS 37.355 V19.3.0 OTDOA assistance IEs where one exists (physCellId, prs-Bandwidth,
prs-ConfigurationIndex, numDL-Frames, expectedRSTD). Position and RTD have no 3GPP LPP counterpart in LTE UE-assisted
OTDOA (the location server keeps coordinates), and the OMA LPPe text that carries them (rtdInfo, relative location)
could not be obtained, so those two blocks are this prototype's own: see SPEC-CLAIMS.md "Not verified".
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .timebase import TS_S, prs_period_offset

SCHEMA = "bps-assistance/0-draft"  # [design] draft, versioned so a UE can refuse what it does not know

# [spec] TS 37.355 V19.3.0 clause 6.5.1.2 PRS-Info: prs-Bandwidth {n6,...,n100}, numDL-Frames {sf-1,sf-2,sf-4,sf-6}.
_PRS_BANDWIDTH_PRB = (6, 15, 25, 50, 75, 100)
_NUM_DL_FRAMES = (1, 2, 4, 6)


@dataclass
class Rtd:
    """Relative transmit delay of this cell's subframe grid w.r.t. the reference cell, wrapped to [-0.5, +0.5) ms."""

    rtd_ns: float
    std_ns: float
    valid_at_gps_ns: int  # network time at which rtd_ns holds
    drift_ns_per_s: float = 0.0
    drift_std_ns_per_s: float = 0.0  # uncertainty of the drift rate; makes the RTD uncertainty grow with age

    def at(self, gps_ns: int) -> float:
        return self.rtd_ns + self.drift_ns_per_s * (gps_ns - self.valid_at_gps_ns) / 1e9

    def std_at(self, gps_ns: int) -> float:
        age_s = (gps_ns - self.valid_at_gps_ns) / 1e9
        return (self.std_ns**2 + (self.drift_std_ns_per_s * age_s) ** 2) ** 0.5


# [spec] TS 37.355 V19.3.0 clause 6.5.1.2, prs-MutingInfo: the muting sequence has T_REP = 2, 4, 8, 16, 32, 64, 128, 256, 512 or 1024 bits.
_MUTING_LENGTHS = tuple(2**i for i in range(1, 11))


@dataclass
class Prs:
    """PRS information of a cell (slides 27 and 28 of the proposal; TS 37.355 PRS-Info).

    `prs_id` is the PRS ID; the cell identity when absent (TS 36.211 clause 6.10.4.1, "unless configured by higher layers"). `muting` is the PRS muting
    sequence as a string of '0' and '1': a '0' mutes the PRS occasion (TS 37.355 prs-MutingInfo); absent means no muting."""
    bandwidth_prb: int
    configuration_index: int
    num_dl_frames: int
    prs_id: int | None = None
    muting: str | None = None

    def effective_id(self, phys_cell_id: int) -> int:
        return phys_cell_id if self.prs_id is None else self.prs_id


@dataclass
class Cell:
    phys_cell_id: int
    latitude_deg: float
    longitude_deg: float
    height_m: float
    prs: Prs
    rtd: Rtd | None = None  # absent on the reference cell


@dataclass
class AssistanceDocument:
    version: int
    valid_from_gps_ns: int
    valid_until_gps_ns: int
    reference: Cell
    neighbours: list[Cell] = field(default_factory=list)

    def validate(self) -> None:
        cells = [self.reference, *self.neighbours]
        ids = [c.phys_cell_id for c in cells]
        if len(set(ids)) != len(ids):
            raise ValueError("physCellId must be unique within one document")
        for c in cells:
            if not 0 <= c.phys_cell_id <= 503:
                raise ValueError("physCellId must be 0..503")
            if c.prs.bandwidth_prb not in _PRS_BANDWIDTH_PRB:
                raise ValueError("prs bandwidth must be one of 6,15,25,50,75,100 PRB")
            if c.prs.num_dl_frames not in _NUM_DL_FRAMES:
                raise ValueError("numDL-Frames must be 1,2,4 or 6")
            prs_period_offset(c.prs.configuration_index)  # raises for reserved indices
            if c.prs.prs_id is not None and not 0 <= c.prs.prs_id <= 4095:
                raise ValueError("PRS ID must be 0..4095")
            if c.prs.muting is not None and (len(c.prs.muting) not in _MUTING_LENGTHS or set(c.prs.muting) - {"0", "1"}):
                raise ValueError("the PRS muting sequence must be 2, 4, 8, ... or 1024 characters of 0 and 1")
            if not (-90 <= c.latitude_deg <= 90 and -180 <= c.longitude_deg <= 180):
                raise ValueError("latitude/longitude out of range")
        if self.reference.rtd is not None:
            raise ValueError("the reference cell has no RTD (it defines zero)")
        for c in self.neighbours:
            if c.rtd is None:
                raise ValueError(f"cell {c.phys_cell_id} needs an rtd")
            if not -500_000 <= c.rtd.rtd_ns < 500_000:
                raise ValueError("rtd must be within +-0.5 ms (closest-subframe definition)")
        if self.valid_until_gps_ns <= self.valid_from_gps_ns:
            raise ValueError("validity window is empty")

    def to_json(self) -> str:
        self.validate()
        return json.dumps({"schema": SCHEMA, **asdict(self)}, indent=1)

    @staticmethod
    def from_json(text: str) -> "AssistanceDocument":
        raw = json.loads(text)
        if raw.get("schema") != SCHEMA:
            raise ValueError(f"unknown schema {raw.get('schema')!r}")

        def cell(d):
            return Cell(
                d["phys_cell_id"], d["latitude_deg"], d["longitude_deg"], d["height_m"], Prs(**d["prs"]),
                Rtd(**d["rtd"]) if d.get("rtd") else None,
            )

        doc = AssistanceDocument(
            raw["version"], raw["valid_from_gps_ns"], raw["valid_until_gps_ns"],
            cell(raw["reference"]), [cell(c) for c in raw["neighbours"]],
        )
        doc.validate()
        return doc


def expected_rstd_field(rstd_s: float) -> int:
    """[spec] TS 37.355 V19.3.0 clause 6.5.1.2: expectedRSTD value is (expectedRSTD-8192) with resolution 3Ts."""
    value = round(rstd_s / (3 * TS_S)) + 8192
    if not 0 <= value <= 16383:
        raise ValueError("expectedRSTD out of range 0..16383")
    return value


def expected_rstd_from_field(value: int) -> float:
    return (value - 8192) * 3 * TS_S
