# Specification claims

Baseline: the latest Release 19 text of each document available on 2026-11-13, fetched from the 3GPP archive. The quotations were checked against
local copies of those documents with the 5G-MAG citation checker (`verify-citations.py --strict`); the documents themselves are not part of this repository.
Nothing pins a version for this prototype yet; Release 19 was chosen by the owner, and the newest issue of each document was taken.

| Document | Version | Used for |
|---|---|---|
| TS 36.331 | V19.4.0 (2026-09) | SIB16 time, TimeReferenceInfo |
| TS 36.211 | V19.3.0 (2026-03) | PRS occasions, sequence and mapping; PSS and SSS |
| TS 36.214 | V19.0.0 (2025-09) | RSTD definition |
| TS 36.133 | V19.6.0 (2026-09) | RSTD reporting resolution; which signals RSTD requirements are written for; accuracy conditions |
| TS 37.355 | V19.3.0 (2026-06) | LTE OTDOA assistance data and capability (TS 36.355 V19.0.0 is the stub that points here) |
| TS 36.305 | V19.0.0 | read for MBMS-dedicated cells and OTDOA (no sentence found) |
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

## PRS waveform (`bps/prs.py`)

The sequence, the mapping and the Gold generator are equations in the specification; the text export drops their symbols, so they were read from the rendered
pages of TS 36.211 V19.3.0 (file pages 75 and 76 and page 101 of `36211-j30_s06-s08.pdf`). The sentences quoted here are the ones that survive the export.

