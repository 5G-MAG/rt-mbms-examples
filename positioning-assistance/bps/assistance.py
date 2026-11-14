"""The assistance document a broadcaster publishes over the user plane. [design]

Field names follow the TS 37.355 V19.3.0 OTDOA assistance IEs where one exists (physCellId, prs-Bandwidth,
prs-ConfigurationIndex, numDL-Frames, expectedRSTD). The RTD and the cell positions follow OMA-TS-LPPe V2.0 (2020-08-04, candidate):
the RTD as `rtdInfo` (fineRTD in units of 10 ns, fineRTDstd as an RTDquality, fineRTDdriftRate in ns/s), the reference cell's position as
an EllipsoidPointWithAltitude (TS 23.032 V19.0.0 clause 6.1 and 6.3, ASN.1 of TS 37.355) and each neighbour's as an OMA-LPPe-RelativeLocation to it.
The in-memory `Cell` and `Rtd` hold plain numbers; `to_json` and `from_json` do the coding and so the quantisation (a document read back is what a UE
sees). Fields starting with `x_` are this prototype's own, not LPPe. The container (schema, version, validity window) is also this prototype's:
SPEC-CLAIMS.md "OMA LPPe".
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np

from . import geo
from .timebase import TS_S, prs_period_offset

SCHEMA = "bps-assistance/1-draft"  # [design] draft, versioned so a UE can refuse what it does not know (0-draft had its own RTD and position blocks)

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
    cp_length: str | None = None  # "normal" or "extended": the cell's cyclic prefix (TS 37.355 cpLength of OTDOA assistance data, per cell); absent: the serving cell's


# --- coding of the positions and the RTD (see the module docstring)

_LAT_STEPS = 2**23 / 90.0     # TS 23.032 V19.0.0 clause 6.1: N <= 2^23/90 X < N+1 on the absolute latitude, 23 bits and a sign bit
_LON_STEPS = 2**24 / 360.0    # clause 6.1: N <= 2^24/360 X < N+1, 24 bits two's complement
_UNIT_M = {"cm": 0.01, "dm": 0.1, None: 1.0, "m10": 10.0}   # OMA-LPPe-RelativeLocation units: the absence of the field is 1 m
_UNIT_ORDER = ("cm", "dm", None, "m10")
_REL_LIMIT = 524287           # INTEGER (-524288..524287)
_ALT_LIMIT = (-32768, 32767)  # geodetic-height-depth
_RTD_RESOLUTION_NS = {"ns-5": 5, "ns-10": 10, "ns-50": 50, "ns-100": 100}
_FINE_RTD_STEP_NS = 10.0      # fineRTD is in units of 10 ns
_MAX_NORTH_M, _MAX_EAST_M = 20_000.0, 10_000.0   # beyond these OMA-LPPe-RelativeLocation no longer allows the straight-line approximation used here


def encode_point(lat_deg: float, lon_deg: float, height_m: float) -> dict:
    """EllipsoidPointWithAltitude (TS 37.355 ASN.1) from a WGS 84 position, by the equations of TS 23.032 clause 6.1 and 6.3 (floor, so the point lies within
    the coded range)."""
    if not (-90 <= lat_deg <= 90 and -180 <= lon_deg <= 180):
        raise ValueError("latitude/longitude out of range")
    lat_n = min(int(math.floor(abs(lat_deg) * _LAT_STEPS)), 2**23 - 1)
    lon_n = max(-2**23, min(int(math.floor(lon_deg * _LON_STEPS)), 2**23 - 1))
    alt_n = min(int(math.floor(abs(height_m))), 2**15 - 1)
    return {"latitudeSign": "south" if lat_deg < 0 else "north", "degreesLatitude": lat_n, "degreesLongitude": lon_n,
            "altitudeDirection": "depth" if height_m < 0 else "height", "altitude": alt_n}


def decode_point(p: dict) -> tuple[float, float, float]:
    """The middle of the range each coded number stands for (the clause defines ranges, not points)."""
    lat = (p["degreesLatitude"] + 0.5) / _LAT_STEPS * (-1 if p["latitudeSign"] == "south" else 1)
    lon = (p["degreesLongitude"] + 0.5) / _LON_STEPS
    alt = (p["altitude"] + 0.5) * (-1 if p["altitudeDirection"] == "depth" else 1)
    return lat, lon, alt


def encode_relative(ref_lla: tuple[float, float, float], lla: tuple[float, float, float]) -> dict:
    """OMA-LPPe-RelativeLocation of `lla` against the reference point, in the smallest unit that holds all three numbers."""
    e, n, _ = geo.lla_to_enu(ref_lla, lla)
    if abs(n) > _MAX_NORTH_M or abs(e) > _MAX_EAST_M:
        raise ValueError(f"a cell {n:.0f} m north and {e:.0f} m east of the reference is beyond the straight-line range of RelativeLocation (20 km north, 10 km east)")
    dh = lla[2] - ref_lla[2]
    for unit in _UNIT_ORDER:
        u = _UNIT_M[unit]
        north, east, alt = round(n / u), round(e / u), round(dh / u)
        if abs(north) <= _REL_LIMIT and abs(east) <= _REL_LIMIT and _ALT_LIMIT[0] <= alt <= _ALT_LIMIT[1]:
            out = {"relativeNorth": north, "relativeEast": east, "relativeAltitude": {"geodeticRelativeAltitude": {"geodetic-height-depth": alt}}}
            if unit is not None:
                out["units"] = unit
            return out
    raise ValueError("the offset does not fit RelativeLocation in any unit")


def decode_relative(ref_lla: tuple[float, float, float], rel: dict) -> tuple[float, float, float]:
    u = _UNIT_M[rel.get("units")]
    lat, lon, _ = geo.enu_to_lla(ref_lla, (rel["relativeEast"] * u, rel["relativeNorth"] * u, 0.0))
    alt = rel["relativeAltitude"]["geodeticRelativeAltitude"]["geodetic-height-depth"] * u
    return float(lat), float(lon), ref_lla[2] + alt


def encode_rtd(rtd: "Rtd") -> dict:
    """rtdInfo (OMA-LPPe-OTDOA-CellData): fineRTD in units of 10 ns, taken as the offset of the neighbour's next subframe start after the reference's (0 to under 1 ms),
    fineRTDstd as an RTDquality, fineRTDdriftRate in ns/s. subframeOffset is not provided (optional, "if available": the grids here are tied to the cell acquisition
    subframes, no frame offset is known). The error of the coding (10 ns steps, 1 ns/s steps) is added to the standard deviations."""
    fine = round((rtd.rtd_ns % 1_000_000) / _FINE_RTD_STEP_NS) % 100_000
    std = math.hypot(rtd.std_ns, _FINE_RTD_STEP_NS / math.sqrt(12))
    for name, res in _RTD_RESOLUTION_NS.items():
        q = round(std / res)
        if q <= 127:
            break
    else:
        raise ValueError(f"an RTD standard deviation of {std:.0f} ns does not fit RTDquality (at most 127 x 100 ns)")
    drift = round(rtd.drift_ns_per_s)
    if not -100 <= drift <= 100:
        raise ValueError(f"an RTD drift rate of {rtd.drift_ns_per_s:.1f} ns/s does not fit fineRTDdriftRate (-100..100)")
    return {"fineRTD": fine, "fineRTDstd": {"resolution": name, "quality": q}, "fineRTDdriftRate": drift,
            "x_drift_std_ns_per_s": math.hypot(rtd.drift_std_ns_per_s, 1 / math.sqrt(12)), "x_valid_at_gps_ns": rtd.valid_at_gps_ns}


def decode_rtd(info: dict) -> "Rtd":
    ns = info["fineRTD"] * _FINE_RTD_STEP_NS
    if ns >= 500_000:
        ns -= 1_000_000       # the UE takes the closest subframe, so the offset is used within +-0.5 ms
    std = info["fineRTDstd"]["quality"] * _RTD_RESOLUTION_NS[info["fineRTDstd"]["resolution"]]
    return Rtd(ns, float(std), info["x_valid_at_gps_ns"], float(info.get("fineRTDdriftRate", 0)), float(info.get("x_drift_std_ns_per_s", 0.0)))


@dataclass
class AssistanceDocument:
    version: int
    valid_from_gps_ns: int
    valid_until_gps_ns: int
    reference: Cell
    neighbours: list[Cell] = field(default_factory=list)
    reference_sfn: int | None = None   # the reference cell's SFN at the time the RTDs hold (OMA-LPPe-OTDOA-PositionCalculationInfoRef systemFrameNumber, BIT STRING (SIZE(10)))

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
            if c.cp_length not in (None, "normal", "extended"):
                raise ValueError("cp_length must be \"normal\" or \"extended\"")
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
        if self.reference_sfn is not None and not 0 <= self.reference_sfn <= 1023:
            raise ValueError("systemFrameNumber must be 0..1023")
        if self.valid_until_gps_ns <= self.valid_from_gps_ns:
            raise ValueError("validity window is empty")

    def to_json(self) -> str:
        self.validate()
        ref_lla = decode_point(encode_point(self.reference.latitude_deg, self.reference.longitude_deg, self.reference.height_m))

        def cell(c: Cell, is_ref: bool) -> dict:
            out = {"phys_cell_id": c.phys_cell_id, "cp_length": c.cp_length,
                   "prs": {"bandwidth_prb": c.prs.bandwidth_prb, "configuration_index": c.prs.configuration_index, "num_dl_frames": c.prs.num_dl_frames,
                           "prs_id": c.prs.prs_id, "muting": c.prs.muting}}
            if is_ref:
                out["location"] = encode_point(c.latitude_deg, c.longitude_deg, c.height_m)
                if self.reference_sfn is not None:
                    out["position_calculation_info"] = {"systemFrameNumber": format(self.reference_sfn, "010b")}
            else:
                out["relative_location"] = encode_relative(ref_lla, (c.latitude_deg, c.longitude_deg, c.height_m))
                out["rtd_info"] = encode_rtd(c.rtd)
            return out

        return json.dumps({"schema": SCHEMA, "version": self.version, "valid_from_gps_ns": self.valid_from_gps_ns,
                           "valid_until_gps_ns": self.valid_until_gps_ns, "reference": cell(self.reference, True),
                           "neighbours": [cell(c, False) for c in self.neighbours]}, indent=1)

    @staticmethod
    def from_json(text: str) -> "AssistanceDocument":
        raw = json.loads(text)
        if raw.get("schema") != SCHEMA:
            raise ValueError(f"unknown schema {raw.get('schema')!r}")
        ref_lla = decode_point(raw["reference"]["location"])

        def cell(d: dict, is_ref: bool) -> Cell:
            lla = ref_lla if is_ref else decode_relative(ref_lla, d["relative_location"])
            return Cell(d["phys_cell_id"], lla[0], lla[1], lla[2], Prs(**d["prs"]), None if is_ref else decode_rtd(d["rtd_info"]), d.get("cp_length"))

        sfn = (raw["reference"].get("position_calculation_info") or {}).get("systemFrameNumber")
        doc = AssistanceDocument(raw["version"], raw["valid_from_gps_ns"], raw["valid_until_gps_ns"],
                                 cell(raw["reference"], True), [cell(c, False) for c in raw["neighbours"]],
                                 None if sfn is None else int(sfn, 2))
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
