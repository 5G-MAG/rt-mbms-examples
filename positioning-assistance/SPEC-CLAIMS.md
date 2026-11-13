# Specification claims

Baseline: the latest Release 19 text of each document available on 2026-11-13, fetched from the 3GPP archive. The quotations were checked against
local copies of those documents with the 5G-MAG citation checker (`verify-citations.py --strict`); the documents themselves are not part of this repository.
Nothing pins a version for this prototype yet; Release 19 was chosen by the owner, and the newest issue of each document was taken.

| Document | Version | Used for |
|---|---|---|
| TS 36.331 | V19.4.0 (2026-09) | SIB16 time, TimeReferenceInfo |
| TS 36.211 | V19.3.0 (2026-03) | PRS occasions |
| TS 36.214 | V19.0.0 (2025-09) | RSTD definition |
| TS 36.133 | V19.6.0 (2026-09) | RSTD reporting resolution |
| TS 37.355 | V19.3.0 (2026-06) | LTE OTDOA assistance data and capability (TS 36.355 V19.0.0 is the stub that points here) |
| TS 26.346 | V19.3.0 | announcement of a generic application service (delivery stage, not used by the code yet) |
| TS 26.348 | V19.0.0 | xMB Files session properties (delivery stage, not used by the code yet) |

Each entry is source-derived (quotation and version). What the code does with it is code-derived and named in the last column.

## Time reference (SIB16)

- TS 36.331 V19.4.0 clause 6.3.2: "If the time field is included in SystemInformationBlockType16 and the referenceSFN field is not included, the time field indicates the time at the SFN boundary at or immediately after the ending boundary of the SI-window in which SystemInformationBlockType16 is transmitted." [C1] Code: `TimeAnchor` in `timebase.py`.
- TS 36.331 V19.4.0 clause 6.3.2: "The indicated time is referenced at the network, i.e., without compensating for RF propagation delay." [C2] Code: `ue_time_ns` adds the delay back.
- TS 36.331 V19.4.0 clause 6.3.2: "This field indicates time reference with 0.25 us granularity." [C3] Code: `sib16_r15_time_to_ns` works in 250 ns units.
- TS 36.331 V19.4.0 clause 6.3.2: "The field is mandatory present if TimeReferenceInfo is included in DLInformationTransfer message; otherwise the field is not present." The condition is on `referenceSFN`. So in SIB16 there is no referenceSFN; the anchor is the SI-window rule in entry 1. **The deck (slide 12) describes referenceSFN as part of the SIB16 timing; the text above does not support that.** [C4]
- TS 36.331 V19.4.0 clause 6.3.1: "GPS Time (in seconds) = timeInfoUTC (in seconds) - 2,524,953,600 (seconds) + leapSeconds". [C5] Code: `sib16_r11_to_gps_ns`, test vector in `tests/test_bps.py`.

The unit formula of `time` (refDays, refSeconds, refMilliSeconds, refQuarterMicroSeconds) and the origin rule were read from the same field description; the sentence has no unbroken quotation short enough to check here, see the test `test_r15_unit_arithmetic`.

## PRS

- TS 36.211 V19.3.0 clause 6.10.4.3: "Positioning reference signals are transmitted only in configured DL subframes." The occasion condition (10 n_f + floor(n_s/2) - Delta_PRS) mod T_PRS = 0 and Table 6.10.4.3-1 are equations and a table whose symbols the text export drops; they were read from the rendered pages 174 and 175 of the V19.3.0 document. [C6] Code: `prs_period_offset`, `is_prs_occasion_start`; tests cover one index per table row.
- TS 36.211 V19.3.0 clause 6.10.4: "If both normal and MBSFN subframes are configured as positioning subframes within a cell, the OFDM symbols in a MBSFN subframe configured for positioning reference signal transmission shall use the same cyclic prefix as used for subframe #0." PRS in MBSFN subframes is therefore described. Whether it is allowed on an MBMS-dedicated carrier is **not established** by this clause. [C7]

## RSTD