- TS 36.211 V19.3.0 clause 6.10.4: "Positioning reference signals are transmitted on antenna port 6." [C19] Code: `prs_slot_grid` has one port.
- TS 36.211 V19.3.0 clause 6.10.4: "allocated to the core part of the PBCH, PSS or SSS regardless of their antenna port" (the sentence says PRS shall not be mapped to those resource elements). [C20] Code: `sync_pbch_core_mask` and the last line of `prs_slot_grid`, with the pattern of an MBMS-dedicated cell (5G Broadcast), below.
- TS 36.211 V19.3.0 clause 6.11.1.2, MBMS-dedicated cell: "the primary synchronization signal shall be transmitted in slot 0 in subframes fulfilling" n_f mod 4 = 0 "only" (condition on the rendered page, document page 186). The resource elements are k = n - 31 + N_RB^DL N_sc^RB / 2, n = 0..61, in the last OFDM symbol. There is no PSS in slot 10, so none in subframe 5. [C24] Code: the PSS rows of the mask.
- TS 36.211 V19.3.0 clause 6.11.2.2, MBMS-dedicated cell: the SSS uses the same k, in OFDM symbol N_symb^DL - 2 of the slots where the PSS is transmitted (a row of the equation, read from the rendered page, document page 189; not a sentence of the text, so not quoted). The sentence "are reserved and not used for transmission of the secondary synchronization signal" names n = -5..-1 and 62..66 as reserved; whether "allocated to" in clause 6.10.4 includes them is **not decided by the text**, so they are not excluded (they carry no signal). [C25] Code: the SSS rows of the mask.
- TS 36.211 V19.3.0 clause 6.6.4: "for an MBMS-dedicated cell, be transmitted during 4 consecutive radio frames fulfilling" n_f mod 4 = 0, "starting in each radio frame fulfilling" n_f mod 16 = 0 (rendered page, document page 127); the core set is k = N_RB^DL N_sc^RB / 2 - 36 + k', k' = 0..71, l = 0..3 in slot 1 of subframe 0. Read here as: the core set is present in every frame with n_f mod 4 = 0. [C26] Code: the PBCH rows of the mask.
- TS 36.211 V19.3.0 clause 6.6.4: "For an MBMS-dedicated cell configured with repetition, the physical broadcast channel shall be repeated as described in clause 6.6.4.1." and "For an MBMS-dedicated cell configured with CAS muting, the physical broadcast channel shall only be transmitted in the first" frames set by cas-MutingConfig. [C27] Code: **not implemented**; the mask is the pattern without repetition and without CAS muting. A cell configured with either has more or fewer PBCH core elements than the mask removes.
- **PRS on a 5G Broadcast carrier** (C28 to C32). Read on 2026-11-14 from the rendered pages 98 and 102 of TS 36.211 V19.3.0 and from TS 36.331 V19.4.0:
  - [C28] TS 36.211 V19.3.0 clause 6.10.4: "Positioning reference signals are defined for" a subcarrier spacing of 15 kHz only (the symbol is on the rendered page).
  - [C29] TS 36.211 V19.3.0 clause 6.1: "For an MBMS-dedicated cell, subframes where PSS/SSS/PBCH or PDSCH carrying system information are transmitted with" a 15 kHz spacing "are non-MBSFN subframes." The other subframes of such a cell are MBSFN subframes.
  - [C30] TS 36.331 V19.4.0 clause 6.3.7, field description of subcarrierSpacingMBMS: "EUTRAN configures parameter subcarrierSpacingMBMS only when the MBSFN subframes have subcarrier spacing other than 15 kHz". Without it the MBSFN subframes are 15 kHz.
  - [C31] Code-derived: the demo carrier is `mbms_dedicated = true`, `carrier_type = "fembms_ded"`, and its MBSFN area configures `subcarrier_spacing = "khz1dot25"` (`scripts/tmux/mbms-broadcast-tutorial/conf/sib.conf.mbsfn`, `enb_baseline.conf`). So its only 15 kHz subframes are the cell acquisition subframes (subframe 0 of the frames with n_f mod 4 = 0 for 25 PRB, C18).
  - **Reading (mine, not a sentence of the specification):** on this carrier PRS can only sit in the cell acquisition subframes, so T_PRS must be a multiple of 40 subframes, Delta_PRS a multiple of 40 and N_PRS = 1 (Table 6.10.4.3-1, clause 6.10.4.3). Index 2435 (T_PRS 40, Delta_PRS 0) is one such configuration, and the lab uses it for every cell (`lab.PRS_INDEX`, tested by `LabPrsConfiguration`). Those subframes carry the PSS, SSS and PBCH core set, which is why the exclusion above matters here. An earlier version of the lab used indices 2401 to 2404 (subframes 1 to 4), which are 1.25 kHz subframes on this carrier: that was wrong and is replaced.
  - [C32] TS 36.211 V19.3.0 clause 6.10.4: "If only MBSFN subframes are configured as positioning subframes within a cell, the OFDM symbols configured for positioning reference signals in the MBSFN region of these subframes shall use extended cyclic prefix length." This describes PRS in 15 kHz MBSFN subframes. A carrier whose MBSFN subframes are 15 kHz (the SIB13 test plan tried that) would offer them; not modelled.
  - **Not established:** that an MBMS-dedicated cell may transmit PRS at all. Neither TS 36.211 clause 6.10.4 nor TS 37.355 V19.3.0 says so or excludes it (TS 37.355 mentions MBSFN only in `sameMBSFNconfigRef` and `sameMBSFNconfigNeighbour`). The deck assumes it ("It is assumed that LTE PRSs will be used for 5G BPS", slide 11) and notes that PSS/SSS could be used where the cell acquisition subframe is not SFN'ed (slide 13, note 1). That is an assumption of the proposal. Whether a UE may be configured for OTDOA on such a carrier, and whether the PRS of several cells may share a cell acquisition subframe in an SFN, is not covered by the text read.
- TS 36.211 V19.3.0 clause 6.10.4.1: "unless configured by higher layers and where" (the sentence before it says the generator is initialised at the start of each OFDM symbol with c_init, where N_ID^PRS equals N_ID^cell unless configured by higher layers). The expression of c_init and of r(m) are read from the rendered page. [C21] Code: `prs_c_init`, `prs_sequence`; the laboratory world uses the cell identity as N_ID^PRS.
- TS 36.211 V19.3.0 clause 6.10.4.2: "If PRS frequency hopping is not configured by higher layers, the reference signal sequence" (is mapped as in the equation read from the page). [C22] Code: `prs_slot_grid`, which implements the no-hopping case only; PRS frequency hopping is not implemented.
- TS 36.211 V19.3.0 clause 7.2: "Pseudo-random sequences are defined by a length-31 Gold sequence." The recurrences, Nc = 1600 and the initialisation are read from the rendered page. [C23] Code: `gold_sequence`, compared bit for bit with srsRAN's `srsran_sequence_LTE_pr` for four seeds in `tests/test_prs.py`.

