"""Time handling for broadcast positioning over an LTE-based 5G Broadcast cell.

Everything marked [spec] restates a clause listed in SPEC-CLAIMS.md (baseline: Release 19 texts).
Everything marked [design] is this prototype's own choice and has no clause behind it.

Times are integers: nanoseconds since the GPS epoch (1980-01-06T00:00:00) unless stated, so that the
0.25 microsecond granularity of SIB16 timeReferenceInfo is exact (250 ns).
"""
from __future__ import annotations

from dataclasses import dataclass

# [spec] TS 37.355 V19.3.0 clause 6.5.1.2: "Ts=1/(15000*2048) seconds".
TS_S = 1.0 / (15000 * 2048)
C_MPS = 299_792_458.0  # speed of light in vacuum, defined constant (not from a 3GPP clause)

SUBFRAME_NS = 1_000_000
FRAME_SUBFRAMES = 10
SFN_MODULUS = 1024

# [spec] TS 36.331 V19.4.0 clause 6.3.1, NOTE 1 of timeInfoUTC: 2,524,953,600 s from 1900-01-01 to GPS epoch.
SECONDS_1900_TO_GPS_EPOCH = 2_524_953_600


# ----------------------------------------------------------------------------------------------
# PRS occasions: TS 36.211 V19.3.0 clause 6.10.4.3, Table 6.10.4.3-1
# ----------------------------------------------------------------------------------------------
# (first index, last index, T_PRS in subframes); Delta_PRS = I_PRS - first index  [spec] (read from the
# rendered table, page 175 of the V19.3.0 document; the text export drops the equations).
_PRS_TABLE = (
    (0, 159, 160),
    (160, 479, 320),
    (480, 1119, 640),
    (1120, 2399, 1280),
    (2400, 2404, 5),
    (2405, 2414, 10),
    (2415, 2434, 20),
    (2435, 2474, 40),
    (2475, 2554, 80),
)


def prs_period_offset(i_prs: int) -> tuple[int, int]:
    """Return (T_PRS, Delta_PRS) in subframes for a PRS configuration index. Indices 2555..4095 are reserved."""
    for first, last, period in _PRS_TABLE:
        if first <= i_prs <= last:
            return period, i_prs - first
    raise ValueError(f"PRS configuration index {i_prs} is reserved or out of range")


def is_prs_occasion_start(sfn: int, subframe: int, i_prs: int) -> bool:
    """[spec] first subframe of an occasion satisfies (10*n_f + floor(n_s/2) - Delta_PRS) mod T_PRS = 0.

    floor(n_s/2) is the subframe number within the frame, so `subframe` (0..9) is used directly.
    """
    if not (0 <= sfn < SFN_MODULUS and 0 <= subframe < FRAME_SUBFRAMES):
        raise ValueError("sfn must be 0..1023 and subframe 0..9")
    period, delta = prs_period_offset(i_prs)
    return (FRAME_SUBFRAMES * sfn + subframe - delta) % period == 0


def next_prs_occasion_start(abs_subframe: int, i_prs: int) -> int:
    """Smallest absolute subframe >= abs_subframe that starts a PRS occasion.

    `abs_subframe` is an unwrapped subframe counter whose value modulo 10240 is 10*SFN + subframe.
    [spec-derived] T_PRS divides 10240 for every table entry, so the occasion pattern repeats exactly at
    the SFN wrap; test_timebase checks this for the whole table.
    """
    period, delta = prs_period_offset(i_prs)
    return abs_subframe + ((delta - abs_subframe) % period)


# ----------------------------------------------------------------------------------------------
# SIB16 time -> GPS nanoseconds
# ----------------------------------------------------------------------------------------------
def sib16_r11_to_gps_ns(time_info_utc: int, leap_seconds: int) -> int:
    """[spec] timeInfoUTC counts UTC seconds in 10 ms units since 1900-01-01; GPS = UTC - 2,524,953,600 + leapSeconds.

    Result resolution is 10 ms (the field's granularity).
    """
    if not 0 <= time_info_utc <= 549_755_813_887:
        raise ValueError("timeInfoUTC out of range")
    utc_ns = time_info_utc * 10_000_000
    return utc_ns - SECONDS_1900_TO_GPS_EPOCH * 1_000_000_000 + leap_seconds * 1_000_000_000


def sib16_r15_time_to_ns(ref_days: int, ref_seconds: int, ref_ms: int, ref_quarter_us: int) -> int:
    """[spec] time in 0.25 us units = refDays*86400*1000*4000 + refSeconds*1000*4000 + refMilliSeconds*4000 + refQuarterMicroSeconds.

    Returns nanoseconds from the field's origin. The origin is the GPS epoch unless timeInfoType is localClock, in
    which case its meaning is left to upper layers (the caller must then not treat the result as GPS time).
    """
    if not (0 <= ref_days <= 72999 and 0 <= ref_seconds <= 86399 and 0 <= ref_ms <= 999 and 0 <= ref_quarter_us <= 3999):
        raise ValueError("ReferenceTime-r15 field out of range")
    units = ref_days * 86400 * 1000 * 4000 + ref_seconds * 1000 * 4000 + ref_ms * 4000 + ref_quarter_us
    return units * 250


def gps_ns_to_sib16_r15_fields(gps_ns: int) -> tuple[int, int, int, int]:
    """Inverse of sib16_r15_time_to_ns, truncating to the 0.25 us granularity (used by the simulator)."""
    units = gps_ns // 250
    ref_days, rem = divmod(units, 86400 * 1000 * 4000)
    ref_seconds, rem = divmod(rem, 1000 * 4000)
    ref_ms, ref_quarter_us = divmod(rem, 4000)
    return ref_days, ref_seconds, ref_ms, ref_quarter_us


# ----------------------------------------------------------------------------------------------
# Anchoring the cell's frame grid to UTC [design, using the [spec] meaning of the SIB16 time]
# ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TimeAnchor:
    """Network-referenced GPS time of the start of one absolute subframe of one cell.

    [spec] TS 36.331 clause 6.3.2: for SIB16 without referenceSFN, "the time field indicates the time at the SFN
    boundary at or immediately after the ending boundary of the SI-window in which SystemInformationBlockType16 is
    transmitted", "referenced at the network, i.e., without compensating for RF propagation delay".
    [design] `abs_subframe` is the UE's unwrapped subframe counter at that SFN boundary (the modem must supply it;
    it is the SI-window arithmetic of TS 36.331 clause 5.2.3, not implemented here).
    """

    abs_subframe: int
    gps_ns: int  # time at the network at the START of abs_subframe

    def tx_time_ns(self, abs_subframe: int) -> int:
        """Network-referenced time of the start of an absolute subframe of the same cell."""
        return self.gps_ns + (abs_subframe - self.abs_subframe) * SUBFRAME_NS