- TS 36.214 V19.0.0 clause 5.1.12: "TSubframeRxi is the time when the UE receives the corresponding start of one subframe from cell i that is closest in time to the subframe received from cell j." [C8] Code: RTD wrapped to [-0.5, +0.5) ms in `AssistanceDocument.validate` and the RTD monitor.
- TS 36.133 V19.6.0 clause 9.1.10.3: "The reporting range of RSTD is defined from -15391Ts to 15391Ts with 1Ts resolution for absolute value of RSTD less or equal to 4096Ts and 5Ts for absolute value of RSTD greater than 4096Ts." [C9] Code: `quantise_rstd_ts` in `sim.py`. The same clause family has a higher-resolution report (0.5 Ts, clause 9.1.10.4) that is not simulated.
- TS 37.355 V19.3.0 clause 6.5.1.2: "The RSTD value can be negative and is calculated as (expectedRSTD-8192). The resolution is 3Ts, with Ts=1/(15000*2048) seconds." [C10] Code: `expected_rstd_field`, `TS_S`.
- TS 37.355 V19.3.0 clause 6.5.1.2: "The slot number offsets and expected RSTDs in OTDOA-NeighbourCellInfoList are provided relative to the cell defined by this IE." [C11] Code: one reference cell per document.
- TS 37.355 V19.3.0 clause 6.5.1.1: "The location server should include at least one cell for which the SFN can be obtained by the target device, e.g. the serving cell, in the assistance data, either as the assistance data reference cell or in the neighbour cell list." The reference cell must be one whose SFN the UE can decode. [C12]
- TS 37.355 V19.3.0 clause 6.5.1.7: "Bit 0 indicates that the target device supports UE-assisted OTDOA and LTE PRS." The `otdoa-Mode` bit string of V19.3.0 has only UE-assisted values, so LPP defines no UE-based LTE OTDOA mode; the UE-based processing in this prototype is outside LPP, as the deck says. [C13]

## Delivery stage (not yet in the code)

- TS 26.346 V19.3.0 clause 5.2.2.1: "If a generic application service is delivered, at least one of the delivery methods shall be the download delivery method." [C14]
- TS 26.346 V19.3.0 clause 7.6.2.3: "The presence of the r12:broadcastAppService and/or r12:unicastAppService element under deliveryMethod signifies that the parent MBMS User Service is an application service which contains resources delivered via broadcast and/or unicast modes." [C15]
- TS 26.348 V19.0.0 clause 5.4.6: "In the Push mode, the file list is not used since the BM-SC will monitor its push folder and send the files it receives on a first-come first-served basis." [C16]

- TS 36.331 V19.4.0 clause 5.2.3b: "the SI-window starts always at the subframe #a, where a = x mod 10, in the radio frame for which SFN mod T = FLOOR(x/10), where T is the si-Periodicity of the concerned SI message;" [C17] Code (outside this repository): rt-mbms-tx `rrc::prepare_bcch_dlsch` and rt-mbms-modem `Rrc.cpp` SIB16 handler, both with x = (n - 1)*w and n the order of entry in the SIB1-MBMS schedulingInfoList.

- TS 36.211 V19.3.0 clause 6.6.4.1: "nf mod 4 = 0 for 25≤NRBDL;" [C18] The cell acquisition subframe of an MBMS-dedicated cell is subframe 0 of these frames ("nf mod 8 = 4 for 6<NRBDL<25" below 25 PRB). Code (outside this repository): rt-mbms-tx `si_window.h` and its start-up check, the scheduler fix.

## Not verified, and why

- **OMA LPPe (OMA-TS-LPPe-V1_0 and later):** could not obtain it (the OMA release area is a script-rendered page that lists nothing, a search found only citations of it). So the units and semantics of `rtdInfo` (`subframeOffset`, `fineRTD`, `fineRTDstd`, `fineRTDdriftRate`) and of the relative-location IE are unknown. The deck shows their names and ranges on slide 29; the `Rtd` and position blocks of the assistance document are this prototype's own and carry no clause.
- **Sample-clock anchor:** the SI-window arithmetic (clause 5.2.3b) is implemented in rt-mbms-tx and rt-mbms-modem, and `bps/live.py` consumes it, but the modem does not yet tie the boundary frame to its sample count (SdrReader ignores the receive timestamp), so the UE-side anchor is the host clock at decode time, lab grade.
- **PRS on an MBMS-dedicated (FeMBMS) carrier:** not established, see entry 7.
- **PRS generation and ToA estimation:** no code exists in rt-mbms-tx or rt-mbms-modem (searched `prs`, `otdoa`, `rstd`, `toa`).
- **UE accuracy requirements** (TS 36.133 clause 9.1.10.1 and .2) were not read; the simulator's noise levels are free parameters, not requirement values.