What is this project's own design, not specification: the OFDM synthesis (FFT size per bandwidth, normal cyclic prefix lengths scaled from 2048 points, DC unused), the channel (one path per cell, white noise), the correlation ToA estimator with a parabola on a 16-times upsampled peak, and the weight given to each RSTD.

## PSS and SSS time of arrival (`bps/sync.py`)

The alternative of slide 13, note 1, of the proposal: where the cell acquisition subframes are not SFN'ed, the time of arrival of each cell is taken from its PSS and SSS instead of from PRS. The sequences are equations; they were read from the
rendered pages 185 to 188 of TS 36.211 V19.3.0 and checked against srsRAN (all 504 cell identities, both SSS variants) and Table 6.11.2.1-1 (all 168 groups). The sentences quoted here are the ones that survive the export.

- TS 36.211 V19.3.0 clause 6.11: "There are 504 unique physical-layer cell identities." The identity is N_ID^(cell) = 3 N_ID^(1) + N_ID^(2) (equation on the rendered page). [C33] Code: `split_cell_id`.
- TS 36.211 V19.3.0 clause 6.11.1.1: "used for the primary synchronization signal is generated from a frequency-domain Zadoff-Chu sequence" with the root indices of Table 6.11.1.1-1 (25, 29, 34 for N_ID^(2) = 0, 1, 2) and the two expressions of d(n) read from the rendered page. [C34] Code: `pss_sequence`.
- TS 36.211 V19.3.0 clause 6.11.2.1: "is an interleaved concatenation of two length-31 binary sequences" and "The concatenated sequence is scrambled with a scrambling sequence given by the primary synchronization signal." The m-sequences, the shifts and the two subframe variants are read from the rendered page. [C35] Code: `sss_sequence`, `sss_indices`.
- TS 36.211 V19.3.0 clauses 6.11.1.2 and 6.11.2.2, MBMS-dedicated cell: the PSS in the last OFDM symbol and the SSS in the one before it, in slot 0 of the frames with n_f mod 4 = 0, on k = n - 31 + N_RB^DL N_sc^RB / 2, n = 0..61 (C24, C25). [C36] Code: `sync_slot_grid`; its resource elements are the ones `bps.prs.sync_pbch_core_mask` keeps PRS clear of (tested).
- TS 36.214 V19.0.0 clause 5.1.12 defines the RSTD through "the time when the UE receives the start of one subframe", without naming the signal it is taken from. The time difference of subframe starts derived from the PSS and SSS is the same quantity. TS 36.133 clause 8.1.2.5 was read afterwards: C38. [C37]

What is this project's own design: the waveform (slot 0 only, no PBCH, no cell-specific reference signals), the channel (one path per cell, white noise, no frequency offset), the correlation estimator with interference cancellation (strongest cell first, subtract, repeat, two refinement passes), and the weights (`SYNC_TOA_SIGMA_NS`, from `python3 -m bps.compare sigma`). Not modelled: the cell-specific reference signals of the cell acquisition subframe, which would add bandwidth; a UE that uses them would do better.

## Which signals a UE measures RSTD on, and the cyclic prefix per cell (read 2026-11-14)

Read for the alternative of slide 13, note 1 (PSS/SSS), and for the per-cell cyclic prefix of the assistance document: TS 36.133 V19.6.0, TS 37.355 V19.3.0, TS 36.331 V19.4.0, TS 36.305 V19.0.0, and TS 36.355 V19.0.0 (a stub that points to TS 37.355, so searched but not informative) of `Downloads/3GPP-specs-pnt-rel19/specs`).

