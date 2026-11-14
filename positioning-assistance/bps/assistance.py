"""The assistance document a broadcaster publishes over the user plane. [design]

Field names follow the TS 37.355 V19.3.0 OTDOA assistance IEs where one exists (physCellId, prs-Bandwidth,
prs-ConfigurationIndex, numDL-Frames, expectedRSTD). The RTD and the cell positions follow OMA-TS-LPPe V2.0 (2020-08-04, candidate):
the RTD as `rtdInfo` (fineRTD in units of 10 ns, fineRTDstd as an RTDquality, fineRTDdriftRate in ns/s), the reference cell's position as
an EllipsoidPointWithAltitude (TS 23.032 V19.0.0 clause 6.1 and 6.3, ASN.1 of TS 37.355) and each neighbour's as an OMA-LPPe-RelativeLocation to it.
The layout nests the IEs as OMA-LPPe does (see `to_json`), with the ASN.1 field names, and leaves out what the conditions of the IEs say to leave out (a
neighbour's cpLength and prsInfo when they are the reference cell's); the neighbours carry the mandatory expectedRSTD and expectedRSTD-Uncertainty, the search window.
The in-memory `Cell` and `Rtd` hold plain numbers; `to_json` and `from_json` do the coding and so the quantisation (a document read back is what a UE
sees). Fields starting with `x_` are this prototype's own, not LPPe. The container (schema, version, validity window) is also this prototype's:
SPEC-CLAIMS.md "OMA LPPe".
"""
from __future__ import annotations

import datetime
import json
import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import geo
from .timebase import TS_S, prs_period_offset

SCHEMA = "bps-assistance/3-draft"  # [design] draft, versioned so a UE can refuse what it does not know (0: own RTD and position blocks; 1: LPPe values in a flat layout; 2: the IEs nested; 3: in the envelope of the deck)
_GPS_EPOCH = datetime.datetime(1980, 1, 6, tzinfo=datetime.timezone.utc)
_AUTHOR = "5G-MAG"

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
    subframe_offset: int | None = None  # rtdInfo subframeOffset: whole subframes between SFN=0 of the reference and of this cell; absent: not known

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
    cp_length: str | None = None  # "normal" or "extended": the cell's cyclic prefix (TS 37.355 cpLength); mandatory on the reference cell, absent on a neighbour: the reference cell's
    ecgi: tuple[str, str, int] | None = None  # (mcc, mnc, 28 bit cell identity): cellGlobalId, optional
    expected_rstd_ns: float | None = None  # neighbours: expectedRSTD, the RSTD expected at the centre of the service area, in ns
    expected_rstd_uncertainty_ns: float | None = None  # neighbours: expectedRSTD-Uncertainty, the half width of the search window around it, in ns
    slot_number_offset: int | None = None  # neighbours: slotNumberOffset 0..19; absent: the slot timing is the reference cell's


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


def _int(d: dict, key: str, lo: int, hi: int) -> int:
    """A field of the document that the ASN.1 types as INTEGER (lo..hi): refused when it is anything else or outside."""
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{key} must be an integer in {lo}..{hi}, not {v!r}")
    return v


def _choice(d: dict, key: str, allowed) -> str:
    v = d[key]
    if v not in allowed:
        raise ValueError(f"{key} must be one of {sorted(allowed, key=str)}, not {v!r}")
    return v


def _bits(v, n: int, what: str) -> str:
    if not isinstance(v, str) or len(v) != n or set(v) - {"0", "1"}:
        raise ValueError(f"{what} must be a bit string of {n} bits, not {v!r}")
    return v


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
    lat_n, lon_n, alt_n = _int(p, "degreesLatitude", 0, 2**23 - 1), _int(p, "degreesLongitude", -2**23, 2**23 - 1), _int(p, "altitude", 0, 2**15 - 1)
    lat = (lat_n + 0.5) / _LAT_STEPS * (-1 if _choice(p, "latitudeSign", ("north", "south")) == "south" else 1)
    lon = (lon_n + 0.5) / _LON_STEPS
    alt = (alt_n + 0.5) * (-1 if _choice(p, "altitudeDirection", ("height", "depth")) == "depth" else 1)
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
    if rel.get("units") not in _UNIT_M:
        raise ValueError(f"units must be one of cm, dm, m10 or absent, not {rel.get('units')!r}")
    u = _UNIT_M[rel.get("units")]
    east, north = _int(rel, "relativeEast", -_REL_LIMIT - 1, _REL_LIMIT), _int(rel, "relativeNorth", -_REL_LIMIT - 1, _REL_LIMIT)
    lat, lon, _ = geo.enu_to_lla(ref_lla, (east * u, north * u, 0.0))
    alt = _int(rel["relativeAltitude"]["geodeticRelativeAltitude"], "geodetic-height-depth", *_ALT_LIMIT) * u
    return float(lat), float(lon), ref_lla[2] + alt


def encode_quality(std_ns: float) -> dict:
    """OMA-LPPe-OTDOA-RTDquality: the finest resolution (5, 10, 50 or 100 ns) with which the deviation fits in `quality` 0..127."""
    for name, res in _RTD_RESOLUTION_NS.items():
        q = round(std_ns / res)
        if q <= 127:
            return {"resolution": name, "quality": q}
    raise ValueError(f"a standard deviation of {std_ns:.0f} ns does not fit RTDquality (at most 127 x 100 ns)")


def decode_quality(d: dict) -> float:
    return float(_int(d, "quality", 0, 127) * _RTD_RESOLUTION_NS[_choice(d, "resolution", _RTD_RESOLUTION_NS)])


def encode_rtd(rtd: "Rtd") -> dict:
    """rtdInfo (OMA-LPPe-OTDOA-CellData): fineRTD in units of 10 ns, taken as the offset of the neighbour's next subframe start after the reference's (0 to under 1 ms),
    fineRTDstd as an RTDquality, fineRTDdriftRate in ns/s, subframeOffset when known (optional, "if available"). The error of the coding (10 ns steps, 1 ns/s steps)
    is added to the standard deviations."""
    fine = round((rtd.rtd_ns % 1_000_000) / _FINE_RTD_STEP_NS) % 100_000
    drift = round(rtd.drift_ns_per_s)
    if not -100 <= drift <= 100:
        raise ValueError(f"an RTD drift rate of {rtd.drift_ns_per_s:.1f} ns/s does not fit fineRTDdriftRate (-100..100)")
    out = {}
    if rtd.subframe_offset is not None:
        if not 0 <= rtd.subframe_offset <= 10229:
            raise ValueError("subframeOffset must be 0..10229")
        out["subframeOffset"] = rtd.subframe_offset
    out.update({"fineRTD": fine, "fineRTDstd": encode_quality(math.hypot(rtd.std_ns, _FINE_RTD_STEP_NS / math.sqrt(12))), "fineRTDdriftRate": drift,
                "x_drift_std_ns_per_s": math.hypot(rtd.drift_std_ns_per_s, 1 / math.sqrt(12)), "x_valid_at_gps_ns": rtd.valid_at_gps_ns})
    return out


def decode_rtd(info: dict) -> "Rtd":
    ns = _int(info, "fineRTD", 0, 99999) * _FINE_RTD_STEP_NS
    if ns >= 500_000:
        ns -= 1_000_000       # the UE takes the closest subframe, so the offset is used within +-0.5 ms
    drift = _int(info, "fineRTDdriftRate", -100, 100) if "fineRTDdriftRate" in info else 0
    offset = _int(info, "subframeOffset", 0, 10229) if "subframeOffset" in info else None
    return Rtd(ns, decode_quality(info["fineRTDstd"]), info["x_valid_at_gps_ns"], float(drift), float(info.get("x_drift_std_ns_per_s", 0.0)), offset)


def encode_prs_info(p: "Prs") -> dict:
    """PRS-Info of TS 37.355 V19.3.0 clause 6.5.1.2: prs-Bandwidth, prs-ConfigurationIndex, numDL-Frames, prs-MutingInfo-r9 (a CHOICE by length) and prsID-r14."""
    out = {"prs-Bandwidth": f"n{p.bandwidth_prb}", "prs-ConfigurationIndex": p.configuration_index, "numDL-Frames": f"sf-{p.num_dl_frames}"}
    if p.muting is not None:
        n = len(p.muting)
        out["prs-MutingInfo-r9"] = {f"po{n}-r9" if n <= 16 else f"po{n}-v1420": p.muting}
    if p.prs_id is not None:
        out["prsID-r14"] = p.prs_id
    return out