- TS 36.133 V19.6.0 clause 8.1.2.5: "When using CRS, in addition to PRS, is enabled in the OTDOA assistance data, it is up to UE implementation whether to use or not the CRS for RSTD measurements, but in either case the RSTD measurements reported by the UE shall meet the requirements specified in this section." [C38] So the RSTD requirements are written for PRS, with the CRS as an option. The PSS and SSS are not named for RSTD there. **Consequence:** the PSS/SSS alternative (`bps/sync.py`) is this proposal's own, outside the measurement the specification defines requirements for.
- TS 36.133 V19.6.0 clause 9.1.10.1, Table 9.1.10.1-1, row for 25 RB: "6 | (PRS Ês/Iot)ref ≥-6dB / and / (PRS Ês/Iot)i ≥-13dB |  25 | ≥ 2" (accuracy 6 Ts, at least 2 measurement subframes). [C39] With Ts = 1/30.72 microsecond that is 195 ns at 25 PRB. The lab figures (about 10 ns per RSTD, one path, no fading) are not requirement values and must not be read as such.
- TS 36.133 V19.6.0 clause 9.1.10.1: "The parameter expectedRSTDUncertainty signalled over LPP by E-SMLC as defined in TS 36.355 [24] is less than 5 µs." [C40] A condition of the accuracy requirement; the document of this prototype carries no such parameter.
- TS 37.355 V19.3.0 clause 6.5.1.2, field description of the reference cell: "cpLength / This field specifies the cyclic prefix length of the assistance data reference cell PRS if the prsInfo field is present, otherwise this field specifies the cyclic prefix length of the assistance data reference cell CRS." [C41] The neighbour cell has the same field: "cpLength / This field specifies the cyclic prefix length of the neigbour cell PRS if PRS are present in this neighbour cell, otherwise this field specifies the cyclic prefix length of CRS in this neighbour cell." (sic, "neigbour"). **So the cyclic prefix is stated per cell**, which is what `Cell.cp_length` carries (absent: the serving cell's, the prototype's reading). Code: `bps/assistance.py` `Cell.cp_length`, rt-mbms-modem `PrsToa::CellInfo::cp`.
- TS 37.355 V19.3.0 clause 6.5.1.2: "sameMBSFNconfigRef / This field indicates whether the MBSFN subframe configuration of the assistance data reference cell is the same as the current primary cell of the target device." [C42] Not carried by the prototype.
- **Absence claims** (searched by me, whole documents, case-insensitive): "MBMS-dedicated", "MBMS dedicated" and "FeMBMS" have no hit in TS 36.305 V19.0.0 or TS 37.355 V19.3.0 (0 hits each); "MBMS-dedicated" and "FeMBMS" have no hit in clauses 8.1.2.5 and 9.1.10 of TS 36.133 V19.6.0 (it has "FeMBMS" elsewhere, in the RRM requirements); in TS 36.331 V19.4.0 none of the 11 lines with "OTDOA" mentions MBMS. So no sentence read allows or forbids PRS, or sets RSTD requirements, for an MBMS-dedicated cell. This is a negative result of a keyword search, not a reading of every clause.

What is this project's own design: using PSS and SSS as the time-of-arrival signal (it is not what the RSTD requirements cover), and reading an absent `cp_length` as the serving cell's.

## Not verified, and why

- **OMA LPPe (OMA-TS-LPPe-V1_0 and later):** could not obtain it (the OMA release area is a script-rendered page that lists nothing, a search found only citations of it). So the units and semantics of `rtdInfo` (`subframeOffset`, `fineRTD`, `fineRTDstd`, `fineRTDdriftRate`) and of the relative-location IE are unknown. The deck shows their names and ranges on slide 29; the `Rtd` and position blocks of the assistance document are this prototype's own and carry no clause.
- **Sample-clock anchor:** the SI-window arithmetic (clause 5.2.3b) is implemented in rt-mbms-tx and rt-mbms-modem, and `bps/live.py` consumes it, but the modem does not yet tie the boundary frame to its sample count (SdrReader ignores the receive timestamp), so the UE-side anchor is the host clock at decode time, lab grade.
- **PRS on an MBMS-dedicated (FeMBMS) carrier:** not established; what the text does say is in C28 to C32.
- **PRS generation and ToA estimation in the stack:** rt-mbms-tx `feature/prs-transmit` generates PRS and rt-mbms-modem `feature/prs-toa` measures it (both local, unpushed, and checked against the golden vectors of `tools/gen_prs_golden.py`); `bps/prs.py` is the Python reference and the software model of the UE processing. Neighbouring cells are emulated by `bps/rig.py`.
- **UE accuracy requirements:** clause 9.1.10.1 was read (C39, C40); the simulator's noise levels are free parameters, not requirement values. Clause 9.1.10.2 (inter-frequency) was not read.