def decode_prs_info(d: dict) -> "Prs":
    muting = None
    if "prs-MutingInfo-r9" in d:
        choice = d["prs-MutingInfo-r9"]
        if not isinstance(choice, dict) or len(choice) != 1:
            raise ValueError("prs-MutingInfo-r9 is a CHOICE: exactly one alternative")
        (name, bits), = choice.items()
        for n in _MUTING_LENGTHS:
            if name == (f"po{n}-r9" if n <= 16 else f"po{n}-v1420"):
                muting = _bits(bits, n, name)
                break
        else:
            raise ValueError(f"prs-MutingInfo-r9 alternative {name!r} is not one of po2-r9 .. po16-r9, po32-v1420 .. po1024-v1420")
    bandwidth = _choice(d, "prs-Bandwidth", [f"n{b}" for b in _PRS_BANDWIDTH_PRB])
    frames = _choice(d, "numDL-Frames", [f"sf-{n}" for n in _NUM_DL_FRAMES])
    prs_id = _int(d, "prsID-r14", 0, 4095) if "prsID-r14" in d else None
    return Prs(int(bandwidth[1:]), _int(d, "prs-ConfigurationIndex", 0, 4095), int(frames[3:]), prs_id, muting)


def encode_ecgi(ecgi: tuple[str, str, int]) -> dict:
    """ECGI of TS 37.355 V19.3.0: mcc as three digits, mnc as two or three, cellidentity as a 28 bit string."""
    mcc, mnc, ci = ecgi
    if not (len(mcc) == 3 and len(mnc) in (2, 3) and (mcc + mnc).isdigit() and 0 <= ci < 2**28):
        raise ValueError("an ECGI needs a 3 digit MCC, a 2 or 3 digit MNC and a cell identity below 2^28")
    return {"mcc": [int(c) for c in mcc], "mnc": [int(c) for c in mnc], "cellidentity": format(ci, "028b")}


def decode_ecgi(d: dict) -> tuple[str, str, int]:
    for key, sizes in (("mcc", (3,)), ("mnc", (2, 3))):
        v = d[key]
        if not isinstance(v, list) or len(v) not in sizes or any(isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 9 for x in v):
            raise ValueError(f"ECGI {key} must be {' or '.join(map(str, sizes))} digits 0..9, not {v!r}")
    return "".join(map(str, d["mcc"])), "".join(map(str, d["mnc"])), int(_bits(d["cellidentity"], 28, "cellidentity"), 2)


_RSTD_UNIT_NS = 3 * TS_S * 1e9    # expectedRSTD and its uncertainty are in units of 3 Ts


def encode_expected(expected_ns: float, uncertainty_ns: float) -> tuple[int, int]:
    """expectedRSTD (offset 8192, 3 Ts) and expectedRSTD-Uncertainty (3 Ts, rounded up so the window still covers what it was meant to)."""
    value = round(expected_ns / _RSTD_UNIT_NS) + 8192
    unc = math.ceil(uncertainty_ns / _RSTD_UNIT_NS - 1e-9)
    if not 0 <= value <= 16383:
        raise ValueError(f"expectedRSTD of {expected_ns:.0f} ns does not fit 0..16383 (+-{8192 * _RSTD_UNIT_NS:.0f} ns)")
    if not 0 <= unc <= 1023:
        raise ValueError(f"expectedRSTD-Uncertainty of {uncertainty_ns:.0f} ns does not fit 0..1023 (at most {1023 * _RSTD_UNIT_NS:.0f} ns)")
    return value, unc


def decode_expected(value: int, unc: int) -> tuple[float, float]:
    for what, v, hi in (("expectedRSTD", value, 16383), ("expectedRSTD-Uncertainty", unc, 1023)):
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= hi:
            raise ValueError(f"{what} must be an integer in 0..{hi}, not {v!r}")
    return (value - 8192) * _RSTD_UNIT_NS, unc * _RSTD_UNIT_NS


def expected_rstd_window(ref_lla, cell_lla, centre_lla, radius_m: float, rtd: "Rtd", t_from_ns: int, t_until_ns: int) -> tuple[float, float]:
    """expectedRSTD and its uncertainty, in ns, for one neighbour. [design] The location server's a-priori estimate of the UE position is the centre of the service area
    and the uncertainty covers any UE within `radius_m` of it (the RSTD changes by at most 2 radius / c), the RTD drifting over the validity window and three of its
    standard deviations (TS 37.355 leaves the choice to the server: "related to the location server's a-priori estimation of the target device location")."""
    c_ns = 299_792_458.0 / 1e9

    def dist(a, b):
        return float(np.linalg.norm(geo.lla_to_ecef(*a) - geo.lla_to_ecef(*b)))

    t_mid = (t_from_ns + t_until_ns) // 2
    geometric = (dist(centre_lla, cell_lla) - dist(centre_lla, ref_lla)) / c_ns
    expected = geometric + rtd.at(t_mid)
    drift = max(abs(rtd.at(t) - rtd.at(t_mid)) for t in (t_from_ns, t_until_ns))
    sigma = max(rtd.std_at(t) for t in (t_from_ns, t_until_ns))
    return expected, 2.0 * radius_m / c_ns + drift + 3.0 * sigma


def fill_expected_rstd(doc: "AssistanceDocument", centre_lla=None, radius_m: float | None = None) -> None:
    """Sets expectedRSTD and its uncertainty on every neighbour of the document. The default service area is the one bounded by the cells: its centre is the mean of
    the cell positions and its radius the largest distance from it to a cell."""
    llas = [(c.latitude_deg, c.longitude_deg, c.height_m) for c in (doc.reference, *doc.neighbours)]
    if centre_lla is None:
        centre_lla = tuple(float(x) for x in geo.ecef_to_lla(np.mean([geo.lla_to_ecef(*p) for p in llas], axis=0)))
    if radius_m is None:
        radius_m = max(float(np.linalg.norm(geo.lla_to_ecef(*p) - geo.lla_to_ecef(*centre_lla))) for p in llas)
    ref_lla = llas[0]
    for c, lla in zip(doc.neighbours, llas[1:]):
        c.expected_rstd_ns, c.expected_rstd_uncertainty_ns = expected_rstd_window(ref_lla, lla, centre_lla, radius_m, c.rtd, doc.valid_from_gps_ns, doc.valid_until_gps_ns)


@dataclass
class AssistanceDocument:
    version: int
    valid_from_gps_ns: int
    valid_until_gps_ns: int
    reference: Cell
    neighbours: list[Cell] = field(default_factory=list)
    reference_sfn: int | None = None   # the reference cell's SFN at the time the RTDs hold (OMA-LPPe-OTDOA-PositionCalculationInfoRef systemFrameNumber, BIT STRING (SIZE(10)))
    reference_rtd_std_ns: float | None = None   # rtdReferenceStd: the standard deviation of the reference cell's timing used to determine the RTDs
    service_id: str = "PositioningService"      # envelope (deck, slide 14): serviceId
    service_name: str = "MBMS Assistance Data Delivery"
    broadcast_area: str | None = None           # envelope: broadcastArea, when the broadcaster names one
    repeat_interval_s: float | None = None      # envelope: fileSchedule repeatInterval, how often the publisher sends the file
    created_gps_ns: int | None = None           # envelope: metadata createdDate
    leap_seconds: int = 18                      # GPS minus UTC, for the ISO 8601 times of the envelope (the exact validity is in x_ fields)

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
            if c.slot_number_offset is not None and not 0 <= c.slot_number_offset <= 19:
                raise ValueError("slotNumberOffset must be 0..19")
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

    def _iso(self, gps_ns: int) -> str:
        utc = _GPS_EPOCH + datetime.timedelta(microseconds=gps_ns // 1000 - self.leap_seconds * 1_000_000)
        return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"

    def to_json(self) -> str:
        """The file the publisher sends: the key-value envelope of the deck (slide 14, "Sample JSON file containing positioning assistance data": MBMSDelivery with serviceId,
        serviceName, deliveryMethod, fileSchedule, textData of textId, title and contents of contentId, content and author, and metadata), holding the IEs of `_ie_dict`.
        The deck says the example is to be replicated for every parameter needed: the physical and global cell identity are entries of their own, as in the example, and
        each IE is the content of an entry of the cell it belongs to. The exact validity (GPS nanoseconds) is in the x_ fields of the metadata; the ISO 8601 times are for readers."""
        ie = self._ie_dict()
        ref = self.reference
        ref_contents = [{"contentId": "PHY Reference Cell ID", "content": str(ref.phys_cell_id), "author": _AUTHOR}]
        if ref.ecgi is not None:
            ref_contents.append({"contentId": "Global Reference Cell ID", "content": "-".join(map(str, ref.ecgi)), "author": _AUTHOR})
        ref_contents.append({"contentId": "OMA-LPPe-OTDOA-ReferenceCellInfo", "content": ie["reference"], "author": _AUTHOR})
        text_data = [{"textId": "eNBRefInfo", "title": "Reference eNB information", "contents": ref_contents}]
        for n in ie["neighbours"]:
            cid = n["otdoa-eNB-CellDataList"][0]["otdoa-NeighbourCellInfoElement"]["physCellId"]
            text_data.append({"textId": f"eNBNeighbourInfo{cid}", "title": f"Neighbour eNB {cid} information",
                              "contents": [{"contentId": "PHY Cell ID", "content": str(cid), "author": _AUTHOR},
                                           {"contentId": "OMA-LPPe-OTDOA-NeighbourCellInfoElement-eNB", "content": n, "author": _AUTHOR}]})
        schedule = {"startTime": self._iso(self.valid_from_gps_ns), "endTime": self._iso(self.valid_until_gps_ns)}
        if self.repeat_interval_s is not None:
            schedule["repeatInterval"] = f"PT{self.repeat_interval_s:g}S"
        meta = {"description": "Positioning assistance data for 5G Broadcast", "version": str(self.version), "createdBy": _AUTHOR,
                "x_valid_from_gps_ns": self.valid_from_gps_ns, "x_valid_until_gps_ns": self.valid_until_gps_ns, "x_leap_seconds": self.leap_seconds}
        if self.created_gps_ns is not None:
            meta["createdDate"] = self._iso(self.created_gps_ns)
        delivery = {"serviceId": self.service_id, "serviceName": self.service_name}
        if self.broadcast_area is not None:
            delivery["broadcastArea"] = self.broadcast_area
        delivery.update({"deliveryMethod": "broadcast", "fileSchedule": schedule, "textData": text_data, "metadata": meta})
        return json.dumps({"schema": SCHEMA, "MBMSDelivery": delivery}, indent=1)

    def _ie_dict(self) -> dict:
        """The document as the IEs of OMA-TS-LPPe V2.0 and TS 37.355 nest them: OMA-LPPe-OTDOA-ReferenceCellInfo for the reference cell and one
        OMA-LPPe-OTDOA-NeighbourCellInfoElement-eNB per neighbour (a neighbour with the reference cell's cyclic prefix or PRS-Info leaves them out, as the conditions
        of the IEs require). The container (schema, version, validity window) is this prototype's own."""
        self.validate()
        ref = self.reference
        if ref.cp_length is None:
            raise ValueError("cpLength is mandatory in OTDOA-ReferenceCellInfo: state the reference cell's cyclic prefix")
        ref_lla = decode_point(encode_point(ref.latitude_deg, ref.longitude_deg, ref.height_m))
        ref_info = {"physCellId": ref.phys_cell_id}
        if ref.ecgi is not None:
            ref_info["cellGlobalId"] = encode_ecgi(ref.ecgi)
        ref_info["cpLength"] = ref.cp_length
        ref_info["prsInfo"] = encode_prs_info(ref.prs)
        calc = {}
        if self.reference_sfn is not None:
            calc["systemFrameNumber"] = format(self.reference_sfn, "010b")
        if self.reference_rtd_std_ns is not None:
            calc["rtdReferenceStd"] = encode_quality(self.reference_rtd_std_ns)
        calc["cellLocation"] = {"reference-point": {"referencePointGeographicLocation": {"location3D": encode_point(ref.latitude_deg, ref.longitude_deg, ref.height_m)}}}

        def neighbour(c: Cell) -> dict:
            if c.expected_rstd_ns is None or c.expected_rstd_uncertainty_ns is None:
                raise ValueError(f"cell {c.phys_cell_id}: expectedRSTD and expectedRSTD-Uncertainty are mandatory (see fill_expected_rstd)")
            info = {"physCellId": c.phys_cell_id}
            if c.ecgi is not None:
                info["cellGlobalId"] = encode_ecgi(c.ecgi)
            if c.cp_length is not None and c.cp_length != ref.cp_length:
                info["cpLength"] = c.cp_length
            if c.prs != ref.prs:
                info["prsInfo"] = encode_prs_info(c.prs)
            if c.slot_number_offset is not None:
                info["slotNumberOffset"] = c.slot_number_offset
            info["expectedRSTD"], info["expectedRSTD-Uncertainty"] = encode_expected(c.expected_rstd_ns, c.expected_rstd_uncertainty_ns)
            return {"relative-Location": encode_relative(ref_lla, (c.latitude_deg, c.longitude_deg, c.height_m)),
                    "otdoa-eNB-CellDataList": [{"otdoa-NeighbourCellInfoElement": info, "rtdInfo": encode_rtd(c.rtd)}]}

        return {"schema": SCHEMA, "version": self.version, "valid_from_gps_ns": self.valid_from_gps_ns, "valid_until_gps_ns": self.valid_until_gps_ns,
                "reference": {"referenceCellInfo": ref_info, "positionCalculationInfoRef": calc}, "neighbours": [neighbour(c) for c in self.neighbours]}

    @staticmethod
    def unwrap(raw: dict) -> dict:
        """The IE view of a document read from the envelope: what `_ie_dict` builds (schema, version, validity, reference, neighbours)."""
        if raw.get("schema") != SCHEMA:
            raise ValueError(f"unknown schema {raw.get('schema')!r}")
        delivery = raw["MBMSDelivery"]
        if delivery.get("deliveryMethod") != "broadcast":
            raise ValueError(f"deliveryMethod must be broadcast, not {delivery.get('deliveryMethod')!r}")
        meta = delivery["metadata"]
        ref = None
        neighbours = []
        for t in delivery["textData"]:
            for c in t["contents"]:
                if c["contentId"] == "OMA-LPPe-OTDOA-ReferenceCellInfo" and t["textId"] == "eNBRefInfo":
                    ref = c["content"]
                elif c["contentId"] == "OMA-LPPe-OTDOA-NeighbourCellInfoElement-eNB":
                    neighbours.append(c["content"])
        if ref is None:
            raise ValueError("no OMA-LPPe-OTDOA-ReferenceCellInfo in textData")
        return {"schema": raw["schema"], "version": int(meta["version"]), "valid_from_gps_ns": meta["x_valid_from_gps_ns"], "valid_until_gps_ns": meta["x_valid_until_gps_ns"],
                "reference": ref, "neighbours": neighbours, "x_leap_seconds": meta.get("x_leap_seconds", 18), "x_delivery": delivery}

    @staticmethod
    def from_json(text: str) -> "AssistanceDocument":
        raw = AssistanceDocument.unwrap(json.loads(text))
        rinfo, calc = raw["reference"]["referenceCellInfo"], raw["reference"]["positionCalculationInfoRef"]
        ref_point = calc["cellLocation"]["reference-point"]["referencePointGeographicLocation"]["location3D"]
        ref_lla = decode_point(ref_point)
        prs = decode_prs_info(rinfo["prsInfo"])
        ref = Cell(rinfo["physCellId"], ref_lla[0], ref_lla[1], ref_lla[2], prs, None, rinfo["cpLength"],
                   decode_ecgi(rinfo["cellGlobalId"]) if "cellGlobalId" in rinfo else None)
        neighbours = []
        for el in raw["neighbours"]:
            lla = decode_relative(ref_lla, el["relative-Location"])
            for cd in el["otdoa-eNB-CellDataList"]:       # co-located cells share one location
                i = cd["otdoa-NeighbourCellInfoElement"]
                expected, unc = decode_expected(i["expectedRSTD"], i["expectedRSTD-Uncertainty"])
                neighbours.append(Cell(i["physCellId"], lla[0], lla[1], lla[2], decode_prs_info(i["prsInfo"]) if "prsInfo" in i else prs, decode_rtd(cd["rtdInfo"]),
                                       i.get("cpLength", rinfo["cpLength"]), decode_ecgi(i["cellGlobalId"]) if "cellGlobalId" in i else None, expected, unc,
                                       i.get("slotNumberOffset")))
        sfn = _bits(calc["systemFrameNumber"], 10, "systemFrameNumber") if "systemFrameNumber" in calc else None
        doc = AssistanceDocument(raw["version"], raw["valid_from_gps_ns"], raw["valid_until_gps_ns"], ref, neighbours,
                                 None if sfn is None else int(sfn, 2),
                                 decode_quality(calc["rtdReferenceStd"]) if "rtdReferenceStd" in calc else None)
        delivery = raw["x_delivery"]
        doc.leap_seconds = int(raw["x_leap_seconds"])
        doc.service_id, doc.service_name, doc.broadcast_area = delivery["serviceId"], delivery["serviceName"], delivery.get("broadcastArea")
        interval = delivery["fileSchedule"].get("repeatInterval")
        if interval is not None:
            m = re.fullmatch(r"PT([0-9.]+)S", interval)
            if not m:
                raise ValueError(f"repeatInterval must be of the form PT<seconds>S, not {interval!r}")
            doc.repeat_interval_s = float(m.group(1))
        created = delivery["metadata"].get("createdDate")
        if created is not None:
            t = datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)
            td = t - _GPS_EPOCH
            doc.created_gps_ns = ((td.days * 86400 + td.seconds + doc.leap_seconds) * 1_000_000 + td.microseconds) * 1000
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
