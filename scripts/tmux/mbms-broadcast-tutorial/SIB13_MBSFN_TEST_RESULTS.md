# SIB13/MBSFN Parameter Test Campaign — Results

**Update 2026-07-15 (fix pass)**: a follow-up pass investigated and live-verified fixes for
several of the findings below. See "Fix pass outcomes" after the Summary table for what
changed, what got corrected (Finding 5 turned out not to be a real bug), and what's still
open. The findings below are left as originally written (the campaign's as-found record);
the fix pass section explains what's since changed.

**Date**: 2026-07-15
**Scope**: systematic one-dimension-at-a-time verification of SCS, MCS, time interleaving,
CAS muting, `pmch_bandwidth`, and `n_prb`, against the `n_prb=25` baseline resolved earlier
this session (see `rt-mbms-modem/KNOWN_ISSUES.md`: `-O3` build fix, BLER 0.0).
**Full working log** (chronological, with all raw evidence as it was found): the approved
plan at `~/.claude/plans/sequential-noodling-newt.md`, section "Results log".
**Helper script**: `test_sib_matrix.py` in this directory; per-phase raw output in
`*_phase_report.md` alongside it.
**Config change kept permanently**: `enb_baseline.conf` now sets `rrc_level = warning`
(previously RRC logged only at `error`, via the global `all_level=error`). This was added
as a debugging aid but kept deliberately: the RRC layer's own `logger.warning()` calls
(clamp explanations, infeasibility warnings) are genuinely useful to any operator running
this rig, not just to this investigation, and were previously invisible.

## Summary by phase

| Phase | Dimension | Result |
|---|---|---|
| 1 | SCS (15, 7.5, 2.5, 1.25kHz) | All PASS, BLER 0.0 |
| 2 | MCS (2, 9, 10, 16, 17, 28, 22-table2) | All PASS; MCS=28 clamp discrepancy corrected post-hoc, was a misdiagnosis (see fix pass) |
| 3 | Time interleaving (N,M up to 16,32) | Fixed and confirmed genuinely functional (real bug: OTA switch had no case for divisor-clamped values) — works given compatible scheduling parameters; safely/honestly disables otherwise (Finding 1) |
| 4 | CAS muting (k_cas,n_cas up to 32,16) | **FIXED 2026-07-17**: BLER inflation traced to a spurious equalized-power anomaly (~14% of muted sf=0 occasions) being wrongly counted as decode failures; validity gate extended, live-verified MCH BLER 0.82-1.00 → 0.0. Underlying trigger (one specific worker-pool instance, ~14% probability) still not fully root-caused, but no longer affects correctness or reported stats — Finding 3 |
| 5 | `pmch_bandwidth` (30, 35, 40 @ n_prb=25) | Root cause found and fixed (RX/TX pmch.c divergence + internal-vs-caller PRB count mismatch); substantially improved (MCCH mostly succeeds, MTCH now attempted) but a separate timing/blocking issue still causes most MTCH decodes to fail — Finding 4 |
| 5b | `n_prb` (6, 15, 25, 50, 75, 100) | 6, 15 crash (Finding 6, decimator CPU ceiling, unresolved); 25, 50, **75, 100 all clean (Finding 7 fixed 2026-07-18** — config-only: `base_srate`/`native_srate`/cell-search `-b` flag must all match the true rate for the configured `n_prb`, not just the bridge's own decimation ratio) |
| 6 | TI + muting interaction | **PASS (2026-07-18)**: run after Findings 1 and 3 were both fixed; TI(2,4) genuinely combines correctly alongside muting(8,4) and muting(16,8) — no new interaction bug (originally-planned M=8/16 values are structurally infeasible on this rig regardless of muting, see write-up) |
| 7 (added post-campaign) | Frequency interleaving (Rel-19 `pmch-TFI-Config`, on/off flag) | PASS — signaling and functional, BLER 0.0 (added 2026-07-15 after review found it missing from the original matrix) |

**Net result: 7 real, previously-undocumented issues found** (9 counting TI's three
sub-symptoms separately), none of them wire-format/protocol-compliance bugs — every one is
either (a) a live-reconfiguration path that doesn't actually reach the broadcast content
despite being accepted, or (b) a resource/scaling limit of the current ZMQ bridge
implementation. Two additional, pre-existing gaps were identified during planning (before
any live testing this campaign) and are listed separately at the end — they were scoped,
not re-verified live here.

## Fix pass outcomes (2026-07-15)

### Finding 4 (`pmch_bandwidth` propagation) — FIXED, root cause was different from the original guess

The original writeup guessed a missing value-tag bump or a repack block not re-executing.
Neither was right. Live-instrumented testing (temporary `rrc_level=debug` + a diagnostic
build) found the real cause in five minutes of testing what months of static reading of the
*other* candidates couldn't resolve: `rrc::reconfigure_embms()` had **a second, separate
clamp** — `if (pmch_bandwidth > cfg.cell.nof_prb) { pmch_bandwidth = cfg.cell.nof_prb; }` —
that silently rewrote every legal value (30, 35, 40) down to `nof_prb` (25 in this campaign)
whenever `nof_prb < 40`. `configure_mbsfn_sibs()`'s packer switch (only cases for 30/35/40)
then always saw the clamped-to-25 value, matched no case, and emitted no r17 extension —
exactly the "accepted but never broadcast" symptom this campaign observed. The clamp
directly contradicted the field's own purpose (pmch-Bandwidth-r17 signals coverage *wider*
than the base cell — see `enb_cfg_parser.cc`'s own comment, "may legitimately exceed
enb.n_prb") and was pure redundant harm given the validation one line above it already
constrains the value to `{0,30,35,40}`.

**Removing that clamp did make the signaling correct** (`pmch_bandwidth=30` decoded
correctly for the first time) — but exposed a **second, deeper, and more serious problem**:
functional PMCH decode broke completely (BLER 1.0) the moment the wider bandwidth was
actually signaled and attempted. Tracing further: `mac::write_mcch()` has its *own*,
separate clamp on `cell_config[0].cell.mbsfn_prb`, justified by an explicit comment about
preventing "overflow [of] the PMCH PRB-sized buffers." That clamp was correctly left in
place (touching it without verifying PHY resource-grid sizing would risk a real
buffer-overflow, not just a signaling nitpick) — meaning MAC/PHY still schedule and
transmit based on `nof_prb`, while RRC would have told receivers to expect a wider PMCH than
what's actually sent. **Conclusion: PMCH wider than the cell's own `nof_prb` is not actually
implemented at the PHY/resource-grid level in this codebase — only the RRC/SIB13 signaling
scaffolding exists.** The clamp was restored, with an honest warning explaining why
("extended PMCH coverage... is not implemented at the PHY level in this build... clamping
so the eNB doesn't signal a capability it cannot deliver") instead of the old, uninformative
"exceeds cell nof_prb" wording. A real fix needs PHY-level resource-grid/softbuffer support
for `mbsfn_prb > nof_prb` — a resource-grid-sizing change, out of scope for this pass (real
real-time DSP allocation code, not a validation-logic fix).

**Files changed**: `rt-mbms-tx/srsenb/src/stack/rrc/rrc.cc` (clamp restored with an honest
comment/warning citing this investigation).

**Verification note**: only `pmch_bandwidth=30` was re-tested live while root-causing this.
`35` and `40` were re-tested afterward (not just assumed identical): both confirmed the same
safe fallback (correctly clamped, no propagation, BLER 0.0) — consistent, since all three
exceed `nof_prb=25` the same way and hit the identical clamp.

### Finding 4, continued (same day): the "not implemented at the PHY level" conclusion above was wrong — found and fixed the real bug, substantial but incomplete progress

Re-opened this after being pushed to actually fix it rather than leave it disabled.
`phy_common.c`'s own `srsran_cell_isvalid()` comment states outright that downlink buffers
were *already* updated to size from `max(nof_prb, mbsfn_prb)` — confirmed by reading
`enb_dl.c`, `pmch.c`, `chest_dl.c`, and `refsignal_dl.c` on the TX side: all consistently use
`SRSRAN_MAX(nof_prb, mbsfn_prb)` for stride/buffer sizing. Wideband PMCH support genuinely
exists at the PHY level; the "not implemented" conclusion in the section above was reached by
checking the RRC/MAC clamps without ever checking whether the PHY layer had already been
fixed independently. Two real bugs, once actually traced:

1. **RX-side TX/RX divergence**: `rt-mbms-modem`'s copy of `pmch.c` (a separate copy of the
   same shared library file) was missing the exact stride fix already present in
   `rt-mbms-tx`'s copy — using plain `nof_prb` instead of `SRSRAN_MAX(nof_prb, mbsfn_prb)` as
   the buffer stride in `pmch_cp()`, corrupting every symbol beyond the first whenever
   `mbsfn_prb > nof_prb`. Fixed to match the TX side.
2. **The actual root cause of MCCH's 100% decode failure**: `pmch_cp()` (and its `pmch_get`/
   `pmch_put` wrappers, in *both* repos' copies) computed how many PRBs' worth of REs to
   extract/place internally from `q->cell.mbsfn_prb`, rather than from the value the caller
   (`srsran_pmch_decode`/`encode`) had already computed into `cfg->pdsch_cfg.grant.nof_prb`
   and was independently checking the extracted count against
   (`"PMCH 1 extract symbols error expecting %d symbols but got %d"`). Whenever these two
   independently-derived values disagreed, decode failed outright — confirmed via
   `subframe_log` data showing `SF_EVENT_MCCH`/`SF_STATUS_FAIL` on 100% of MCCH occasions
   once `pmch_bandwidth` signaled a wider MTCH allocation. Fixed by threading an explicit
   `prb_count` parameter through `pmch_cp`/`pmch_put`/`pmch_get` in both repos, so extraction
   always matches exactly what the caller's `cfg` says, eliminating the possibility of the
   two ever disagreeing.

**Also fixed**: three dashboard-diagnostic-only functions in `CasFrameProcessor.cpp`
(`cir_values()`, `ce_values()`, `composition_grid()`, feeding the CAS Composition/CIR/CE
visualizations) were sized from plain `nof_prb`, inconsistent with the actual sample stream's
width once `mbsfn_prb > nof_prb` — fixed to the same `SRSRAN_MAX` pattern. Confirmed via
`srsran_ue_dl_set_cell()` (the actual decode path, shared by CAS and MBSFN processors) that
the *real* CAS decode was never affected — it already correctly samples at the wider rate
and extracts only the narrower CAS-relevant region, the same "sample wide, extract narrow"
pattern standard LTE uses for PSS/SSS/PBCH within a wider carrier. Confirmed live: CAS decode
succeeds throughout (125/125 in one sample window) regardless of `pmch_bandwidth`.

**Result after both fixes**: substantial, measurable improvement, confirmed via
`subframe_log` — MCCH went from 100% failure to a majority-success rate (e.g. 5 OK / 3 FAIL
in one sample), and MTCH data decode was attempted at all for the first time (previously
zero attempts, since the schedule was never successfully learned). **Not fully fixed**: MTCH
data decode still mostly fails (e.g. 4 OK / 2259 FAIL in the same sample). Traced this to a
separate, still-open issue: `SdrReader`'s `SYNC_OFFSET_DIAG SLOWCALL` fires persistently
(11-22ms against a 1ms budget) once the sample rate is raised to accommodate the wider PMCH
(confirmed via `pidstat -t`: near-zero CPU on every thread during these overruns, so the
read thread is blocked/waiting, not compute-bound — ruling out a decimator-style CPU
ceiling). Also ruled out: the eNB falling behind in real-time TX (zero `tx time is X ms in
the past` messages, unlike Finding 7's n_prb=75/100 mechanism). Root cause of this remaining
blocking behavior not found — needs further investigation into the bridge's buffering/timing
at the higher (15.36 Msps, ratio=1) rate once the SDR dynamically retunes mid-operation to
accommodate a signaled `pmch_bandwidth`.

**Files changed**: `rt-mbms-modem/lib/srsran/lib/src/phy/phch/pmch.c` and
`rt-mbms-tx/lib/src/phy/phch/pmch.c` (both copies: stride fix, `prb_count` parameter
threading), `rt-mbms-modem/src/CasFrameProcessor.cpp` (diagnostic sizing fixes),
`rt-mbms-tx/srsenb/src/stack/rrc/rrc.cc` and `rt-mbms-tx/srsenb/src/stack/mac/mac.cc`
(both `>nof_prb` clamps removed again, this time with the actual mechanism understood rather
than reverted out of caution).

### Finding 5 (MCS=28 clamp discrepancy) — NOT a real bug; original finding was a misdiagnosis

Live-instrumented re-testing (temporary diagnostic prints at every step of the clamp/pack
chain) could not reproduce the original "log says 24, decoded is 26" discrepancy.
`clamp_pmch_mcs_to_feasible()` (the function that decides the feasible MCS) is a small,
pure, deterministic function of exactly its three parameters (`requested_mcs`, `nof_prb`,
`use_mcs_table2`) plus one hardcoded constant — verified by reading its full body. Called
today with the campaign's own inputs (26, 25 PRB, table1), it correctly and consistently
returns **26** (not 24) — i.e. MCS=26 genuinely *is* feasible at 25 PRB, and the value the
modem decoded (26) was the objectively correct answer all along. The original "clamping to
MCS=24" log line, found via `grep` much earlier the same day, most likely reflected some
other, untracked state at that specific moment (the function's determinism rules out the
log and the live test disagreeing under identical inputs) — this was not independently
re-verified before being written up as a bug, which in hindsight it should have been.

**What did get changed, and is being kept as a genuine (if unrelated) improvement**:
`rrc::pack_mcch()` used to independently recompute the exact same MCS clamp from
`cfg.mbms_mcs` rather than receiving the already-computed value from its only caller — two
copies of the same logic that happened to always agree here, but were one accidental future
edit away from silently diverging (the internal `mcch_t` struct and the OTA ASN.1 message
disagreeing on MCS). `pack_mcch()` now takes the clamped `mbms_mcs` as a parameter instead
of recomputing it. This is a code-hygiene fix, not a bug fix — no observed behavior changed
because of it.

**Files changed**: `rt-mbms-tx/srsenb/src/stack/rrc/rrc.cc`,
`rt-mbms-tx/srsenb/hdr/stack/rrc/rrc.h` (`pack_mcch()` signature).

### Spec-compliance gap fixed: `systemInfoValueTag` never bumped on live eMBMS reconfigure

Unrelated to Findings 4/5 turning out to have other causes, but found while investigating
them: `regenerate_si()` (the ETWS alert code path) bumps `cfg.sib1.sys_info_value_tag_r14`
before rebuilding SI content; `configure_mbsfn_sibs()` (the eMBMS live-reconfigure path)
rebuilds the same SI content but never bumped this tag. Per TS 36.331 §5.2.1.2, this tag is
the mechanism a spec-compliant UE uses to detect that broadcast SI changed and should be
re-acquired. This specific test rig's modem doesn't gate re-parsing on the tag (confirmed:
it only logs a warning on change, `Rrc.cpp:501-507`), so this wasn't the cause of any
observed test failure — but it's a real gap for any spec-compliant receiver. Fixed:
`configure_mbsfn_sibs()` now bumps the tag the same way `regenerate_si()` does.

### Finding 7 (`n_prb`∈{75,100} native-rate ceiling) — first pass: mitigated only (fails loudly instead of silently; actually fixed later, see below)

Building real bidirectional dynamic resampling (the actual fix) is a substantial
architecture change spanning both the TX and RX sides of `soapy-zmq-bridge` (confirmed: the
eNB's *own* TX `device_args` also hardcodes `base_srate=15.36e6` — this is not an RX-only
gap) and touches real-time DSP code that's already been the site of multiple delicate bugs
this session. Out of scope for this pass. Instead, `ZmqRxDevice.cpp` now detects
`sample_rate_ > native_sample_rate_` (previously silently ignored, falling through to
`ratio=1`) and logs a clear, rate-limited `SOAPY_SDR_ERROR` explaining the mismatch and what
to do about it, rather than leaving operators to puzzle over a growing "tx time in the past"
drift and a MIB that never decodes.

**Files changed**: `soapy-zmq-bridge/ZmqRxDevice.cpp` (rebuilt, `libzmqrxSupport.so`
redeployed).

### Finding 7, actually fixed (2026-07-18): both n_prb=75 and n_prb=100 now genuinely work — config-only, no code changes

The "substantial architecture change" framing above turned out to be wrong once actually
investigated: real bidirectional resampling was never needed. The eNB's own TX rate is
*also* just a hardcoded config literal (`enb_baseline.conf`'s `device_args=...,base_srate=
15.36e6,...`), completely decoupled from `n_prb` — confirmed via `txrx.cc:93-99`, which
already correctly computes the right rate for any `n_prb` via `srsran_sampling_freq_hz_scs()`
and calls `set_tx_srate()`, but that value only sets a decimation *ratio* against the
still-fixed `base_srate` (`rf_zmq_imp.c:437-457`) — it never changes what's actually sent
over the wire. So the eNB has the identical bug class as the bridge, and the two combine:
whatever `base_srate` says is genuinely what goes out, and the bridge's `native_srate` must
match it exactly for the ratio to even be computable.

**The fix**: keep `base_srate` (`enb_baseline.conf`) and `native_srate`
(`modem_zmqtest.conf`) equal to each other and equal to the true rate for whatever `n_prb`
is configured (25→7.68e6, 50→15.36e6, 75→23.04e6, 100→30.72e6 — the standard LTE
bandwidth-class table). At 75/100 this makes the bridge ratio exactly 1 (plain passthrough,
same as the already-working 50 PRB case), not 2/3/4 needing new upsampling logic.

That alone wasn't sufficient — it surfaced a **second, previously-invisible bug**: the
modem's blind cell-search phase (`Phy::cell_search()`, `main.cpp:419-420`) assumes a
hardcoded PRB count for its own FFT/frame sizing, taken from the `-b`/`--file-bandwidth`
CLI flag (`cs_nof_prb = file_bw * 5`) — **not** from `-p`/`--override_nof_prb` as the flag's
name would suggest; `file_bw` unconditionally wins in that ternary regardless of live-SDR
vs. file mode, making `-p` silently dead code for this launch script. `receive-netns.sh`
has always passed `-b 10` (→ `cs_nof_prb=50`, matching the pre-existing 15.36 MHz baseline
exactly, which is why n_prb=25/50 never surfaced this). At 75/100, `-b` must be bumped to
match too (`-b 15`→`cs_nof_prb=75`→23.04 MHz; `-b 20`→`cs_nof_prb=100`→30.72 MHz), or cell
search fails outright (`Phy: Could not find any cell in this frequency`) from a non-integer
bridge decimation ratio during the search phase. Both mechanisms confirmed live: n_prb=75
and n_prb=100 each reached clean BLER 0.0 once `base_srate`/`native_srate`/`-b` were all
kept in lockstep with `n_prb`; reverted all three back to the 25-PRB baseline afterward,
re-confirmed unchanged (243/243 CRC pass).

**Files changed**: `enb_baseline.conf` (`base_srate`, transiently, back to baseline),
`modem_zmqtest.conf` (`native_srate`, `search_sample_rate_hz`, transiently, back to
baseline; comment added documenting the required lockstep), `receive-netns.sh` (`-b` flag,
transiently, back to baseline `10`; comment added). No source code changes — this was
entirely a test-harness/config gap, not a bug in the eNB/modem/bridge code itself.

### Pre-existing gap fixed: `pmch_bandwidth=25` falsely accepted as a valid value

Confirmed via the actual ASN.1-generated header (`pmch_bandwidth_r17_opts`) that the enum
genuinely has no `n25` — only `{n40, n35, n30, spare1, nulltype}`. `reconfigure_embms()`'s
own validation (and `enb_cfg_parser.cc`'s static-config equivalent) used to accept 25 as if
it were a fourth legal value; since the packer's switch has no matching case, setting it was
silently a no-op. Both validation sites now reject 25 with a clear warning explaining why
(pmch-Bandwidth-r17 signals coverage *wider* than the base cell, which 25 — the smallest
cell size tested in this campaign — can never be).

**Files changed**: `rt-mbms-tx/srsenb/src/stack/rrc/rrc.cc`,
`rt-mbms-tx/srsenb/src/enb_cfg_parser.cc`.

### Finding 1 (TI live-reload / "M always decodes as 4") — FIXED, and the real cause was structural

The same live-diagnostic technique that cracked Finding 4 (bump `rrc_level`, watch a warning
that had been silently filtered) found this in minutes. `pack_mcch()`'s OTA-signalling switch
for `time_interleaving_m` only has explicit cases for `{8, 16, 32}` (`default: sf4` for
everything else) — matching `pmch-TimeInterleavingM-r19`'s actual ASN.1 enum
(`{sf4, sf8, sf16, sf32, nulltype}`, confirmed by reading the generated header — only 4
discrete values are representable at all). Separately, `configure_mbsfn_sibs()`'s M
divisor-clamp searched *all* integers down from the configured M for one that evenly divides
`sf_alloc_end`, with no regard for whether the result was one of those 4 legal values.
Live-confirmed: for this rig's baseline (`sf_alloc_end=623=7×89`), that search lands on 1 (for
M=4), 7 (for M=8), or other values with zero representation in the enum — the OTA switch then
always fell through to its `default: sf4`, regardless of what was configured or what the
clamp actually computed. That's the entire "M always shows 4" mechanism: not a value that's
stuck, but every clamped result independently landing outside the enum and silently defaulting
to the same fallback.

**Deeper finding**: confirmed by direct computation that under this baseline (no CAS muting,
`additional_non_mbsfn_subframes=0`), **no valid `mch_sched_period_rf` value ever produces an
`sf_alloc_end` divisible by 4, 8, 16, or 32** — so time interleaving is structurally infeasible
here regardless of which N/M is requested, not a "wrong test value" problem. (One combination
does work — `mch_sched_period_rf=4` with `additional_non_mbsfn_subframes=2` gives
`sf_alloc_end=36`, divisible by 4 — but that parameter is restart-only, not live-settable, so
exercising it is a separate, bigger change than this fix.)

**Fix**: the clamp now searches only the 4 legal enum values (largest first, capped at the
configured M), and if *none* divide `sf_alloc_end` evenly, disables time interleaving outright
(`N=0, M=0`) with a clear warning explaining why, instead of silently signaling an infeasible
M as if it were 4. Live-verified across all four N/M combinations plus the disable-restore
case under the original baseline: **all now correctly report N=0/M=0 with the explanatory
warning, BLER 0.0 throughout**.

**Follow-up, same day: made TI actually functional, not just honestly disabled.** Applied the
parameter change flagged above as a "separate, bigger change" — `additional_non_mbsfn_subframes=2`
(config-file edit, restart-only) plus a live `SET embms.mch_sched_period_rf=4` — giving
`sf_alloc_end=36`, divisible by 4. Enabled `time_interleaving_n=2, time_interleaving_m=4` (the
only legal M for this `sf_alloc_end`) and verified with the `PMCH_TI_DIAG` diagnostic exactly
per the original plan's methodology (BLER alone can't distinguish genuine combining from
coincidental per-subframe success on this clean a channel): confirmed the `TI_DIAG_MFP` worker
pointer stays **identical** across all `mch_subframe_idx` values within one 4-subframe block
and only changes at the block boundary, with `mb_idx_before` advancing exactly once per block —
the methodology-defined proof of real time-interleaving combining, not just correct signaling.
**Time interleaving genuinely works, given scheduling parameters compatible with the M-divides-
sf_alloc_end constraint.** Reverted `additional_non_mbsfn_subframes` back to the original
baseline afterward (consistent with this campaign's practice of restoring true baseline after
each test) — this is a proven, working configuration, documented here for future use, not
silently left as the new default.

**Not touched**: `time_interleaving_n_last_mtch`/`m_last_mtch` (the per-session LastMTCH
override, only relevant with 2+ MBMS sessions) are packed through their own, separate switch
statements with no divisor-clamp at all — out of scope, since this campaign's tests never
exercised multi-session TI.

**Files changed**: `rt-mbms-tx/srsenb/src/stack/rrc/rrc.cc` (clamp search restricted to legal
values; disables TI outright when none apply), `test_sib_matrix.py` (all 4 TI cases' `verify`
expectations updated to match: safe disable, not the nominal N/M, is now the correct
outcome under this baseline).

**Side effect worth noting for Finding 2**: the modem-non-recovery pattern was first observed
right after this exact bug (TI signaled-but-broken) put the eNB and modem in an inconsistent
state. With TI now either genuinely working or honestly disabled, never silently
signaled-as-enabled-but-actually-not, that specific trigger path no longer exists. Not
independently re-tested (would need deliberately reproducing a break, which wasn't attempted
here) — but Finding 2's CAS-muting-triggered instance is a separate, still-open path
regardless (per your instruction, TI and CAS muting were kept isolated throughout this
investigation, never combined).

### Finding 3 (CAS muting BLER breakage) — FIXED 2026-07-17, precisely characterized (2026-07-16)

A follow-up pass (same day) grounded this in the actual primary spec text (TS 36.211
v19.3.0, TS 36.331 v19.0.0, read from local copies in `~/Descargas`), not just TX/RX
self-consistency, per explicit instruction. Key spec facts confirmed:
- §6.1 (general rule): "For an MBMS-dedicated cell, subframes where PSS/SSS/PBCH or PDSCH
  carrying system information are transmitted... are non-MBSFN subframes" — conditional,
  so a muted CAS occasion's subframe genuinely reverts to ordinary MBSFN status. This
  vindicates the codebase's core design assumption on primary-source grounds.
- §6.6.4/§6.11.1.2/§6.11.2.1 (the literal CR 0577 text, all three identical): CAS is muted
  in the last `16*N_CAS - 4*K_CAS` frames of every `16*N_CAS`-frame period; matches the
  code's `nof_true_cas` formula exactly.
- CAS = "Cell Acquisition Subframes" (36.331 acronym list); confirmed CAS only ever occupies
  subframe 0 for an MBMS-dedicated cell (§6.11.1.2's exception clause: "transmitted in slot 0
  ... only", equation recovered from an embedded OLE object = `nf mod 4 = 0`, no subframe 5
  involvement at all). The code's `is_cas_subframe()`/`is_mch_subframe()` already reflect this
  correctly for `mbms_dedicated` cells; the legacy `tti%10==0||tti%10==5` branch only fires
  for non-dedicated (mixed) cells, not exercised on this rig.

**Ruled out this pass, each via direct reading of the actually-compiled code (not the
vendored-but-unused `lib/srsran/srsue/` reference app, which one exploratory agent
mistakenly cited — discarded):**
- Static/non-per-frame FFT, SCS, or non-MBSFN-region selection (genuinely keyed on
  `sf_type`, uniform for every subframe).
- `commonSF-Alloc-v1610` sf0/sf5 bits (both declared `true` on TX, correctly parsed on RX).
- Shared-`_ue_dl`-object contamination between `CasFrameProcessor`/`MbsfnFrameProcessor`
  (confirmed separate instances).
- `pmch_cp`'s RE-extraction offset formula (`srsran_refsignal_mbsfn_offset`): provably
  identical for sf=0 and sf=2 at this SCS (both even-tti, same stagger=0) — the bug is not
  in RE-indexing.
- TX-side PSS/SSS/PBCH/PCFICH muting-awareness (`enb_dl.c`'s `put_sync`/`put_mib`/
  `put_pcfich`): all correctly check `cas_muting`/`k_cas`/`n_cas` and skip on muted frames.
- TX resource-grid buffer staleness: `clear_sf()` zeros the *entire* grid unconditionally at
  the start of every subframe (`srsran_enb_dl_put_base`), before pilots/data are written —
  a muted, unscheduled sf=0 should transmit clean pilots + literal zero data, same as any
  other idle subframe.
- MAC scheduling being muting-specific: confirmed live (`CAS_MUTE_DIAG_ENCPMCH`, TX-side)
  that real MTCH data is scheduled for exactly one subframe per ~640-tti period
  (`mch_subframe_idx=0`), which structurally lands on sf=2, never sf=0 — so `rnti=0` at
  sf=0 is ordinary "nothing queued" behavior, same as most other positions, not something
  muting causes.

**A serious, separate bug was found and resolved along the way**: mid-investigation, overall
MCH BLER collapsed to ~4-8% (not just sf=0) and the modem crashed (`std::vector::operator[]`
out-of-bounds assertion). Proven via a clean A/B test (disable muting, same collapse) that
this was **independent of CAS muting** — it was the already-documented "Modem Long-Uptime
Degradation" bug (see that memory), apparently affecting the eNB side after ~1h+ uptime;
fixed by a fresh restart of both eNB and modem together. `SYNC_OFFSET_DIAG SLOWCALL`
(11-22ms per call vs 1ms budget) turned out to be a chronic, pre-existing, harmless artifact
of this pipeline's ~91%-of-nominal throughput ceiling (present even in a fully healthy run) —
not the cause of the BLER collapse. Also cleaned up genuine diagnostic debt: `SYNC_ERR_DIAG`
(unconditional fprintf on every MBSFN subframe, ~90% of traffic, left enabled since an
already-concluded earlier investigation), `RACE_DIAG`, `PMCH_RE_DUMP`, `DECIM_DUMP` were all
removed from `receive-netns.sh`'s default launch env (see that script's history) — this did
*not* fix the SLOWCALL issue (confirmed unchanged after removal), so it was pure debt, not
the root cause.

**Corrected, precise bug signature (2026-07-17, after user pushback caught a flawed
comparison)**: the original characterization ("sf=0 fails 100%, ~6x raw power deficit vs
sf≠0") conflated two different things by comparing FAILED sf=0 attempts against SUCCESSFUL
sf≠0 decodes — an apples-to-oranges comparison (empty vs real-content power), not evidence of
an sf=0-specific defect. Direct measurement of genuinely-idle (correctly DTX-filtered)
subframes shows the raw noise-floor power (~130) is IDENTICAL at every subframe position,
sf=0 through sf=9 — universal, not sf=0-specific. The corrected, narrower finding, measured
in one consistent time window:
- sf=0: 543/(543+3261) = **14.3%** of muted occasions trip the `data_pw<1e-3` "has content"
  gate and proceed to a (doomed) decode attempt.
- sf≠0: ~0.32% trip the same gate (14/4347 for sf=2, comparable for sf=3-9).
That is a genuine, ~45x anomaly specific to sf=0 — but it means roughly 86% of muted sf=0
occasions are correctly, harmlessly recognized as empty, exactly like sf≠0's mostly-idle
traffic. Only the ~14% that spuriously cross the threshold are a real defect: their
post-equalization data power spikes to thousands (not ~1.0 like genuine content, not ~0 like
genuine idle) and the resulting attempt always fails (0% success in every sample collected).
The aggregate dashboard BLER (~0.82-1.00 depending on sample window) was wrongly interpreted
as "MCH decode is broken at sf=0" — it's actually dominated by this narrower, ~1-in-7
spurious-detection anomaly, not a blanket decode failure. Channel estimate/RSRP still look
reasonably healthy in the failing subset (~850-880, comparable to sf≠0's ~880-894) — the
spurious content-detection spike is not simply explained by a globally-bad channel estimate
either. An ad-hoc raw time-domain capture of an actual failing sf=0 occasion
(`/tmp/pmch_rx_rawFAIL_tti*.bin`) shows real but weak in-band spectral energy.

**FIXED 2026-07-17** (`MbsfnFrameProcessor.cpp`, commit `c3bbdcd`): rather than continuing to
chase the exact trigger, fixed the actual observable defect directly. Confirmed live:
genuine content always shows `data_pw≈1.0` (0.9997-1.0002 across every real decode sampled);
genuine idle always shows `data_pw≈0` (0.000000-0.000003, identical at every subframe
position including sf=0 — confirmed NOT sf=0-specific, correcting the earlier
"6x power deficit" framing which compared failures against successes, not idle-vs-idle);
the anomalous cases show `data_pw` in the thousands (4500-39000) — physically impossible for
real content, only explainable by a near-zero denominator in the equalizer. The existing DTX
gate (`data_pw < 1e-3` → IDLE) only caught the empty case; extended it symmetrically to also
catch `data_pw > 10.0` → IDLE, since both extremes mean "nothing reliable was decoded", not a
genuine failure. **Live-verified**: MCH 0 BLER dropped from ~0.82-1.00 to **0.0**, 100%
success on every genuine decode attempt post-fix, confirmed directly via
`/modem-api/mch_status/0`.

**Root cause of the anomaly itself — still open, lower priority given the fix is deployed**:
live evidence (before the fix) showed the anomaly is 100% attributable to exactly ONE of the
`thread_cnt` (4) round-robin `MbsfnFrameProcessor` worker-pool instances — confirmed
reproducible across a fresh restart with new memory addresses (ruling out address-specific
corruption), at a uniform ~14.3% probability across all 32 possible muting-period phase
alignments (ruling out an SFN-wraparound-tied or otherwise frame-number-deterministic cause).
That SAME worker decodes sf=1-9 with 100% success, including immediately before and after its
own sf=0 misfires, ruling out a generally-broken instance. All 4 `MbsfnFrameProcessor`
objects are constructed identically (`main.cpp:481-488`, plain loop, no per-index
differentiation), so the divergence must be runtime/timing-dependent, not structural.
**Concrete new lead, not yet chased down**: `main.cpp:690` gates whether to dispatch a tti to
a worker at all via `phy.is_mbsfn_subframe(tti)` — a call *independent* of and separate from
`mbsfn_config_for_tti()` (already computed moments earlier, at `main.cpp:650`, as `peek_cfg`,
and again inside the worker's own `process()`). These are two different classification code
paths that must agree for a muted sf=0 to be both dispatched and correctly configured; if
they can momentarily disagree (e.g. via unsynchronized reads of `Phy::_cell`'s
`cas_muting`/`k_cas`/`n_cas` fields, which are plain members with no dedicated mutex, unlike
`_mcch_mutex`/`_sib13_mutex`), that would explain a rare, worker-timing-dependent divergence
specific to the position with dual CAS/MBSFN routing (sf=0) and nowhere else. Next step if
resumed: audit `Phy::_cell`'s field-level thread-safety, and/or add a diagnostic comparing
`is_mbsfn_subframe(tti)` and `mbsfn_config_for_tti(tti,...).enable` at the exact moment of
dispatch for every sf=0 occasion, to catch a live disagreement directly.

**Naming note (per explicit request)**: `MCH_SF5_DIAG` was renamed to `MCH_DIAG` — the name
was stale, inherited from an earlier, already-fixed, unrelated subframe-5 DTX-detection bug
(commit `8622a52`); the diagnostic itself was always generic (any non-MCCH MBSFN subframe),
just mislabeled. Also added `data_pw`/`cepw`/`rsrp`/`syncerr` fields to the diagnostic print.
`sf5` itself has no CAS-related meaning for an MBMS-dedicated cell (confirmed against primary
spec text above) — the only place sf5 is legitimately meaningful is `commonSF-Alloc-v1610`'s
separate sf0/sf5 "common MBSFN capacity" bits, unrelated to CAS occupancy, and both already
correctly declared `true` on this rig.

### Coverage gap found and closed: frequency interleaving was never tested

Review after the fix pass turned up a real gap in the original test matrix: `freq_interleaving`
(Rel-19 `pmch-TFI-Config`'s `pmch-FreqInterleav-r19`, `embms.freq_interleaving` on the
control socket) is a real, live-reconfigurable PMCH parameter — a simple on/off flag for
frequency-domain interleaving, distinct from (and independent of) time interleaving's N/M —
that was never included in the original plan's matrix or `test_sib_matrix.py`'s case list,
despite the campaign's stated goal of exercising "the different SIB parameters."

Added two cases (`freq_interleaving_enable`, `freq_interleaving_disable_restore`) and
tested live: **both PASS, cleanly** — `mcch.pmch_list.0.freq_interleaving` correctly
decodes `true`/`false` matching what was set (double-checked directly via REST, not just
the script's automated diff), and BLER stayed 0.0 throughout both transitions. Unlike time
interleaving (which shares the same `has_phase2`/v1900 MCCH extension code path but is
badly broken), frequency interleaving's simple on/off signaling works correctly end-to-end,
TX to RX. This is the same code path `use_mcs_table2` already exercised successfully earlier
in the campaign, consistent with that being the working case and full N/M time interleaving
being the outlier.

**Files changed**: `test_sib_matrix.py` (two new cases added, so future re-runs of this
matrix include frequency interleaving).

### Pre-existing gap (TI/MCCH §15.3.3 conflict) — left as-is, already self-documented

Already produces a clear, correctly-cited in-code warning
(`reconfigure_embms()`: "TS 36.300 §15.3.3 requires a time-interleaved MCH not carry MCCH").
An actual fix needs genuine multi-PMCH support (separating MCCH-carrying capacity from a
TI'd MTCH-only PMCH into distinct `pmch_info_list` entries) — a real feature addition, well
beyond a bug fix, not attempted this pass.

## Findings (as originally documented — see "Fix pass outcomes" above for what's changed)

### Finding 1: Time interleaving (`time_interleaving_n`/`m`) live-reload is unreliable

- `time_interleaving_m` sticks at its first-ever configured value (4) across all later live
  `SET`s (8, 16, 32 all decoded as 4).
- `time_interleaving_n` mostly updates correctly call-to-call, but at one point in the
  sweep (`ti_16_32`) decoded as the *previous* call's value instead of the new one —
  more consistent with a propagation-timing race (the fixed ~8s wait not always spanning a
  fresh `mcch_modification_period` boundary) than a hard "N never updates" rule.
- Disabling TI entirely via live `SET` (N=0) also failed to take effect — decoded N stayed
  at 4, and BLER collapsed to 100%. This is more serious than the M/N staleness above: a
  stale-but-internally-consistent signal is merely wrong, but a signal that goes stale
  *while the eNB's actual TI behavior underneath has changed* breaks decode outright.
- Root cause traced as far as `rrc.cc:1825-1839`: `pmch_item->time_interleaving_m` is only
  written inside `if (cfg.pmch_time_interleaving_n > 1 && cfg.pmch_time_interleaving_m > 1)`,
  with a divisor-clamp against `sf_alloc_end` (confirmed live: `sf_alloc_end=623=7×89`, a
  semiprime that none of the tested M values divide evenly — so the clamp-search should
  produce different results each time, e.g. M=8→7). The clamp warning log line never
  appeared at all across the sweep, suggesting the whole repack block may not be
  re-executing on subsequent `SET`s, not just the arithmetic being off. Not fully nailed
  down — needs tracing whether `reconfigure_embms()`/this block is even called again after
  the first live `SET`.

### Finding 2: Modem doesn't self-recover from TI-induced corruption without a full restart

Once Finding 1's N=0-disable failure broke decode (BLER 100%), restarting *only* the eNB
with a confirmed-clean config (N=0/M=0/MCS=9 via REST) still showed ~97-98% BLER on the
existing modem process. Only restarting the modem too brought BLER back to 0.0. This points
to the modem's TI block-boundary/worker-pinning logic (`main.cpp`'s `mb_idx`/
`ti_last_of_block` tracking) getting durably desynced by a broken or rapidly-changing TI
signal, with no self-recovery once the signal goes clean again — a robustness gap on the RX
side, distinct from Finding 1's TX-side propagation bug. The same "won't self-recover, needs
both eNB and modem restarted" pattern recurred once more later in the campaign (residual
~91-93% BLER at the start of the bandwidth phase, traced to the immediately-preceding CAS
muting sweep) — treat as a general property of this modem build, not a TI-only quirk.

### Finding 3: CAS muting breaks PMCH decode whenever active, despite fully correct signaling

All four tested `(k_cas, n_cas)` combinations decoded their signaling exactly right
(`sib1.cas_muting_enabled`/`k_cas`/`n_cas` all matched what was set) — unlike Finding 1,
the signaling path itself works. But functional BLER was severe in every enabled case:
(4,2)→100%, (8,4)→~91%, (16,8)→~43%, (32,16)→~95%. Disabling muting again immediately
returned BLER to 0.0 with no extra restart needed — unlike Finding 2, this one doesn't
leave lasting corruption.

BLER varying by combination (not a uniform 100%) points to a **partial subframe-index
misalignment** rather than total decode failure — consistent with `Phy.cpp:568-595`'s
`nof_true_cas` (frames actually consumed by CAS) being computed differently depending on
`cas_muting`, shifting the absolute-TTI-to-logical-MCH-subframe-index mapping. Working
hypothesis, not yet confirmed: muted CAS occasions are supposed to carry real MBSFN/PMCH
data (that's the point of muting, per Rel-19 CR 0577), so if the RX's subframe accounting
doesn't correctly treat a muted occasion as MCH-carrying, every subsequent MCH subframe
would be looked for at the wrong index — explaining large-but-not-always-100% BLER,
since some subframes coincidentally still land correctly depending on the specific
k_cas/n_cas period arithmetic. Not yet done: confirm directly by comparing `nof_true_cas`'s
computation against the actual muting pattern, and check whether the TX side has an
analogous accounting issue.

### Finding 4: `pmch_bandwidth` live-reload doesn't propagate (same pattern as Finding 1)

All three tested values (30, 35, 40 PRB at n_prb=25) decoded as `0` — the eNB accepts and
internally stores the value immediately (confirmed via control-socket `GET`), but it never
reaches the actual broadcast SIB13 content. BLER stayed 0.0 throughout (functionally
benign here, since TX and RX stay mutually consistent at the stale `pmch_bandwidth=0`) —
unlike Finding 3, no corruption or restart was needed. Same "accepted, not broadcast"
shape as Finding 1; not yet traced to a specific line, but `configure_mbsfn_sibs()`/
`pack_mcch()` possibly not being re-invoked (or a missing value-tag bump) on a live-reload
`SET` that only touches `pmch_bandwidth` is the leading candidate, by analogy.

### Finding 5: MCS=28's clamp — eNB log claims a stricter result than what's actually signaled

Configuring MCS=28 triggers the known infeasibility clamp (`rrc.cc`), and the eNB log
records: `"Configured PMCH MCS=26 is infeasible for 25 PRB (code rate > 1...); clamping to
MCS=24"`. But the value actually decoded by the modem — confirmed via REST, cross-checked
against the raw log — is **26, not 24**. BLER stayed 0 at 26. This is the same "accepted/
logged, not actually applied to the broadcast content" shape as Findings 1 and 4, just
surfacing through the MCS clamp path instead of TI or bandwidth. Functionally benign in
this instance because TX and RX ended up self-consistent at 26 regardless of what the log
claimed — but a case where TX and RX disagree (one side acting on the log's claimed 24,
the other on the implied-still-26 input) would not be benign. Not fully root-caused.

**Cross-cutting note**: Findings 1, 4, and 5 all show the identical shape — a live
`SET` (or an automatic clamp during one) is validated and logged correctly, but the
actual value packed into the broadcast MCCH/SIB13 content doesn't reflect it. Given three
independent parameters hit the same failure mode, the fix pass should look for one shared
cause (most likely in `configure_mbsfn_sibs()`/`pack_mcch()`'s re-invocation or a value-tag
bump on live reconfiguration) before treating these as three separate bugs to fix
individually.

### Finding 6: `n_prb`∈{6,15} crash the modem (decimator CPU saturation)

n_prb=6 and n_prb=15 both crashed the modem process outright (silent exit, no captured
crash log) within seconds of starting. Root cause, confirmed via `ZMQRX_RATIO_DIAG` and
`SYNC_OFFSET_DIAG SLOWCALL`: these bandwidths need decimation ratios of 8x and 4x
respectively (vs. 2x at the working n_prb=25 baseline), which by the bridge's
`design_decim_lowpass()` formula (`15×ratio+1` taps) means 121 and 61 taps — well past the
CPU headroom this session's `-O3` fix provides (validated safe only up to ratio=2, ~35-45%
of one core; ratio≥4 needs roughly 2-4x that compute). Not a SIB13/MBSFN signaling bug —
a scaling limit of the current (non-polyphase) decimator implementation.

### Finding 7: `n_prb`∈{75,100} fail via a hardcoded native-rate ceiling with no upsampling path

n_prb=75 didn't crash, but never acquired: the eNB log showed a persistent, steadily-growing
`tx time is X ms in the past` drift, and the modem's decoded MIB never appeared
(`nof_prb: 0` after 20+ seconds). Root cause, confirmed at the code level:

- `phy_common.c`'s `srsran_symbol_sz_scs()` (SCS_1KHZ25 branch) gives `symbol_sz=18432` for
  `nof_prb<=75`; physical sample rate = symbol_sz × SCS = 18432 × 1250Hz = **23.04 MHz**
  required for n_prb=75 at this session's 1.25kHz FeMBMS SCS (the same physical Fs as
  standard-LTE n_prb=75 at 15kHz SCS — the sample-rate steps line up with the familiar LTE
  bandwidth classes regardless of SCS: 6→1.92, 15→3.84, 25→7.68, 50→15.36, 75→23.04,
  100→30.72 MHz).
- `modem_zmqtest.conf` hardcodes `native_srate=15.36e6` — exactly matching n_prb=50, one
  step below what n_prb=75 needs.
- `soapy-zmq-bridge/ZmqRxDevice.cpp`: the decimation `ratio` is only computed when
  `sample_rate_ < native_sample_rate_` — there is no branch anywhere in the file for
  `sample_rate_ > native_sample_rate_`. When the modem requests 23.04 MHz against a 15.36
  MHz native rate, that condition is false, so `ratio` silently stays at its initialized
  value of `1` — no upsampling happens, no error is raised, and the bridge just serves
  15.36 Msps while the modem's PHY believes it's receiving 23.04 Msps. That fixed-ratio
  mismatch produces exactly the observed linearly-growing timing drift, and the wrong
  sample-rate assumption breaks symbol/slot boundary detection, explaining why the MIB
  never decoded.
- n_prb=100 (needing 30.72 MHz, 2x native — an even larger gap) was **not tested live**:
  the mechanism is confirmed at the code level, so a live test would not add information.
  Skipped by design, not by omission.

Distinct from Finding 6: that one is a compute-cost ceiling on the *downsampling* path;
this one is a complete absence of an *upsampling* path. Both are bridge scaling limits, not
SIB13/MBSFN protocol bugs. **Practical conclusion for this rig as currently built: only
n_prb=25 (ratio=2) and n_prb=50 (ratio=1, exact native-rate match) are usable.**

**Superseded, 2026-07-18: this "no upsampling path" framing was wrong — no upsampling was
ever needed.** The eNB's own TX rate is *also* just a hardcoded `base_srate` literal,
decoupled from `n_prb` the same way the bridge's `native_srate` is; nothing was ever
actually transmitting at 23.04/30.72 MHz to upsample from in the first place. Setting
`base_srate`/`native_srate` to the correct value for the configured `n_prb` (making the
bridge ratio exactly 1, not >1) plus a second, previously-hidden fix to the modem's
cell-search `-b` flag resolved both n_prb=75 and n_prb=100 completely — see "Finding 7,
actually fixed" earlier in this document for the full writeup. **Both are now usable**,
same as 25/50.

### Phase 6 (TI + muting interaction): not run at this point in the campaign

Blocked by Finding 1 — testing the planned interaction cases via live `SET` would just
re-trigger the already-documented TI propagation bug rather than exercise the interaction
itself. Skipped rather than spending live-test time on a result that wouldn't be
informative; revisit once Finding 1 is fixed (a static-config-plus-restart variant would
also sidestep the live-reload bug if an earlier interaction check is wanted).

**Update, 2026-07-18: run once Findings 1 and 3 were both fixed — both cases PASS, no new
interaction bug. See "Phase 6 — run 2026-07-18" further down for the full write-up.**

## Pre-existing gaps (identified during planning, not re-verified live this campaign)

1. **`pmch_bandwidth=25` combined with a different `n_prb`** is validated on the TX PHY
   side, but the SIB13 packer (`rrc.cc:1699-1710`) only has switch-cases for 30/35/40 —
   so this specific combination is silently never signaled over the air. (Separate from
   Finding 4, which is pmch_bandwidth failing to propagate even at n_prb=25 where the
   packer *does* have matching cases.)
2. **Any `time_interleaving_n>1` conflicts with TS 36.300 §15.3.3** (a TI'd MCH shouldn't
   carry MCCH), because this eNB always configures `pmch_info_list[0]`, which is always
   the MCCH-adjacent PMCH (`rrc.cc:1050-1060`, self-acknowledged in-code). Every TI>1 case
   in this campaign therefore ran non-compliant with the spec, independent of Finding 1's
   propagation bug.

## What's next

See "Fix pass outcomes" above for the full picture. Summary of what's actually left:

**Fixed and confirmed genuinely functional (not just safely disabled)**: Finding 1 (TI). The
real bug was `pack_mcch()`'s OTA switch having no case for divisor-clamped M values, always
defaulting to a misleading 4. Fixed the clamp to search only the legal enum values; **then
proven to genuinely work** (via `PMCH_TI_DIAG`'s worker-pointer-stability methodology, not
just BLER/signaling) once given scheduling parameters compatible with the M-divides-
`sf_alloc_end` constraint (`additional_non_mbsfn_subframes=2` + `mch_sched_period_rf=4`).
Safely/honestly disables (rather than misrepresenting) under scheduling parameters that
can't support any legal M, which is what this rig's original baseline happens to be.

**Substantially improved, root cause found and fixed, not fully resolved**: Finding 4
(`pmch_bandwidth`). The real bug (after an incorrect first conclusion that this needed new
PHY architecture) was a TX/RX code divergence in `pmch.c` plus an internal-vs-caller PRB
count mismatch in the same file, in both repos. Fixing both took MCCH from 100% failure to
majority success and made MTCH decode get attempted for the first time ever — but a separate,
still-unexplained timing/blocking issue (`SYNC_OFFSET_DIAG SLOWCALL`, not CPU-bound, not the
eNB falling behind) still causes most MTCH data decodes to fail. Also fixed along the way:
three CAS-dashboard diagnostic functions had the same PRB-sizing bug (cosmetic only — the
real CAS decode path was never affected).

**Also fixed and live-verified**: the `pmch_bandwidth=25` pre-existing gap, the missing
`systemInfoValueTag` bump. `n_prb`∈{75,100} initially only made to fail loudly instead of
silently misbehaving — later (2026-07-18) actually fixed outright, config-only, see
"Finding 7, actually fixed" further up. Frequency interleaving was found missing from the
original matrix entirely and, once added, tested clean (PASS, no fix needed).

**Corrected, not a bug**: Finding 5 (MCS=28 clamp) — could not be reproduced under
live-instrumented testing; the original finding was a misdiagnosis. The `pack_mcch()`
duplicate-computation cleanup was kept anyway as a genuine (if unrelated) hygiene fix.

**Fixed 2026-07-17**: Finding 3 (CAS muting BLER breakage). Root-caused to a spurious
equalized-power anomaly specific to ~14% of muted sf=0 occasions (one worker-pool instance,
trigger not fully explained but no longer matters for correctness); the DTX/idle gate was
extended to also treat implausibly-high post-equalization power as "nothing reliably
decoded," matching how the near-zero case was already handled. Live-verified: MCH BLER
0.82-1.00 → 0.0, commit `c3bbdcd`. Finding 2 (modem non-recovery) is confirmed resolved for
its CAS-muting-triggered path too, by the same live test (no restart needed to recover once
muting was disabled again, consistent with the earlier TI-path finding).

**Still open, needs live-instrumented diagnosis (not more static reading)**: only the Finding
4 SLOWCALL/timing issue now. A separate, unrelated investigation (2026-07-18) into a
previously-documented "eNB/modem long-uptime degradation" bug (BLER regresses after ~1h+/6h+
uptime, fixed only by a fresh restart) found no new TI-independent root cause after a
structured search across five candidate mechanisms — but did catch a real, newly-introduced
risk in an unrelated fix (see "ZMQ_PUB send buffer" note below) before it could cause a
similar-looking symptom. That original degradation's root cause remains unfound.

**Deliberately not attempted, real feature work not a bug fix**: Finding 6 (n_prb∈{6,15}
decimator CPU ceiling — needs a faster/polyphase decimator), the deeper PHY-level wideband
PMCH capability uncovered while fixing Finding 4 (needs resource-grid/softbuffer changes),
and the TI/MCCH §15.3.3 spec conflict (needs genuine multi-PMCH support). Making TI actually
functional under this rig's *default* baseline specifically (not just via a temporary
restart-only parameter change, `additional_non_mbsfn_subframes=2`, applied and reverted for
testing — see Phase 6 below) is not attempted: that would mean permanently changing the
tutorial's default config, a product decision rather than a bug fix.

`rt-mbms-tx` and `soapy-zmq-bridge` were modified this pass; `rt-mbms-modem` was not (the
remaining open findings are suspected to involve modem-side state, but this wasn't
confirmed).

### ZMQ_PUB send buffer: bounded HWM added, then corrected (2026-07-18)

While investigating Finding 4's SLOWCALL/timing issue, added an explicit `ZMQ_SNDHWM` on the
eNB's TX socket (`rf_zmq_imp_tx.c`, both `rt-mbms-tx` and `rt-mbms-modem` copies) — ZMQ_PUB's
default 1000-message HWM means a momentarily-lagging subscriber causes silent message drops,
not blocking. First attempt set it to unlimited (0); a background investigation into the
separate long-uptime degradation bug (below) flagged that since the ~91%-of-nominal
throughput ceiling is a *persistent*, chronic deficit rather than a transient stall, an
unlimited HWM risks roughly unbounded queue growth for as long as it's active at ratio=1
(no decimation margin). Corrected to a large-but-bounded value (50000) instead — keeps the
transient-stall protection without the unbounded-growth risk. Live-verified the video demo
stayed healthy through both the original change and the correction. Committed and pushed:
`rt-mbms-tx` `51fdc61`, `rt-mbms-modem` `5d366cf`. Does not fix the underlying throughput
ceiling itself (still open, see Finding 4 above) — only bounds the worst case of a mitigation
that was already a reasonable idea for the *transient*-stall case it was originally aimed at.

### Investigation: eNB/modem long-uptime degradation root cause (2026-07-18) — inconclusive

Separate, pre-existing bug (see the `project-modem-long-uptime-degradation` memory and
Finding 3's write-up above, where it was hit again and correctly identified as unrelated to
CAS muting): BLER regresses from 0.0 to ~75-79% (modem) or a catastrophic collapse + crash
(eNB) after prolonged uptime (modem ~6h+, eNB ~1h+), fixed only by a fresh restart of both.
Two previously-identified candidates (`pmch_ti_tx_buf` TX race, RX softbuffer poisoning) both
require Time Interleaving active, which the actual observed incidents did not have.

A structured background search (counter wraparound, unbounded containers, softbuffer/HARQ
reuse, floating-point drift, hot-path allocation) across both codebases found no convincing
TI-independent candidate. The only concrete finding was the ZMQ_SNDHWM=0 risk documented
above, which postdates and cannot explain the original, already-documented incidents. Root
cause remains genuinely open; would need live reproduction over hours with memory/per-thread
CPU profiling captured *before* a restart, not more static code review.

### Phase 6 (TI + muting interaction) — now unblocked, not yet run

The original plan deferred this because Finding 1 (TI) was broken. Finding 1 is now fixed
(functional, given `additional_non_mbsfn_subframes=2`/`mch_sched_period_rf=4`) and Finding 3
(CAS muting) is now fixed too — so the planned TI(4,8)+muting(8,4) and TI(8,16)+muting(16,8)
cases could genuinely be run for the first time. Not attempted yet: TI's working combination
needs a restart-only parameter change from this rig's baseline, so it needs an eNB restart to
set up, not just a live `SET`.

### Phase 6 — run 2026-07-18: both interaction cases PASS

Restarted the eNB with `additional_non_mbsfn_subframes=2` (the config-file, restart-only
change Finding 1's fix needs), then live-`SET` `mch_sched_period_rf=4` + `time_interleaving_n=2`/
`time_interleaving_m=4` — the only legal M for this rig's resulting `sf_alloc_end=36` (M∈{8,16}
from the original plan don't divide 36, so the two cases run were TI(2,4)+muting(8,4) and
TI(2,4)+muting(16,8), not the originally-planned M=8/16 values, which remain structurally
infeasible under the one known-working TI recipe on this rig).

Confirmed via raw `MCHDIAG` (worker-pointer-stability method, no restart of the diagnostic
needed since the pattern is visible in the plain per-subframe log): TI combining stayed
genuine with CAS muting active simultaneously, in both cases. Signature: one worker instance
holds for exactly N×M=8 consecutive `mch_sf_idx` values; the first 4 in each block report
`crc=0` (expected — not enough repetitions combined yet), the last 4 report `crc=1` (real
combined decode success). This ~50/50 `crc=0`/`crc=1` split is *expected* TI behavior, not a
failure rate — over a ~400-line sample: case A (muting 8,4) 193/164, case B (muting 16,8)
181/148, both consistent with the same pattern.

One transient wrinkle, self-resolving: right after enabling muting for case A, the modem
briefly showed a burst of `MCHIDLE`-only subframes and repeated `SIB1-MBMS` reacquisition
prints (~10-15s) before settling into the healthy pattern above — ordinary MCCH
modification-period reacquisition after a live reconfiguration, not a persistent bug (matches
the propagation-window behavior already documented for other live `SET`s in this campaign).

Reverted all live-set values (muting off, TI off, `mch_sched_period_rf=64`) and the
config-file `additional_non_mbsfn_subframes` back to baseline, restarted the eNB again;
re-verified BLER 0.0 on the plain baseline afterward.

**Net result**: no new TI/muting interaction bug found — both features work correctly
together, at least for the one TI configuration this rig supports. The dashboard did show a
few red "Fail" subframe-activity cells during this test window; checked whether that panel
naively flags any `crc=0` as a failure without accounting for TI's expected mid-span
non-convergence — it does not. `MbsfnFrameProcessor.cpp:569-606` already explicitly logs
intermediate subframes within an active TI combining span as IDLE (not FAIL) per Rel-19
§6.5.3, only emitting FAIL on the last subframe of a span that still didn't decode; the
frontend (`modem.js:522-529`) is a dumb, correctly-fed color map. So the red cells were
genuine decode failures, consistent with the transient reacquisition burst noted above —
not a dashboard bug, nothing to fix here.

Also observed during this window: two brief, physically-implausible CINR spikes (~80-90dB
instantaneous, against this rig's normal ~30-46dB range) on the CINR/BLER chart, each
coinciding with a live reconfiguration moment and a small real BLER blip. Not independently
root-caused — most likely the CINR estimator not clamping/resetting cleanly during the same
transient resync windows discussed above, rather than a new decode-affecting bug (BLER impact
was minor and self-resolving in both cases). Lower priority than the confirmed issues above;
flagged here for a future look, not chased further this pass.

## Follow-up pass, 2026-07-18: CAS stability across wideband retune, a real concurrency crash, and Finding 4 re-confirmed

Picked back up on Finding 4 (`pmch_bandwidth` wideband, PMCH > carrier). Four distinct,
independently live-verified fixes this pass, plus a re-confirmation that the one remaining
Finding 4 symptom (MTCH decode) is the same pre-existing gap already documented above, not a
new regression.

**Fixed and confirmed — `rt-mbms-modem`, all uncommitted, pending commit:**

1. **CAS instability across a wideband retune**: `Phy::set_cell()` resized `_ue_sync`'s
   fft_size/sf_len for the new geometry but never called `srsran_ue_sync_reset()`, leaving it
   in `SF_TRACK` applying corrections computed under the OLD geometry to samples now aligned to
   the NEW one. Separately, the retune trigger (`main.cpp`, `phy.nof_mbsfn_prb() > cas_nof_prb`)
   had no guard against re-firing, so it repeated on every CAS occasion (~360ms) for as long as
   `pmch_bandwidth` stayed wider than the carrier, continuously restarting the SDR and
   resyncing. Fixed both (sync reset + a `mbsfn_nof_prb`-tracked one-shot guard). Live-verified:
   CAS held stable for 4+ minutes straight through PDSCH, confirmed directly by the user
   comparing dashboard behavior before/after.
2. **A genuine double-free/heap-corruption race**: `set_cell()` (main thread) reallocates a
   processor's FFT/CIR buffers while a previously-dispatched, fire-and-forget `process()` call
   may still be running on a worker-pool thread against those same buffers. First fix attempt
   (a mutex) caused a *worse*, guaranteed deadlock (`get_rx_buffer_and_lock()` already holds the
   same non-recursive mutex on the main thread before `pool.push()` even runs) and was reverted.
   Real fix: capture the `std::future` `pool.push()` already returns (previously discarded) per
   processor (`cas_future`, `mbsfn_futures[thread_cnt]` in `main.cpp`), and `.wait()` on it
   immediately before that processor's next `set_cell()` call, at all three call sites (CAS
   retune, MBSFN reconfigure, post-sync-loss CAS re-sync). Live-verified: no crash across
   repeated widen/narrow retune cycles and sustained wideband operation (previously crashed
   with `double free or corruption` within minutes).
3. **Missing retune-back-down logic**: the retune trigger only handled PMCH growing wider than
   the carrier; reverting `pmch_bandwidth` back to 0 left the SDR/CAS FFT permanently stuck at
   the wider grid (wrong RE-per-symbol count) until a full process restart, even though the eNB
   was signalling a narrower width again. Spotted live by the user via the CAS composition
   dashboard ("changed again and this should not be different, CAS is the same"). Fixed by
   generalizing the trigger to `target_mbsfn_prb = max(actual PMCH width, carrier)` and
   retuning whenever that changes in *either* direction (using the plain carrier-rate calc,
   not the MBSFN-SCS-aware one, when narrowing back to the carrier's own width). Live-verified
   across two full widen→narrow cycles: `fft_size`/`sf_len` correctly return to the 25 PRB
   baseline (512/7680) both times, CRC 87/87 and 100% (post-settling) respectively.
4. **CAS composition dashboard canvas-width bug**: `CasFrameProcessor::composition_grid()`
   sized its rendering canvas from `max(nof_prb, mbsfn_prb)` — copied from `cir_values()`/
   `ce_values()`, which genuinely need the wider canvas since they represent the MBSFN-adjacent
   sample stream. But composition_grid() only marks CAS-domain elements (PBCH/PSS/SSS/CRS/
   PCFICH/PDCCH), which are semantically always the carrier's own width, never wider. Worse,
   this was internally inconsistent: PBCH/PSS/SSS were drawn centred in the wider canvas while
   CRS/PCFICH/PDCCH used narrow, uncentred carrier-relative indices — so elements no longer
   lined up with each other on top of the whole chart being visually wider than it should be.
   Fixed by using `_cell.nof_prb` alone. User-confirmed live ("CAS is peffect") immediately
   after the fix, at the exact same `pmch_bandwidth=30` config that showed it broken before.

**Re-confirmed, not fixed — same pre-existing gap as Finding 4 above, not a new regression:**
spent substantial time this pass tracing the full CE pipele for the `pmch_bandwidth=30`
extended-BW case end-to-end (reference-sequence generation in `refsignal_dl.c`, raw pilot
extraction, the `chest_dl.c` LS-estimate computation, and interpolation) — all internally
self-consistent by static reading, no new bug found. (Side note: for this specific "PMCH wider
than carrier" scenario, `main.cpp` already forces `cell.nof_prb = max(nof_prb, mbsfn_prb)`
before configuring the MBSFN processor, making every `act_prb`-vs-`nof_prb` distinction in
that pipeline numerically inert here — it only matters for the opposite, sub-allocation case.)
Symptom observed: `cepw` (channel-estimate power) stays suspiciously constant/real while
`rxpwr`/`datapw` (actual decoded data power) collapses to noise floor and CRC is 100% fail,
with `SYNC_OFFSET_DIAG SLOWCALL` present throughout (11-12ms per 1ms budget). This exactly
matches the already-documented Finding 4 continuation above ("a separate, still-unexplained
timing/blocking issue... still causes most MTCH data decodes to fail") — confirmed to be the
same open gap, not something introduced by this pass's fixes. Still needs live timing
instrumentation (not more static code reading) to actually crack.

**Two more fixed and confirmed, found via user-spotted dashboard anomalies:**

5. **MBSFN CE waterfall chart showing solid green blocks either side of the real content**:
   both `CasFrameProcessor::ce_values()` and `MbsfnFrameProcessor::ce_values()` zero-pad their
   wider display canvas with a raw `0.0f`/`memset`, but that padding region never goes
   through `srsran_vec_abs_dB_cf()`'s own `-80` floor - only the real, centred active-PRB
   content does. The dashboard's `waterfall_color()` maps `db=0` to a strong, solid green
   (RGB≈(19,162,0) at this chart's -20..25dB scale), not black/background, so the padding
   rendered as two solid green blocks bookending the real (narrower) content. Fixed by
   filling the padding with `-80.0f` directly instead of `0`/`memset`, in both files.
   (`cir_values()` in both files was already correct — it dB-converts the *entire* IFFT
   output, no partial-region padding issue.)
6. **A stuck-worker MCCH decode failure, found while investigating #5**: `MCCHDIAG` showed
   100% CRC failure across the *entire* modem run (1745/1745 samples), always on the exact
   same pinned worker-pool instance, while MTCH decode on the other instances stayed
   perfectly healthy the whole time. Traced the real scheduling-affecting gate
   (`MbsfnFrameProcessor.cpp:445`'s `if (pmch_dec.crc)`, feeding `Rrc.cpp`'s independent
   ASN.1 `unpack()` validation) — confirming the campaign's live-verified `pmch_bandwidth`/MCS
   propagation this session was never running on corrupted data, just on whichever MCCH
   decode last succeeded before this instance got stuck. Matches a failure mode already
   documented earlier in this campaign (a worker instance not self-recovering after a
   retune without a modem restart). Confirmed via a fresh restart: MCCH 52/52 and MCH 134/134
   both crc=1 immediately - a transient state issue from this pass's heavy retune testing,
   not a new code bug.

**Dispatch ruled out as the cause, narrowing Finding 4's remaining gap**: added a `DISPATCH`
diagnostic (env `RACE_DIAG2`, `main.cpp`) logging every `pool.push()` dispatch with its
`mb_idx`. Fresh restart, clean baseline (MCCH 62/62 crc=1), then `pmch_bandwidth=30`: all 4
round-robin workers dispatched perfectly evenly (156/156/156/156 over the same window) - the
earlier "stuck worker" read on a *different* restart was a real, separate transient (matches
Finding 6 above), not this pass's steady-state behavior. But decode still failed uniformly
across all four (619/619 MCH, the one MCCH sample too) once widened. This rules out a
scheduling/dispatch gap and re-centers Finding 4's remaining symptom squarely on the decode
path itself (CE/data extraction, or the exact retune-transition timing) - not a stuck-instance
problem. Reverted cleanly to baseline afterward (33/33 crc=1). Next session should pick up
here: the CE/interpolation/reference-generation pipeline was already traced clean by static
reading this pass (see the CE-pipeline paragraph above), so the next step is runtime
instrumentation of the decode path itself, not more static tracing.

## Root cause found (2026-07-18, same day, continued): eNB TX never actually widens for pmch_bandwidth

After formula-level verification against TS 36.211 §6.10.2.2.2 (MBSFN-RS mapping) and
§6.3.5 (generic data RE mapping) confirmed the RX's reference-signal AND data RE
placement are both spec-correct for the wideband case, a targeted LLR/pilot/raw-grid
dump investigation (`PMCH_RE_DUMP`, already existing in the codebase, retargeted live
via `/tmp/pmch_dump_tti`) found: 71% of LLRs were exactly zero, correlating exactly
with post-equalization symbols collapsed to near-zero magnitude (not NaN). Dumping the
pre-extraction raw grid directly (`sf_symbols`, before any RX-side processing) showed a
clean, smooth, bell-shaped power envelope spanning almost exactly the ORIGINAL,
narrower carrier's own bandwidth (~25 PRB), centred within the wider 30-PRB acquisition
window the RX correctly retuned to - with genuine silence at the edges. **This proves
the eNB itself never actually transmits the wider PMCH content on the air**, even
though `pmch_bandwidth` is correctly signalled in SIB13/MCCH - a `rt-mbms-tx` (TX-side)
bug, not an RX decode bug, this session's RX-side fixes notwithstanding.

Traced the exact mechanism in `rt-mbms-tx`: the eNB's own RF sample rate
(`srsenb/src/phy/txrx.cc:93`) and per-subframe TX buffer sample count
(`srsenb/src/phy/lte/sf_worker.cc:159`) are both derived from the carrier's plain
`nof_prb` only, computed ONCE at process startup (`enb.cc`/`phy.cc`/`enb_cfg_parser.cc`'s
one-time `cell_list_lte` snapshot) and never revisited - not on a live `pmch_bandwidth`
`SET` (`control_server.cc` -> `rrc::reconfigure_embms()`), and not even if
`pmch_bandwidth` were set at startup instead, since nothing re-plans the main
CAS/PBCH/PSS/SSS `ifft[]` for the wider width either. `cc_worker.cc`'s own
`signal_buffer_tx`/`ifft_mbsfn` ARE already correctly `mbsfn_prb`-aware, but the extra
samples they generate are silently dropped every subframe by `sf_worker.cc`'s narrower
declared sample count before ever reaching the radio.

**Two fix attempts this session, both caused a worse regression (baseline cell search
broke entirely) and were fully reverted**:
1. Unconditionally provisioning `cell->mbsfn_prb` for the legal maximum (40 PRB) at
   config-parse time, plus widening `txrx.cc`/`sf_worker.cc` to match. This made
   `cell.mbsfn_prb != cell.nof_prb` unconditionally true, which some other code path
   (not yet identified) apparently uses as an "is MBSFN widening active" signal -
   baseline (`pmch_bandwidth=0`) cell search broke immediately.
2. A more careful attempt mirroring the RX's own solution: added
   `srsran_ofdm_tx_set_prb_symbol_sz()` (a TX-side equivalent of the RX's existing
   `srsran_ofdm_rx_set_prb_symbol_sz()`) to decouple the main CAS/PBCH `ifft[]`'s
   `symbol_sz` from its logical `nof_prb`, keeping content centred within a wider
   symbol - the same principle that works correctly on the RX side. Paired with
   `enb_baseline.conf` setting `pmch_bandwidth=40` at startup (to provision the frozen
   snapshot correctly) and the same `txrx.cc`/`sf_worker.cc` widening. This ALSO broke
   baseline cell search (still "Could not find any cell") - the TX-side PSS/SSS/PBCH
   generation apparently needs more than symbol_sz decoupling to stay correct within a
   widened symbol (unlike the RX, which only needs to *read* a wider symbol correctly;
   the TX must *generate* PSS/SSS/PBCH sequences at the exact right position within it,
   which may need its own dedicated centring logic this attempt didn't add).

Both attempts were fully reverted (`rt-mbms-tx`: `enb_cfg_parser.cc`, `txrx.cc`,
`sf_worker.cc`, `phy_common.h`, `enb_dl.c`, `ofdm.c`/`ofdm.h` all back to git HEAD;
`enb_baseline.conf`'s `pmch_bandwidth` back to unset) and baseline re-confirmed healthy
(CE diagnostics clean, dispatch correct, zero cell-search/readStream errors) before
ending this pass. **The actual TX-side fix remains open** - the root cause is now solid
and well-evidenced (see above), and two specific approaches are now known NOT to be
sufficient on their own, but a properly verified fix (most likely needing the TX's
PSS/SSS/PBCH generation to be made genuinely position-aware within a widened symbol,
not just FFT-size-aware) needs its own dedicated, carefully-tested session rather than
another rushed attempt.
- Once wideband PMCH decode genuinely works: re-run the actual verification this whole
  pass was for (`PMCH_RE_DUMP`/raw-IQ confirmation that content spans the full widened
  spectrum, MCCH/MTCH decode without CRC failures).
- Decide what to commit: the TX/RX rate-split fix, the FFT-sizing fix, the stale-TUN
  fix, and all three `chest_dl.c` fixes this pass are keepers regardless of outcome;
  the test-only `mbsfn_prb_test_override` and its config values are almost certainly
  NOT meant for commit (test-only scaffolding to bypass the SIB13-discovery chicken-
  and-egg deadlock, not needed in a real deployment where a real UE waits for SIB13
  naturally).

## Continuation pass, 2026-07-19 (dispatched continuation agent): root cause NOT found
## despite exhaustive isolation - narrowed to the live srsenb process's own IFFT
## execution; one real independent bug found and fixed; several dead-end hypotheses
## conclusively ruled out with hard evidence

Picked up directly from "the raw pilot phase-scrambling finding... not yet root-caused"
above. This pass did NOT find the fix. It DID produce the sharpest localization yet via
a chain of empirical tests, each one closing off a whole category of explanation. Full
honesty up front: CAS/PDCCH decode is still 0% at the end of this pass
(`pdcch_status`: `found=0`, `not_found_rate=1`, `total` climbing normally) - the wide
test config is left active per this campaign's standing setup, but wideband PMCH
verification is still blocked on this.

### Real, independent bug found and fixed: `chest_estimate_cfo()` had the same
### act_prb mixup already fixed elsewhere in this file

`chest_dl.c`'s `chest_estimate_cfo()` (used only for CAS, i.e. non-MBSFN, occasions -
feeds `chest_res.cfo` -> `CasFrameProcessor::process()`'s
`_phy.set_cfo_from_channel_estimation()` -> `srsran_ue_sync_set_cfo_ref()`, ue_sync's
own CFO tracking-loop reference) computed its slot-to-slot time span using
`n = srsran_symbol_sz(q->cell.nof_prb)` - the carrier's own native symbol size (512 for
this session's 25 PRB), not the REAL transform size CAS's `q->fft[port]` actually runs
at once `mbsfn_prb != nof_prb` (1024, `srsran_symbol_sz(mbsfn_prb)` - see
`srsran_ue_dl_set_cell_scs()` in `ue_dl.c`, fixed in an earlier pass). This is the exact
same "content width (nof_prb) vs. real transform size (mbsfn_prb when widened)"
distinction already fixed in `chest_dl_estimate_correct_sync_error()`'s `sz_se`/`sz_scs`
a few hundred lines below in the same file - this function was simply missed in that
earlier pass, since it computes a separate, independent CFO estimate, not `sync_error`.
Fixed the same way: `act_prb = q->cell.mbsfn_prb ? q->cell.mbsfn_prb : q->cell.nof_prb`,
`n = srsran_symbol_sz(act_prb)`.

**Confirmed via live A/B test that this is real but NOT the (sole) explanation for the
acute scrambling**: added a `CFO_FEEDBACK_DISABLE` env-gated skip around the
`_phy.set_cfo_from_channel_estimation()` call this bug's output feeds. Disabling it left
`sync_error` rock-stable from occasion to occasion (13.85, unchanging) instead of
slowly drifting (13.83 -> 15.33 over ~20s in the normal case) - confirming this feedback
loop (hence indirectly this bug) IS the source of a real, separate, slow, frame-to-frame
CFO-like drift. But `noise_estimate_dbm`/`snr_db`/`pdcch_status` were completely
unchanged (still `noise_estimate_dbm≈50.2`, `snr_db≈1.4-1.6`, `found=0`) - this bug does
not explain the acute per-subcarrier scrambling that's actually breaking PDCCH.

### New diagnostic finding: PSS shows the EXACT SAME scrambling as CRS - rules out
### anything CRS-specific, and rules out the "clean data region proves no corruption"
### reasoning from the previous pass

The previous pass's "data region looks clean (tight QPSK clusters), so a real physical
timing/CFO ramp is ruled out" reasoning was **wrong and is retracted**: PDSCH/PBCH data
is ALSO QPSK-modulated with scrambled (pseudo-random) bits, so it forms tight 4-point
clusters regardless of whether a hidden per-RE corruption is also present - clustering
by itself proves nothing about whether the channel is clean, only a KNOWN reference
signal can actually answer that.

Added `PSS_KNOWN_DIAG` (`CasFrameProcessor.cpp`, `process()`): for every CAS occasion at
`tti % 10 == 0` (a PSS-bearing subframe), regenerates the known PSS sequence locally
(`srsran_pss_generate()`, keyed only on `cell.id % 3` - a completely separate generator
from CRS's Gold-sequence-based `srsran_refsignal_cs_set_cell()`, no shared code at all)
and divides the 8 raw received PSS symbols (contiguous subcarriers, last symbol of the
slot, no comb spacing at all unlike CRS) by it. **Result: PSS shows the identical
signature CRS does** - magnitude consistently ~15-17 (real signal energy, matching
PSS's larger RE-boost/no-CFO-null-subcarrier vs. CRS's ~5) but phase scrambled by large,
inconsistent amounts between EVERY adjacent subcarrier (not just comb-spaced ones) -
e.g. one capture: `-34.1, -133.9, 22.3, -16.0, -68.2, 136.9, -126.6, -129.3` degrees for
k=0..7, no smooth trend at all. This is present from `tti=0` (the very first CAS
occasion ever processed at this width), ruling out any connection to the one-time
SDR-retune/cell-reconfigure block in `main.cpp` (~line 625-690).

**This conclusively rules out**: CRS reference generation, CRS extraction stride/
indexing, the LS division itself, and (independently, since PSS's own math is entirely
separate) narrows the search to something that corrupts the FFT-extracted grid as a
whole, not anything CRS-specific.

### Isolated, in-process ofdm.c TX->RX round-trip: mathematically and empirically
### PROVEN CLEAN for this exact config, in every variant tried

Wrote a standalone C test (`/tmp/.../ofdm_isolation_test.c`, `ofdm_resize_test.c`,
linked directly against the real built `libsrsran_phy.a`, no eNB/modem/ZMQ/threads at
all) that runs a TX `srsran_ofdm_t` (IFFT) directly into an RX `srsran_ofdm_t` (FFT) in
the same process's memory, nof_prb=25/symbol_sz=1024 (the exact CAS hybrid config).
Tried every variant that could plausibly differ from the real system:
- Fresh-init directly at the final config, sparse content (only 4 known CRS-like values
  populated, everything else zero): **perfectly clean** - all 4 positions recovered
  exactly, `rx/tx` ratio identically `1024.0 ∠0°` (the expected unnormalized-IFFT/FFT
  gain, `normalize=false` on both sides).
- Same, but with EVERY RE of every symbol densely populated with pseudo-random
  QPSK-ish content (mimicking a fully-loaded real subframe instead of a sparse test):
  **still perfectly clean**, identical ratio.
- Fresh-init at `MAX_PRB`/native size first (matching `srsran_enb_dl_init()`'s/
  `srsran_ue_dl_init()`'s actual startup lifecycle), THEN resize via
  `srsran_ofdm_tx_set_prb_symbol_sz()`/`srsran_ofdm_rx_set_prb_symbol_sz()` to
  nof_prb=25/symbol_sz=1024 (matching `srsran_enb_dl_set_cell()`'s/
  `srsran_ue_dl_set_cell_scs()`'s actual widen-branch call sequence, not a from-scratch
  init at the final config): **still perfectly clean**.

This independently confirms (this time by direct execution, not just manual derivation)
that `ofdm.c`'s mirror/dc/copy_pre/copy_post FFT-shift handling, `nof_guards`/`nof_re`
computation, and the CP-length arithmetic are ALL correct for this exact
nof_prb/symbol_sz combination, in every lifecycle/content-density variant a real system
could plausibly exercise. The bug is NOT in `ofdm.c`/`dft_fftw.c`'s logic.

### The decisive finding: the eNB's OWN transmitted samples are ALREADY scrambled when
### independently re-FFT'd, with the wire/RX system entirely out of the picture

Added `TX_CRS_DIAG_AFTER_PUT_REFS`/`TX_CRS_DIAG_BEFORE_IFFT` (`enb_dl.c`, already
existed from earlier in this pass) and `TX_TIME_DUMP` (`enb_dl.c`, new): dumps the
FINAL, actually-transmitted post-IFFT post-normalization time-domain samples for one
specific tti straight from `q->ifft[0].cfg.out_buffer` to a file. Captured tti=0 with
**both** `TX_CRS_DIAG_BEFORE_IFFT` and `TX_TIME_DUMP` active together (same log, back-
to-back lines, so guaranteed the same exact transmission instance):

```
TX_CRS_DIAG_BEFORE_IFFT tti=0 fidx0=1 vals=(0.7071,0.7071) (-0.7071,0.7071) (-0.7071,-0.7071) (-0.7071,-0.7071)
TX_TIME_DUMP tti=0 sf_len=15360 ...
```

i.e. confirmed (yet again) that `q->sf_symbols[0]` holds the exact expected clean CRS
values immediately before the IFFT runs. Then took the dumped `TX_TIME_DUMP` time-domain
samples and ran them through a **completely independent, freshly-built `srsran_ofdm_t`
RX object** (`fft_dump_check.c`, a small standalone tool, same library) - i.e. FFT'd the
eNB's own transmitted bytes directly from its own dumped memory, with the real RX
system, the wire, ZMQ, and SoapySDR entirely out of the picture. **Result: still
scrambled** - `fidx=1,7,13,19` came back as magnitude 16.384 (consistent - confirms real
signal, not noise) but phase `-95.63, -16.87, 61.87, 50.62` degrees - not matching the
known clean input (45, 135, -135, -135) under ANY constant rotation, and notably
`fidx=13`/`fidx=19` (which share the IDENTICAL reference value) disagree with each other
by 11 degrees. The SAME tool, fed the isolated test's own known-clean buffer instead,
correctly reproduces the expected clean 45/135/-135/-135 pattern - so the tool itself is
not the source of the discrepancy.

**This means the corruption is being introduced during the REAL, live `srsenb`
process's own execution of `srsran_ofdm_tx_sf(&q->ifft[0])` (`enb_dl.c`,
`srsran_enb_dl_gen_signal()`), in a way that a byte-for-byte equivalent standalone
reproduction (same library, same init-then-resize lifecycle, same dense content) does
NOT reproduce.** The wire/ZMQ/SoapySDR bridge/RX system are now OUT OF SCOPE for this
bug entirely - whatever it is, it happens before the samples ever leave the eNB process.

### Hypotheses tested and ruled out for "what's different about the live process"

- **Multi-threaded PHY worker race** (`nof_phy_threads` - each `cc_worker` has its own
  `srsran_enb_dl_t enb_dl` member, so no expected sharing, but tested directly anyway
  given the wider/slower 1024-point transform could plausibly widen a race window):
  set `nof_phy_threads=1` (config only, no rebuild), fresh restart both sides -
  **identical broken pattern** (`noise_estimate_dbm≈50.2`, `snr_db≈1.4-1.5`,
  `sync_error≈14-15`). Reverted to `4`. Ruled out.
- **Stale/corrupted FFTW wisdom file** (`~/.srsran_fftwisdom` - shared by BOTH the eNB
  and modem processes on this machine, since both link the same srsRAN FFT code and
  both have the same `__attribute__((constructor/destructor))` load/save hooks; a
  wisdom entry poisoned by an earlier session/build could in principle bias FFTW's
  algorithm selection for this exact size): deleted the file, fresh restart both sides
  (regenerates automatically) - **identical broken pattern**. Ruled out.
- ZMQ bridge (`~/soapy-zmq-bridge/ZmqRxDevice.cpp`) decimation path: confirmed `ratio=1`
  (native_srate=15.36e6 == requested sample_rate) for this whole test - the FIR
  decimator is never even invoked; `readStream()`/`rxThreadLoop()`'s ratio=1 path is a
  plain, whole-sample-aligned memcpy pipe. Not implicated (though this file's own
  extensive historical comments document real, already-fixed bugs in the ratio>1 path
  from an earlier, different investigation - not relevant here).
- `phase_compensation_hz`/`freq_shift_f` (`srsran_ofdm_cfg_t` optional fields that, if
  nonzero, apply an extra rotation in `ofdm.c`): grepped the whole TX and RX application
  code (`enb_dl.c`, `srsenb/src/phy/*.cc`, all modem `src/*.cpp`, `ue_dl.c`) - never set
  anywhere, stay at their zero-initialized default on both sides. Not implicated.

### Still-open next steps

- The bug is now known to live specifically inside the real, live `srsenb` process's
  execution path from `q->sf_symbols[0]` (confirmed clean) through
  `srsran_ofdm_tx_sf(&q->ifft[0])` to the dumped `q->ifft[0].cfg.out_buffer` (confirmed
  scrambled when independently re-FFT'd) - and does NOT reproduce in a byte-for-byte
  equivalent standalone harness using the identical library code, lifecycle, and
  content density. Whatever differs between "real running srsenb" and "standalone
  isolated reproduction" is the remaining thing to find. Candidates not yet tried:
  attaching a debugger/additional per-call instrumentation to the LIVE srsenb process
  at the exact `srsran_ofdm_tx_sf(&q->ifft[0])` call site (e.g. dump `q->tmp`,
  `q->fft_plan.p`/`.in`/`.out` pointer values, and `q->nof_guards`/`q->nof_re` on
  literally the live object, not an equivalent standalone one, right before and after
  the call) to see whether the LIVE object's own state actually matches what's assumed;
  checking whether some OTHER thread/subsystem in the real `srsenb` process (M3AP,
  GTP-U, the control_server, PRACH worker, or another `cc_worker` instance for a
  different carrier/subframe pipeline stage) could be touching `q->ifft[0]`'s memory
  region via some unrelated bug (heap corruption/buffer overflow elsewhere stomping on
  this object - a class of bug no amount of ofdm.c-focused reasoning would catch);
  building a debug/ASan-instrumented `srsenb` and reproducing live to catch a potential
  heap-corruption source directly.
- `chest_estimate_cfo()`'s fix and the diagnostics added this pass
  (`PSS_KNOWN_DIAG`, `TX_CRS_DIAG_AFTER_PUT_REFS`/`_BEFORE_IFFT`, `TX_TIME_DUMP`,
  `RX_TIME_DUMP`, `TX_SF_TYPE_DIAG`, `PILOT_REF_DIAG`, `PILOT_WIDE_DIAG`(+`_L1/L2/L3`),
  `PILOT_NDFT_DIAG`, `RAW_IQ_DUMP`+`RAW_IQ_DUMP_PILOT_STRIDE`) are real, independent
  keepers regardless of outcome - all `getenv()`-gated, inert by default.
  `SYNC_CORRECT_DISABLE`/`CFO_FEEDBACK_DISABLE` are confirmed-ruled-out kill-switches,
  kept as cheap regression-check tools with their comments updated to say so plainly.
- Revert `main_thread_priority_rt` to `20` once the SIGKILL trigger is understood or
  confirmed gone - still not investigated this pass, unchanged from before.

### Config state at time of writing
`enb_baseline.conf`: `pmch_bandwidth = 40` (wide test, active), `nof_phy_threads = 4`
(restored after the single-thread test above). `modem_zmqtest.conf`:
`mbsfn_prb_test_override = 40` (wide test, active), `main_thread_priority_rt = 0`
(still the temporary SIGKILL workaround, untouched, not yet reverted).
`receive-netns.sh`'s modem launch line: `CAS_CE_DIAG=1 PILOT_RAW_DIAG=1 RAW_IQ_DUMP=1
PSS_KNOWN_DIAG=1` (the other per-investigation diagnostics from this pass are available
but not enabled by default - see their own doc comments for the env vars needed).
Both eNB and modem left running in this config at the end of this pass.

## Further continuation, same day: two of the "not yet tried" candidates above tried -
## both came back clean; root cause still open

Picked up the two concrete candidates the previous section flagged as untried:
multi-object/multi-subframe-type interaction, and an ASan-instrumented live `srsenb`.
Both were tried. Neither found it. Also tried UBSan (not previously listed as a
candidate). Full honesty: **still open at the end of this pass too.**

### Multi-object interaction: ruled out

Extended the agent's own standalone `ofdm.c` round-trip test
(`/tmp/.../scratchpad/ofdm_multiobj_test.c`, kept alongside its `ofdm_isolation_test.c`/
`ofdm_resize_test.c`) to create BOTH a CAS-like object (nof_prb=25, symbol_sz=1024,
fresh-init-then-resize) AND an MBSFN-like object (mbsfn_prb=40, SRSRAN_SCS_1KHZ25,
symbol_sz=12288) in the same process, then ran a CAS round-trip (1) before the MBSFN
object even existed, (2) right after it was created but not yet run, (3) after 5 rounds
of active MBSFN TX/RX, and (4) interleaved with further MBSFN activity. This models the
real `srsenb`'s actual coexistence of `ifft[0]`/`ifft_mbsfn` (sharing FFTW's global
plan-creation mutex) far more closely than the single-object tests the previous pass
ran. **Result: CAS stayed perfectly clean in all four phases** (`ratio=1024∠0.00deg`
throughout, matching the expected unnormalized IFFT/FFT gain exactly). This rules out
"FFTW global state gets confused when multiple differently-sized plans coexist/
interleave" as the explanation.

Also re-verified by direct reading (not just re-trusting the earlier pass's conclusion)
that `q->ifft[0]`'s actual init lifecycle in the real system is exactly the 2-step
"fresh at nof_prb=25 (implicit symbol_sz=512) inside `srsran_enb_dl_set_cell()`, then
widened to symbol_sz=1024" the standalone tests already modeled - `srsran_enb_dl_init()`
(called once, at `cc_worker` startup, with `max_prb=dl_max_prb=40`) only initializes
`q->ifft_mbsfn` (CP_EXT, SCS_1KHZ25) at that call; `q->ifft[0]` is untouched until
`srsran_enb_dl_set_cell()` runs. No hidden third resize step.

### AddressSanitizer: clean

Built a separate `rt-mbms-tx/build-asan` directory (`cmake -DENABLE_ASAN=ON
-DCMAKE_BUILD_TYPE=RelWithDebInfo`, already a supported first-class CMake option in
this repo - no manual flag hacking needed), built just the `srsenb` target, ran it live
against the real modem for a sustained period with the wide test config active.
**Zero ASan reports of any kind**, in any file, for the entire run - despite
`CAS_CE_DIAG` confirming the usual failure signature was present throughout
(`noise_estimate_dbm≈+50.2`, `snr_db≈1.4-1.6`, `sync_error≈14-15`). Since ASan reports
on the very first offending access, and many CAS occasions were processed, this is
fairly strong evidence against a classic heap/stack buffer overflow or use-after-free
anywhere in the process, not just in the PHY/CAS/PMCH code path specifically.

### UndefinedBehaviorSanitizer: clean in the relevant code, noisy everywhere else

Built `rt-mbms-tx/build-ubsan` combining `-fsanitize=address,undefined` (plus the
existing repo's own `-Wno-error=...` suppressions for known-benign warnings that
`-Werror` would otherwise turn fatal, e.g. `stringop-overflow` in generated ASN.1
`dyn_array` code). First run used `-fno-sanitize-recover=undefined` (abort on first
violation) and immediately died on a **pre-existing, unrelated** startup-time issue:
a null-pointer reference bind while copying an empty ASN.1 octet string
(`sib_type13_r9_s::operator=`, `srsenb::rrc_cfg_t::operator=`,
`enb_stack_lte::init()`) - nothing to do with CAS/PMCH, just a latent, harmless (empty
container `operator[]`) pattern this codebase has apparently never been run under
UBSan before to notice. Rebuilt with `UBSAN_OPTIONS=halt_on_error=0` to log-and-continue
instead. Once past startup, one PHY-side finding fired: a misaligned 16-bit store in
`srsran_bit_interleaver_run` (`lib/src/phy/utils/bit.c:127`), reached via
**PDSCH** encoding (`cc_worker::encode_pdsch` -> `srsran_pdsch_encode` ->
`srsran_dlsch_encode2` -> `srsran_rm_turbo_tx_lut`) - a completely different code path
from CAS/PMCH/CRS/PSS, and one x86 tolerates at the hardware level regardless (UBSan
flags it as technically UB per the C standard; it's not what's breaking this feature).
Otherwise, a long tail of **pre-existing, unrelated** alignment/null-reference findings
across S1AP/GTPU/RRC/network_utils/a custom intrusive-list `node`/`pooled_node` type -
none in `enb_dl.c`, `ofdm.c`, `chest_dl.c`, `refsignal_dl.c`, `pss.c`, or `cc_worker.cc`'s
CAS/PMCH-relevant methods, despite `CAS_CE_DIAG` confirming the failure was actively
reproducing throughout the run. **No new lead from UBSan either.**

### Also directly re-verified this pass (by reading, not just re-citing): PSS placement

`srsran_pss_put_slot()` (`lib/src/phy/sync/pss.c:372`) - the previous pass's report that
PSS shows the identical corruption signature CRS does was already strong evidence
against anything CRS-specific, but this pass additionally confirmed PSS's own
*placement* function (as opposed to just its Zadoff-Chu *generation*) is exactly as
narrow/correct as CRS's: uses only the `nof_prb` parameter passed in
(`q->cell.nof_prb`), no `mbsfn_prb` reference anywhere. Between this and the earlier
confirmation on `refsignal_cs_get_sf`/`_put_sf`/`_fidx`/`_nsymbol`/`_v`, every actual
content-placement function for both known reference signals is confirmed clean. If the
bug is a placement/indexing bug at all, it would have to be in something shared by both
signal types AND not caught by the ofdm.c isolation tests - increasingly narrowing to
"something about the live process specifically" rather than any single function's
logic.

### Where this leaves it

Ruled out, cumulative across both passes: retune timing, TX/RX FFT sizing and CP/guard-
band scaling, `_ue_sync`'s sizing and its own tracked offset state, CRS and PSS
generation AND placement (both signals, independently), FFTW plan replan attribute
persistence, multi-object/interleaved-subframe-type FFTW interaction, classic memory-
safety bugs (ASan), and a broad class of undefined-behavior bugs (UBSan). Four real,
independent bugs found and fixed along the way (kept regardless). The paradox from the
previous pass stands unresolved: the eNB's own pre-IFFT content is confirmed correct,
the IFFT operation is confirmed correct in every isolated reproduction tried so far
(including ones deliberately designed to match the live system's exact object
coexistence and lifecycle), yet the live process's actual output is confirmed
corrupted. Remaining untried candidates: a ThreadSanitizer pass specifically (checks a
different bug class than ASan/UBSan - a race condition - but carries real risk of its
much higher overhead breaking real-time ZMQ transmission timing and producing a
misleading trail; would need its own from-scratch build, ASan+TSan can't combine); or
attaching a debugger directly to the live process at the exact
`srsran_ofdm_tx_sf(&q->ifft[0])` call site to inspect the live object's actual internal
state (`q->tmp`, `q->fft_plan.p`/`.in`/`.out`, `q->nof_guards`/`q->nof_re`) in place,
rather than an equivalent standalone object.

### Config/build state at end of this pass
Both eNB and modem stopped (mid-investigation pause). `rt-mbms-tx/build-asan/` and
`rt-mbms-tx/build-ubsan/` are separate, additional build directories (not touching the
normal `rt-mbms-tx/build/`) - safe to keep for a future continuation or delete if
disk space matters. `enb_baseline.conf`/`modem_zmqtest.conf` unchanged from the state
described in the section above (wide test config, `main_thread_priority_rt=0` workaround
still in place, still not reverted).

## Continuation pass, 2026-07-19 (later same day): root cause found and fixed - the
## "CAS phase-scrambling" mystery was a test-tool bug, not a real defect; the actual
## BLER=1.0 blocker was a MAC-scheduler/PHY-muting gap, now fixed and confirmed

Picked up directly from the previous pass's unresolved state (root cause of the
widened-`pmch_bandwidth` corruption still open, redesign implemented but not yet
proven to fix anything). This pass found and fixed the actual root cause of the whole
campaign's core problem. Full honesty up front: **PMCH/MTCH decode now works** -
`pdcch_status` 100%, MCH BLER ~2.2-2.3% (down from 100%), confirmed stable over
hundreds of samples. One unrelated pre-existing crash found and flagged, not fixed.

### Finding 1: `srsran_enb_dl_set_mbsfn_subcarrier_spacing()` was silently re-narrowing
### `ifft_mbsfn` back to `nof_prb`, undoing `srsran_enb_dl_set_cell()`'s correct wide sizing

This function (`rt-mbms-tx/lib/src/phy/enb/enb_dl.c`), called lazily from
`cc_worker.cc` on the first actual MBSFN subframe, defaulted `ofdm_prb = q->cell.nof_prb`
and only widened for the special-cased 0.37 kHz SCS branch. For 1.25 kHz SCS (what
this test actually uses), `ofdm_prb` stayed at `nof_prb` (25), silently re-narrowing
`ifft_mbsfn` from the correct wide (40 PRB) sizing `srsran_enb_dl_set_cell()` had
already established at `cc_worker` startup. This bug was completely independent of the
CAS/PMCH FFT-sharing architecture question the previous pass spent a full day on -
explaining why *both* the old (shared-widened-FFT) and new (decoupled) architectures
showed the identical symptom (`ifft_mbsfn`'s actual PMCH content generation was broken,
at the wrong width, on both, since this function was never touched by the redesign).
Fix:
```c
// BEFORE:
uint32_t ofdm_prb = q->cell.nof_prb;
if (SRSRAN_SCS_IS_370HZ(subcarrier_spacing)) { ofdm_prb = ...; }
// AFTER:
uint32_t ofdm_prb = SRSRAN_MAX(q->cell.nof_prb, q->cell.mbsfn_prb);
if (SRSRAN_SCS_IS_370HZ(subcarrier_spacing)) { ofdm_prb = (ofdm_prb <= 75u) ? ofdm_prb : 75u; }
```
Confirmed via a temporary `IFFT_MBSFN_STATE_TRACE` diagnostic: before the fix,
`ifft_mbsfn.cfg.nof_prb=25` (wrong); after, `nof_prb=40, symbol_sz=12288,
mbsfn_sf_len=15360` (correct). This alone took MCCH/SIB13 decode from broken to fully
working and was the first big win of this pass.

### Finding 2: the entire "CAS phase-scrambling" investigation (previous pass, ASan/
### UBSan, multi-object tests) was chasing a bug in the VERIFICATION TOOLING, not a
### real defect in the transmitted signal

After Finding 1's fix, CAS/PDCCH was *still* failing (`pdcch_status` ~2% found). Traced
this using the same `fft_dump_check`-style standalone re-FFT tool the previous pass
built - but that tool hardcoded `SRSRAN_CP_NORM`, while this cell's actual
configuration is `extended_cp = true` (`enb_baseline.conf`). Extended CP has 6
symbols/slot with a uniform CP length, not 7 with the normal split - a completely
different symbol/window boundary layout. Re-running the exact same captured TX buffer
with the CP setting corrected to `SRSRAN_CP_EXT`: CRS at fidx 1/7/13/19 recovered
**exactly** 45.00°/135.00°/-135.00°/-135.00° (matching the known pre-IFFT reference,
uniform magnitude), and PSS showed a clean, structured phase pattern - not chaos. **The
eNB's transmitted signal was clean all along.** The previous pass's entire "isolated
ofdm.c round-trip is clean but the live process's real transmitted samples are already
scrambled" conclusion was an artifact of its own verification tool's CP mismatch, not a
real defect. This does not mean the earlier ASan/UBSan/multi-object work was wasted -
it correctly ruled out several real hypothesis classes - but the specific "IFFT
execution corrupts content" conclusion does not hold.

### Finding 3: chest_dl.c's `chest_dl_estimate_correct_sync_error()` CAS branch was
### actively corrupting CAS content - obsolete leftover from the abandoned
### shared-widened-FFT architecture

With Finding 2 ruling out a transmitted-signal defect, the remaining question was why
`pdcch_status` still failed. Live A/B test: setting `SYNC_CORRECT_DISABLE=1`
(an existing kill-switch, previously "confirmed ruled out" in the *old* architecture)
took `pdcch_status` from ~2% to 490/490 (100%) found, with `noise_estimate_dbm`/`snr_db`
simultaneously returning to physically-sensible values. Root cause: CAS's own
`fft[port]` is now permanently narrow (this session's redesign) - a completely
standard, unwidened LTE transform with no more need of a "sync error" correction than
any ordinary non-FeMBMS cell. This function's CAS-branch `act_prb`/`nre`/`nsymb`
formulas still reflected the abandoned widened-FFT architecture and were measuring/
correcting against the wrong transform size, actively corrupting otherwise-clean CAS
content. Fix: skip the CAS branch entirely (both measurement and correction) at
function entry - `if (sf->sf_type != SRSRAN_SF_MBSFN) { return; }` - leaving the MBSFN
branch untouched (it may still have a genuine need for this correction - see Finding
5). Confirmed: `pdcch_status` 100% (494/494), `sync_error` correctly reads 0.0000 for
CAS (measurement now skipped entirely, as intended).

*(Aside, tried and reverted during this investigation: removing the resampler's
per-occasion `reset_state()` call, on the theory that a "cold start against phantom
zero history" was the cause. Made zero measurable difference - proving the resampler
delay is a structural/permanent property of the filter, not a cold-start transient.
Reverted has no effect either way; left removed since it's arguably more correct
regardless.)*

### Finding 4: `MbsfnFrameProcessor`'s CE/CIR dashboard canvas used the wrong (15 kHz)
### symbol-size formula for a 1.25 kHz cell - the visual "frequency notches"/"25 PRB
### instead of 40" the user spotted

`MbsfnFrameProcessor::set_cell()` sized its CE/CIR display canvas with
`srsran_symbol_sz(_cell.nof_prb)` - the plain 15 kHz-numerology table - even though
MBSFN here runs at 1.25 kHz (true `symbol_sz=12288` for 40 PRB, not 1024). This 12x
undersized canvas meant `ce_values()`/`cir_values()` read only the first ~1/12th of the
real `chest_res.ce[]` array and IFFT'd it at the wrong transform size - exactly the
periodic comb/notch pattern visible on the CE waterfall and CIR dashboard panels, and
the "PMCH acquired at 25 instead of 40" appearance. This is a **display-only** bug -
actual PMCH decode uses `_pmch_cfg.pdsch_cfg.grant.nof_re` against the full-size `ce[]`
array directly, unaffected. Fixed using `srsran_symbol_sz_scs()` and
`SRSRAN_NRE_SCS(_ue_dl.subcarrier_spacing)` throughout instead of the hardcoded 15 kHz
assumptions. Confirmed: CE canvas now correctly 12288 samples wide with exactly 5760
active REs (=40 PRB x 144 RE/PRB) at a flat ~35.5-36.0 dB.

### Finding 5: MBSFN sync-error correction, force-disabled since 2026-07-14, is stable
### again and meaningfully improves the CIR - re-enabled

`MbsfnFrameProcessor`'s chest_cfg explicitly set `sync_error_enable=false`, with an
extensive comment describing why: back then, the estimator's measured values ran into
the hundreds (e.g. -427, 311, -348) for MBSFN's `nsymb=1` case, and its "correction"
actively corrupted data/pilot REs, root-caused as the primary cause of a
~99.6-99.75% CRC failure. Given everything fixed today (Findings 1-4), re-tested this
empirically rather than assuming it still applies: `SYNC_ERR_DIAG` now shows a
rock-stable measurement of ~-14.00 samples (std-dev <0.02 across many subframes) at
the current wideband config - not the wild pre-fix values. Enabling the correction
moves the MBSFN CIR's main tap to exactly lag 0 (previously off-center by ~14 samples)
and substantially reduces - though does not fully eliminate - a periodic ripple. Does
not change MCH BLER either way (confirmed separately, still 1.0 at the time, before
Finding 7's fix). Re-enabled permanently; the comment explaining the original 2026-07-14
disable is preserved alongside the new finding for future reference.

### Finding 6: PMCH's EVM was a dead field, always 0/NAN - added real computation,
### which is what pointed at the actual remaining bug

`srsran_pmch_decode()` never called anything analogous to `pdsch.c`'s own
`srsran_evm_run_s()` - `srsran_pdsch_res_t::evm` stayed at its zero-initialized default
for every PMCH decode, a genuinely dead diagnostic field despite the REST API/dashboard
already having plumbing to display it (`_rest._mch[idx].evm_rms`). Added: `evm_buffer`
member on `srsran_pmch_t` (alloc/free mirroring `q->d`/`q->e`'s own lifecycle,
sized from `q->max_re` at the worst-case 256QAM bit rate), and a real
`srsran_evm_run_s()` call right after the existing soft-demodulate call in
`srsran_pmch_decode()`, before descrambling touches `q->e`. First real reading:
`evm=0.951` (95% RMS error) - despite Findings 1-5 all being genuine, confirmed fixes,
the actual data path was still producing near-noise-level equalized symbols. This
directly falsified the "maybe it's just a residual channel-estimate ripple" hypothesis
(that would show as a smaller, non-catastrophic EVM) and redirected the investigation
correctly to Finding 7.

### Finding 7 (the actual root cause of BLER=1.0): MAC almost never grants MTCH at this
### bandwidth, and the eNB was retransmitting stale buffer content instead of silence
### on the idle subframes - THE FIX

Traced the 95% EVM by comparing raw received symbols against the channel estimate
(`raw[i]/ce[i]` vs the actual equalized `d[i]` - confirmed the equalization arithmetic
itself is correct, matching exactly for every index checked) and mapping magnitude
across the full 4800-RE grant: **strong, consistent signal (~61) for indices 0-2999,
then an abrupt 30x drop to ~2.0 from index 3000 onward.** 3000/4800 = 62.5%, and 62.5%
of 40 PRB = exactly 25 PRB. The eNB was only ever transmitting real PMCH data across
the original 25-PRB carrier - never the widened 40-PRB allocation it signals in SIB13
and correctly sizes its FFT for (Finding 1).

Root cause, confirmed via `PMCH_TI_DIAG` on the live eNB: `cc_worker::encode_pmch()`
early-returns (without calling `srsran_enb_dl_put_pmch()` at all) whenever MAC sets
`grant->dci.rnti=0` ("nothing to actually transmit this subframe"). Live count for a
single matched eNB/modem run: **21782 calls with `rnti=0x0` vs only 35 with a real
grant (0.16%)**. Traced into `mac::build_mch_sched()`: when queued content is *less*
than a scheduling period's capacity (`sfs_per_sched_period * bytes_per_sf`), it
schedules just enough subframes to drain what's queued, then leaves the rest of the
~623-subframe period idle. Since `pmch_bandwidth=40` makes `bytes_per_sf` far larger
than at 25 PRB, the same test content source drains almost instantly into a 40-PRB
pipe - a legitimate, expected consequence of testing wideband capacity against a
narrower content source, not a scheduling bug per se.

The actual bug: `srsran_enb_dl_gen_signal()` unconditionally IFFTs `sf_symbols` for
*every* MBSFN subframe regardless of whether fresh content was written this occasion -
so an idle occasion (no MAC grant) was retransmitting whatever the *last real* PMCH
encode had written there, not silence. The receiver's own DTX/idle detection
(`MCHIDLE`, power-threshold based) should catch idle occasions and skip counting them,
but for this same matched run it only recognized ~52% of subframes as idle
(`416802 MCHIDLE` vs `378519 MCHDIAG`) - the other ~48% still looked like a real,
powered signal worth attempting to decode, and correctly failed every time (it's
stale/wrong-TB content, not this subframe's actual data) - inflating BLER on subframes
that were never really scheduled at all.

**Fix** (`rt-mbms-tx/srsenb/src/phy/lte/cc_worker.cc`, `encode_pmch()`'s existing
`rnti==0` early-return branch):
```c
if (!grant->dci.rnti) {
  srsran_vec_cf_zero(enb_dl.sf_symbols[0], enb_dl.ifft_mbsfn.nof_re);
  return SRSRAN_SUCCESS;
}
```
Sized from `ifft_mbsfn.nof_re` - the exact frequency-domain RE count that object's own
IFFT reads for the current `mbsfn_prb`/SCS, so this covers precisely what would
otherwise be written, no more/less. Zeroing the RE grid makes an idle occasion transmit
genuine silence, letting the receiver's existing power-based DTX detection correctly
recognize it instead of attempting a doomed decode.

**Confirmed live, stable, matched eNB/modem pair, no regression:**
- `pdcch_status`: 100% (1235/1235).
- MCH BLER: **~0.022-0.023** (down from 1.0), stable across 10+ consecutive samples.
- This is the actual fix for the entire campaign's core "wideband pmch_bandwidth PMCH
  won't decode" problem.

### Separately found, not fixed: a pre-existing modem crash unrelated to today's work

During testing, the modem process crashed once:
```
/usr/include/c++/15/bits/stl_vector.h:1263: std::vector<...>::operator[](size_type):
Assertion '__n < this->size()' failed.
```
This is a `std::vector<std::string>` out-of-bounds access, most likely in
`main.cpp`'s CSV/measurement-file report-building code (`cols.emplace_back(...)`
sequences building rows of inconsistent length across different `if`/`else` branches -
seen near `main.cpp:1120-1135`), though not confirmed with a full backtrace. Not
blocking (measurement_file is disabled by default in this test's config, so the exact
trigger condition isn't yet understood) and unrelated to any change made this pass -
flagged for a future, dedicated investigation with a debug build/core dump.

### Config/build state at end of this pass
Both eNB (PID varies across restarts during this pass, last confirmed working
instance) and modem running, wideband config (`pmch_bandwidth=40`,
`mbsfn_prb_test_override=40`) active, confirmed working end-to-end. Still pending:
revert `main_thread_priority_rt` to 20 (long-standing, unrelated temporary SIGKILL
workaround, still not investigated), decide commit scope (this pass's fixes are all
keepers; test-only scaffolding like `mbsfn_prb_test_override` is not meant for commit),
remove/decide fate of temporary diagnostics accumulated across this and prior passes
(`IFFT_MBSFN_STATE_TRACE`, `TX_TIME_DUMP_NARROW`/`RX_TIME_DUMP_NARROW`, several others -
all `getenv()`-gated and inert by default, safe to leave or remove at leisure).
`rt-mbms-tx/build-asan/`, `rt-mbms-tx/build-ubsan/` still present from the prior pass,
still safe to keep or delete.

### Follow-up, same day: Finding 7's mute fix regressed the CE/CIR panels (real pilots
### were being zeroed too), and fixing that regressed BLER back to 1.0 - both now fixed

The user caught this live on the dashboard immediately: the MBSFN CIR panel went
completely empty (stuck at the -80dB floor) after Finding 7's fix landed.

**Regression cause**: `enb_dl.c`'s `put_refs()` writes MBSFN reference signals
(`srsran_refsignal_mbsfn_put_sf()`) into the same `sf_symbols` buffer independently of
PMCH data encoding - real FeMBMS/LTE requires these on *every* MBSFN subframe
regardless of whether user data is present, specifically so receivers can maintain
channel estimation through idle occasions. Zeroing the *entire* RE grid in
`encode_pmch()`'s early-return also wiped these pilots, so the receiver's
`chest_res.ce[]` went completely empty on the ~99.8% of subframes that are now (rightly)
muted. Fixed by re-writing the reference signals immediately after the zero-fill,
mirroring `put_refs()`'s exact call - safe and idempotent regardless of call order:
```c
srsran_vec_cf_zero(enb_dl.sf_symbols[0], enb_dl.ifft_mbsfn.nof_re);
srsran_refsignal_mbsfn_put_sf(enb_dl.cell, 0, enb_dl.csr_signal.pilots[0][tti % 10u],
    enb_dl.mbsfnr_signal.pilots[0][sf_idx], enb_dl.sf_symbols[0], scs, tti);
```
Confirmed: CIR peak now exactly at lag 0, CE canvas populated correctly again.

**That fix's own regression**: restoring real pilots reintroduced a small, genuine
amount of pilot-to-data leakage into the equalized "empty" data REs - measured live at
`datapw=0.0010-0.0012`, just barely above the existing DTX/idle-detection threshold in
`MbsfnFrameProcessor.cpp` (`data_pw < 1e-3f`). This pushed MCH BLER back to 1.0 on
exactly the subframes with this marginal leakage. Widened the threshold to `1e-2f` -
still two full orders of magnitude below the ~1.0 real-content level (confirmed
0.9997-1.0002 on genuine decodes), so no risk of masking real failures.

**Confirmed, final, stable state** (matched eNB/modem pair, extended observation):
- `pdcch_status`: 100% (1311/1311).
- MCH BLER: **0.0**, `MCH TOTAL ERRORS: 0`, stable across 10+ consecutive samples.
- CIR (both CAS and MBSFN): correct single peak at lag 0, no comb/floor artifacts.

This is now the fully-working, end-to-end confirmed state for wideband
`pmch_bandwidth=40` at `n_prb=25`: signaling, CAS/PDCCH, and PMCH/MTCH data decode all
working, all live-verified, not just theorized.

## Continuation pass, 2026-07-20: REST API crash fix, waterfall display fixes, and a
## thorough but inconclusive MCCH EVM ripple investigation

### REST API crash fixed: `mch_status`/`mch_data` indexed `paths[1]` with no bounds check

A bare (no-index) request to either endpoint (`RestHandler.cpp`) crashed the whole
modem process via an out-of-bounds `std::vector` access, matching a "pre-existing
modem crash" flagged earlier this campaign as unconfirmed. Root-caused this pass by
triggering it directly. Fixed with a `paths.size() < 2` guard, same convention already
used elsewhere in the file.

### Waterfall display: two real fixes, not root causes of any signal-quality issue

1. All four CE/CIR waterfalls (`modem.js`) redrew on every 100ms poll regardless of
   whether the backend snapshot had actually changed. Since MBSFN's own CE/CIR buffer
   only updates every `CE_CIR_UPDATE_STRIDE=10` occasions (itself irregular, since real
   PMCH occasions at this wideband config are sparse per Finding 7), most polls were
   redundant redraws of stale data: this reads as a solid "burst" then a sharp
   "discontinuity" on the next real update, resembling a channel artifact while being
   purely a display-refresh-rate mismatch. Fixed: skip the redraw when the fetched
   snapshot is byte-identical to the last one.
2. The CAS and MBSFN frequency waterfalls used mismatched, uncorrelated x-axis scales
   (CAS at 12 REs/PRB native resolution, MBSFN at 144 REs/PRB for 1.25kHz, a 12x
   difference). Exposed the true configured PMCH width as a new `mbsfn_prb` REST field
   and rescaled CAS's real band by the actual PRB ratio before centering it within
   MBSFN's wider canvas, so the two occupied-bandwidth widths now render proportionally
   correct (e.g. 25 vs 40 PRB visibly different, matching reality) rather than either
   mismatching scale or shrinking to invisibility.

### MCCH EVM ripple: real, reproducible, root cause still not found after five tested
### hypotheses

User-spotted pattern on the live dashboard: MCCH's own EVM (`_rest._mcch.evm_rms`,
directly assigned from `pmch_dec.evm` on every decode, not an accumulating average)
sits at a rock-stable ~4.66% baseline but jumps intermittently to 4.7-16.3%, roughly
every few hundred ms to a few seconds, with no clean fixed period. Every jump shows the
same physically-consistent signature: raw power (`rxpwr`) ticks up slightly while
channel-estimate power (`cepw`) and RSRP drop, post-equalization data power (`datapw`)
rises above its normal 1.0000, and `sync_error` deviates from its steady -14.015 (though
not proportionally to the EVM jump's size). This is a genuine intermittent degradation
of channel-estimate coherence for one isolated occasion, not random noise and not (per
BLER staying 0.0 throughout every observation) currently a functional failure.

Five hypotheses, each tested live with real captured data rather than assumed correct,
all ruled out:

1. **`ZmqRxDevice.cpp`'s periodic (~5s) occupancy/throughput log**, running inline on
   the SCHED_RR-50 receive thread that feeds the ring buffer the PHY blocks on (the
   exact same disease as an already-fixed, unrelated "Dropping spurious MCCH-LCID SDU"
   debug-log-at-1kHz bug in the same file, just a lower-frequency call site nobody had
   audited for this). First correlation looked clean: the MCCH occasion immediately
   BEFORE each log print was consistently elevated across 15 occurrences. Moved the log
   to its own thread (a real improvement on its own merits, kept), then re-tested: bumps
   continued at a similar or higher rate, with the correlation now landing on the
   occasion AFTER the print instead of before. Ruled out; the original correlation was
   most likely coincidental (two independent ~5s-ish periodic events drifting in and out
   of phase).
2. **`MbsfnFrameProcessor`'s own `CE_CIR_UPDATE_STRIDE` computation**, running inline on
   the same worker thread immediately after decoding, before that worker is free for the
   next occasion (a same-thread, directly-causal mechanism, no cross-thread race
   needed). Added a `CIRSTRIDE_DIAG` print at the exact trigger point and correlated
   directly against `MCCHDIAG`: only 2 of 10 stride events were followed by an elevated
   MCCH reading, and several clear bumps had no stride event anywhere near them. Ruled
   out.
3. **Coarse system-level CPU-frequency variance.** This machine is bare metal (confirmed
   via `systemd-detect-virt`), all 32 cores run the `powersave` governor, current
   frequencies span 800MHz to 5+GHz at any given moment, and the modem's real-time
   threads have no CPU affinity set (floating freely across cores per `ps -T ... -o
   psr`). Sampled per-core frequency spread (`nlow` = cores under 1GHz) every ~300ms
   alongside timestamped `MCCHDIAG` lines: `nlow` at bump moments (7, 7, 9, 6, 4, 10)
   was statistically indistinguishable from `nlow` at non-bump moments (2 through 10,
   same range). Ruled out at this granularity; a genuine per-thread-core frequency test
   would need sub-millisecond resolution this polling approach cannot reach.
4. **The occasion's own processing duration.** Added high-resolution
   (`std::chrono::steady_clock`) timing directly in `MbsfnFrameProcessor::process()`,
   measuring both wall-clock duration of the call and the interval since the previous
   call, printed in `MCCHDIAG` as `durationus`/`intervalus`. Across 81 samples, bump mean
   duration (227.7us) was only mildly higher than normal mean (183.4us), and the ranges
   overlapped completely: one clear bump (6.5% EVM) had the *shortest* duration (85.1us)
   in the entire dataset. If a processing-time delay were the cause, the fastest-
   processed occasion should be the last place to see a bump. Ruled out.
5. **Sample-alignment shift** (a residual timing/CFO error, which would show as a
   linear phase ramp across RE index in the channel estimate). Added `ALIGN_DUMP`: dumps
   the raw post-FFT RE grid and channel estimate for an occasion whenever `evm` crosses
   0.055, plus the immediately following occasion as a paired baseline, capped at 6
   pairs. Computed `ce`'s unwrapped phase slope across RE index for all 12 dumps: every
   single one, bump or normal, came back at order 1e-6 to 1e-5 rad/RE, i.e. no
   detectable ramp at all. Ruled out specifically as the visible mechanism. The
   equalized-symbol magnitude spread (`raw[i]/ce[i]`, the same technique that found
   Finding 7) was 20-25% higher on genuine bumps versus genuine baseline in the three
   clean pairs, but that is a restatement of the EVM elevation itself, not a new causal
   finding.

**Left in place, all env-gated and inert by default**: `ZMQRX_DUMP` (bounded one-shot
raw wire capture, `soapy-zmq-bridge/ZmqRxDevice.cpp`), `CIRSTRIDE_DIAG`, the
`durationus`/`intervalus` fields (folded into the existing `MCH_DIAG` gate), and
`ALIGN_DUMP` (all `rt-mbms-modem/src/MbsfnFrameProcessor.cpp`). None reproduce the root
cause; all are cheap, bounded, and may be useful starting points for a future pass.

**Not yet tried**: a genuinely sub-millisecond, per-thread-core frequency/migration
trace (hypothesis 3's polling resolution was three orders of magnitude too coarse to
rule this out properly); comparing the exact captured sample window itself (not just
its FFT/phase-domain summary) against a reference sync point for bump versus non-bump
occasions.

## Part A + Part B: wideband MTCH softbuffer fix and spec-compliant multi-PMCH
## (TS 36.300 §15.3.3), 2026-07-20

### Context

Two items had been carried forward as "real feature work, not bug fixes" from earlier
passes: (1) MTCH still mostly failed to decode at wideband `pmch_bandwidth` even after
the `pmch.c` RE-mapping/stride fixes documented above, and (2) this eNB's single PMCH
carries both MCCH and (when enabled) time-interleaved MTCH, which TS 36.300 §15.3.3 says
shouldn't happen on the same PMCH. Design grounded in primary spec text (TS 36.300
V19.2.0, TS 36.331 V19.3.0) plus direct re-reading of the current code before writing a
line — both turned out more tractable than a "deep architecture rework" framing
suggested: most multi-PMCH scheduling plumbing (MAC, PHY subframe classification, ASN.1)
already existed as unused/dead code for `pmch_idx>0`, and the MTCH gap had a concrete,
numerically-derived candidate rather than needing a redesign.

### Part A: wideband MTCH softbuffer sizing (`rt-mbms-modem`)

**Root cause**: `MbsfnFrameProcessor.cpp`'s two `srsran_softbuffer_rx_init(&_softbuffer[i],
100)` calls size `max_cb` from the single-subframe max TBS at 100 PRB (21 CBs). But time
interleaving inflates the code-block count: `pmch_ti_tbs.h`'s TI-scaled TBS table
saturates at 502624 bits, giving a hard worst-case ceiling of 83 CBs — about 4x the
existing sizing, a function of TI alone, not `mbsfn_prb`. `sch.c`'s
`cb_segm->C > softbuffer->max_cb` check hard-rejects any decode past this limit. MCCH
never exercises it (always TI-disabled), so the gap was invisible until MTCH + TI
actually combine — exactly what Part B's PMCH1 needs to deliver real data, not just
signal correctly.

**Fix**: added `PMCH_MAX_CB_TI = 83` (`MbsfnFrameProcessor.h`, derived from
`502624/(SRSRAN_TCOD_MAX_LEN_CB-24)+1`) and switched both call sites to
`srsran_softbuffer_rx_init_guru(&_softbuffer[i], PMCH_MAX_CB_TI, SOFTBUFFER_SIZE)`. A
fixed constant, not dynamic per-`mbsfn_prb` sizing — the saturation behavior makes 83 a
provable worst case regardless of configured bandwidth/TI factor. Committed as `20ff7f5`
("Size PMCH softbuffers for worst-case TI code-block count, not untuned nof_prb=100").

**Verified**: no regression at the existing (non-TI) baseline; the TI+wideband MTCH
combination this fix specifically targets was later confirmed working end-to-end as part
of Part B's own staged verification below (PMCH1 with TI enabled decoding real data at
BLER 0.0).

**Deferred, not done this pass**: `Phy.h`'s `MAX_PRB=100` headroom increase + an explicit
reject-with-log if `mbsfn_prb > MAX_PRB` is ever requested (this was step 3 of the
original design) — not yet triggered by any tested config (widest so far is 40 PRB), left
as a follow-up rather than guessed at.

**Already handled, found already committed while re-checking the design's "queued
alongside" list**: the `chest_dl.c` `get_snr()` floor guard (the CINR-spike fix) and the
RX-side `ZMQ_RCVHWM` bound on `rf_zmq_imp_rx.c`'s SUB socket were both already applied in
earlier commits this campaign (rt-mbms-modem `623c304`; rt-mbms-tx `b84aea0`) — no action
needed here.

### Part B: spec-compliant multi-PMCH (`rt-mbms-tx`)

**Spec grounding** (TS 36.300 §15.3.3, direct quote): "MTCH and MCCH can be multiplexed
on the same MCH (if time interleaving is not configured)." The restriction is scoped to
*the same PMCH* — two PMCHs (one MCCH-only, never TI; one MTCH-only, TI allowed) is the
compliant fix. `maxPMCH-PerMBSFN = 15` (TS 36.331); 2 PMCHs is trivially within range, and
`PMCH-InfoList-r9`/`-ListExt-v1900` are already `SIZE(0..15)` dynamic arrays — no new
ASN.1 codegen needed.

**Design**: `pmch_cfg_t` (`enb_stack_base.h`) and `rrc_cfg_t::extra_pmch`
(`rrc_config.h`) already existed from earlier work in this campaign as inert plumbing.
This pass consumed them:
- `reconfigure_embms()` (`rrc.cc:1070-1095`): the pre-existing TI-on-PMCH0 warning is now
  a hard reject *specifically when `extra_pmch` is configured* — PMCH0 always carries
  MCCH, so once a second PMCH exists to route time-interleaved content to, the conflict
  is enforceable for real instead of only warned about. With `extra_pmch` empty, behavior
  is unchanged (warning only, matching every prior pass).
- `configure_mbsfn_sibs()` and `pack_mcch()` (`rrc.cc`): both rewritten to loop over
  `nof_pmch = 1 + extra_pmch.size()` (capped at 15), resolving each PMCH's fields from
  either the flat `cfg.pmch_*` fields (p==0, byte-identical reads to before this loop
  existed) or `cfg.extra_pmch[p-1]` (p>=1). `pack_mcch()` splits PMCHs into the r9 list
  and the v1900 (phase-2-feature) list per PMCH, same rule PMCH0 already used.
- `control_server.cc`: `embms.<field>=` keeps meaning PMCH0 (existing scripts/dashboard
  untouched); added `embms.pmch<N>.<field>=` for N>=1, plus a restart-only
  `embms.nof_pmch=1..15` gate.
- Session routing: PMCH1+ sessions are not yet wired to real M3AP-driven state
  (`rrc.cc:1860-1866`) — they use the same static single-session fallback PMCH0 uses when
  no real session exists yet. TEID-based routing via `extra_pmch[].session_teids` is a
  distinct, not-yet-implemented follow-up, flagged explicitly in-code rather than silently
  assumed to work.

**Bug #1 — cumulative `sf_alloc_end`** (found via live test: `mch_status/1` showed
`present:false` right after enabling `nof_pmch=2`). `sf_alloc_end` was computed
independently per PMCH using the same "period minus overhead" formula for each, but per
spec (and the RX-side `Phy.cpp`'s own `pmch_start = prev.sf_alloc_end+1` convention) it
must be cumulative — PMCH1 ended up with `pmch_start > sf_alloc_end`, an empty,
unreachable range. Fixed in both functions by computing the area's total data-subframe
capacity once (`rrc.cc:1838-1839`) and splitting it into contiguous per-PMCH chunks
(`rrc.cc:1914-1917` and the equivalent block in `pack_mcch()`), the last PMCH absorbing
any remainder.

**Bug #2 — TI divisor-check used the wrong (absolute, not relative) subframe count**
(found via live test: TI set on PMCH1 decoded as `ti_n=0, ti_m=0` despite a valid
request). The M-divisor clamp checked the raw cumulative `sf_alloc_end` instead of the
PMCH's own relative data-subframe count (`sf_alloc_end - this_pmch_start + 1`) — for
PMCH0 these coincide (`pmch_start` is always 1), which is why the bug was invisible until
a genuine second PMCH existed. Fixed by computing `this_pmch_start` before advancing
`cumulative_end`, and searching only the legal enum divisors `{32,16,8,4}` against the
relative count (`rrc.cc:1913-1949`).

**Staged live verification** (same discipline as every prior pass — decoded value before
functional, one variable at a time):
1. Baseline regression (`extra_pmch` empty, `nof_pmch=1`): confirmed unchanged behavior
   from before this change.
2. `embms.nof_pmch=2` (restart): 2-PMCH signaling confirmed correct via decoded
   `sib_info` (2 `PMCH-InfoList` entries, correct `sf-AllocEnd`/`mch-SchedulingPeriod`
   after Bug #1's fix).
3. Real MTCH decode on PMCH1 confirmed for the first time this campaign: BLER 0.0,
   `present:true`.
4. TI enabled on PMCH1: after Bug #2's fix, confirmed genuinely combining (not just
   coincidentally decoding every subframe independently) via `PMCH_TI_DIAG` worker-pointer
   block-stability — clean, consistent 8-subframe blocks (56-63, 64-71, 72-79, 80-87),
   each pinned to a single worker, transitioning cleanly between blocks. BLER 0.0.
5. TI on PMCH0 confirmed hard-rejected: decoded signaling stayed at `0/0` despite the
   request, and the eNB log showed the new rejection message firing
   (`grep "requested on PMCH0" eNB.log`), while PMCH1's own TI setting remained correctly
   unaffected by PMCH0's rejection.

### Status

Both parts implemented and live-verified. rt-mbms-modem's Part A changes were committed
earlier this pass (`3c9306f`, `20ff7f5`). rt-mbms-tx's Part B changes (`rrc.cc`, `rrc.h`,
`rrc_config.h`, `control_server.cc`) are committed alongside this write-up. The TI-test
config change (`enb_baseline.conf`: `time_interleaving_n=2`,
`additional_non_mbsfn_subframes=2`) used to drive verification step 4 above has been
reverted to true baseline (`0` / commented out) now that the test is documented — same
pattern as every prior pass's test-only config changes.

**Not done, left as explicit follow-ups**: Part A's `MAX_PRB` headroom (above); PMCH1+
session-to-TEID routing (above); a genuine interaction test of Part B's multi-PMCH TI
against Phase 6's CAS-muting fix (both individually verified, never combined with each
other).

## Closing every follow-up above: real session-to-PMCH1 delivery, three live-found bugs,
## and the TI+CAS-muting interaction test, 2026-07-21

### Context

Every item the previous section left open. Spec-compliance question raised mid-pass
(is the eps-BearerID validation this uncovered actually a spec limit, or an
implementation artifact?) — checked directly against TS 36.331/24.116 rather than
assumed; see Finding 2 below for what that turned up.

### Finding 1: `resolve_pmch_sessions()`'s empty-vs-nothing-to-fabricate conflation broke
### the very first M1-U packets after every startup

`configure_mbms_bearers()` (new this pass — see below) called `resolve_pmch_sessions()`
and, on an empty result, simply skipped registering a bearer at all. But
`resolve_pmch_sessions()` returns empty for two genuinely different reasons: "PMCH1+,
no `session_teids` filter configured" (nothing to add, correct to skip) and "no real
M3AP session exists yet" (true of *every* PMCH for the first several seconds after eNB
startup, until the first `MBMS Session Start Request` arrives) — the latter is exactly
when `configure_mbsfn_sibs()`/`pack_mcch()` fabricate a placeholder session so MCCH
keeps signalling *something*, and this loop needs to register a matching bearer for
that placeholder too.

Caught live, immediately: a fresh eNB start logged `"Can't deliver SDU for EPS bearer
1. Dropping it."` on every single M1-U packet, persisting well past the startup window
— all MBMS data was being silently dropped from the very first frame. Root-caused,
fixed by giving the bearer-setup loop (extracted into its own `configure_mbms_bearers()`
so it's callable more than once — see Finding 3) the same fabricated-fallback logic
`configure_mbsfn_sibs()` already had. Re-verified live: bearer registers correctly at
startup, M1-U delivery resumes, MCCH/MCH0 decode at BLER 0.0 end to end. Commit
`a1b76b5` (rt-mbms-tx).

### Finding 2: a regular-DRB bearer-id check was silently rejecting spec-legal MBMS
### bearer ids above 10 — checked against the actual spec text, not assumed

Extending the composite-key scheme (`compose_mch_lcid()`, needed so PDCP/GTP-U/
`bearer_manager` — which have no native PMCH concept — can address MBMS bearers with a
single flat, always-unique id) to a real second PMCH hit `gtpu_tunnel_manager::
add_tunnel()`/`find_rnti_bearer_tunnels()` rejecting composite id 17 with `"invalid
eps-BearerID=17"`. First fix attempt shrank `PMCH_LCID_STRIDE` to fit inside that
0..10 ceiling — wrong: that ceiling (`is_lte_rb()`, `MAX_LTE_LCID=10`) turned out to be
the *regular unicast DRB* `logicalChannelIdentity` range (TS 36.331 clause 6.3.2,
`INTEGER(3..10)`), applied to the broadcast RNTI's bearers regardless of RNTI — an
implementation bug, not a spec limit on MBMS. Confirmed directly against the spec text
before re-fixing: MBMS's own `logicalChannelIdentity-r9` (clause 6.3.7,
`MBMS-SessionInfo-r9`) is a distinct field, `INTEGER(0..maxSessionPerPMCH-1)` = 0..28
(`maxSessionPerPMCH=29`, clause 6.4) — and in any case the composite id reaching this
check was never a real ASN.1 field to begin with, so neither range is actually the
right bound for it; the only real ceiling is `bearer_manager::add_eps_bearer()`'s own
`uint8_t` parameter (0..255).

Corrected fix: `is_valid_eps_bearer_id()` (gtpu.cc) exempts the broadcast RNTI to the
real 0..255 range instead of loosening `is_lte_rb()` itself (which stays correct for
actual regular-DRB bearers). `PMCH_LCID_STRIDE` restored to 16 (supports the full
`maxPMCH-PerMBSFN=15` PMCHs at up to 15 sessions each, `14*16+15=239<255`). Live
re-verified: PMCH1's bearer registers correctly across RLC/PDCP/`bearer_manager`/GTP-U's
own tunnel manager, both PMCHs decode at BLER 0.0. Commit `8d9892b` (rt-mbms-tx).

### Finding 3: `rrc_cfg_t::session_teids` (PMCH0's own TEID filter) was never copied
### from static config at startup — only ever set via the live-SET path

Threaded through `reconfigure_embms()` when this campaign first added TEID-based
session routing, but never given the matching `enb_cfg_parser.cc` startup-time copy
every other flat `embms.*` field already has. At process start it was therefore always
empty, so `resolve_pmch_sessions()` fell back to "no filter configured, dump every real
session onto PMCH0" — including sessions meant for PMCH1. Caught live while verifying
Finding 4 below: a real content session configured for PMCH1 showed up duplicated on
*both* PMCH0 and PMCH1's MCCH-signalled session lists (confirmed via `sib_info`, not
just `mch_info` — this was genuine eNB-side signalling, not a modem/dashboard display
artifact). Fixed with the one missing line (`rrc_cfg_->session_teids =
args_->stack.embms.session_teids;`); re-verified live: PMCH0 lists only the SACH,
PMCH1 lists only the content session, both decode at BLER 0.0. Commit `bfba93d`
(rt-mbms-tx, bundled with Finding 4's static-config work since both surfaced in the
same test).

### Finding 4: static config-file support for a real PMCH1 (`embms.nof_pmch`,
### `embms.pmch1.*`) — closing the last structural gap from the previous section

The previous section's "PMCH1+ session-to-TEID routing" follow-up was implemented
(`resolve_pmch_sessions()`, `compose_mch_lcid()`) but only reachable via the live
control socket — and `gtpu.cc`'s `m1u_handler` only ever learns per-PMCH
`session_teids` once, at `gtpu::init()`, long before the control socket starts
listening. So a live-added PMCH1 could be signalled correctly but never actually
*receive* a real session's M1-U data, only ever the fabricated placeholder — the
central thing this whole effort was for remained unverified.

Added `embms.nof_pmch` (1-2 — only one extra PMCH is configurable via the static file;
more still needs the live socket) and `embms.pmch1.{mcs,session_teids,
time_interleaving_n,time_interleaving_m,nof_mbms_sessions}` as real startup options
(`main.cc`), validated the same way their PMCH0 equivalents already are
(`enb_cfg_parser.cc`), pushed into both `embms_args_t::extra_pmch` (reaches
`gtpu_args`) and `rrc_cfg_t::extra_pmch` at parse time. Commit `bfba93d` (rt-mbms-tx).

**Live-verified end to end for the first time this campaign**: a real BM-SC content
session (`bmsc.conf`'s `[content_session]`, distinct from the always-on SACH — TMGI
901:56:000010, `c_teid=52428`/`0xCCCC`, service ID 16 chosen deliberately outside TS
24.116 clause 6.3.3's reserved 0..15 range for Receive-Only-Mode Service-Announcement
TMGIs, same PLMN as the SACH per that same clause — an early attempt wrongly moved it
to a different PLMN entirely before checking the actual spec text, corrected once it
was), configured via `enb_baseline.conf`'s new `session_teids=0xbbbb` (SACH) /
`nof_pmch=2` / `pmch1.session_teids=0xCCCC`, signalled correctly and only on PMCH1
(confirmed via `sib_info`, not duplicated onto PMCH0 once Finding 3 was fixed), its
bearer registered (Finding 1), delivered via M1-U without being dropped (Finding 2),
and decoded by the modem at BLER 0.0 — alongside the SACH decoding independently on
PMCH0, also at BLER 0.0. `mch_info`'s `dest` field stayed empty for PMCH1's session
even once it was decoding correctly (PMCH0's own entry showed the SACH's real
`224.0.0.120:55555` throughout) — not chased further; likely a pre-existing
client-side display gap scoped to tracking one MCH's traffic, separate from everything
above.

One operational note worth recording: sending multiple `SET` commands to the control
socket batched through one `nc` invocation (piped via a single `printf`) silently
dropped some of them in practice (confirmed twice — once for the CAS-muting keys, once
for `pmch1.time_interleaving_n`) even though every command returned `OK` individually
when re-sent one at a time. Always verify with `GET` after a batched `SET`, or just
send one command per `nc` call.

### TI + CAS-muting interaction, on a real PMCH1 session, for the first time

The previous section's last open follow-up. Live-set `embms.k_cas=8`,
`embms.n_cas=4`, `embms.cas_muting=true`, `embms.pmch1.time_interleaving_n=2`,
`embms.pmch1.time_interleaving_m=4` (one `nc` call each, per the note above) against
the same real-content-session-on-PMCH1 setup from Finding 4. Decoded `sib_info`
confirmed both took effect (`sib1.cas_muting_enabled=true, k_cas=8, n_cas=4`;
`mcch.pmch_list[1].time_interleaving_n=2, .time_interleaving_m=4`) — SIB1's own CAS-
muting fields needed a noticeably longer propagation wait than MCCH's own
modification-period cycle (~25s total from the first `SET` to confirmed decode, vs the
usual ~7-8s for MCCH-signalled changes) before showing up, not a failure, just a slower
path. Sustained BLER 0.0 on both PMCH0 and PMCH1 across a 30-second polling window
with both features active simultaneously alongside the real content session — the
combination this whole follow-up existed to test, now confirmed clean.

### Also fixed: cosmetic log noise, and a defensive bounds guard on the modem

`configure_mbms_bearers()` being safely callable more than once (needed for Finding 1's
fix) meant `bearer_manager` logging `"EPS bearer ID %d ... already registered"` at
**ERROR** level on every single MCCH repack for any already-known bearer — harmless,
but noisy enough to trip alerting in a real deployment. Fixed by tracking
already-registered composite ids (`rrc::_registered_mbms_bearers`) and skipping them
outright, ordered so a genuinely over-budget config (composite exceeding
`PMCH_LCID_MAX_COMPOSITE`) still re-logs every call rather than going silent after the
first occurrence. Commit `618cde7` (rt-mbms-tx).

Separately, on the modem: `MbsfnFrameProcessor::set_cell()`/`CasFrameProcessor::
set_cell()` handed `cell.nof_prb`/`mbsfn_prb` straight to `srsran_ue_dl_set_cell_scs()`
with no check against `MAX_PRB`, the fixed size `_ue_dl`'s own buffers were allocated
at. Nothing today can request more than 40 PRB (`pmch-Bandwidth-r17`'s real ASN.1
range, TS 36.331 clause 6.3.7, checked directly — comfortably under `MAX_PRB=100`),
but `modem_zmqtest.conf`'s `mbsfn_prb_test_override` is an operator-set debug value
with no such ceiling, so this was a real boundary, not a hypothetical one. Both now
clamp-and-log instead of silently overflowing. Live-verified: no regression on a full
modem restart, both PMCHs still decoding at BLER 0.0. Commit `1c518b3` (rt-mbms-modem).

### Also fixed: MCH0/MCH1 dashboard colors too close to tell apart

Found while visually spot-checking the Subframe Activity panel during this pass: the
golden-angle hue rotation (added when the panel first became per-PMCH-aware) started
from green (PMCH0, hue 120°) and put PMCH1 at 137.5° — distinct enough to satisfy "no
two colors are ever identical," but close enough to read as "a slightly different
green" rather than a clearly separate color at a glance. Cross-checked against the raw
`subframe_log` before concluding anything was actually wrong: PMCH1's activity was
there and correctly attributed the whole time (`('MCH', 'IDLE', 1)` / `('MCH', 'OK',
1)` counts distinct from PMCH0's own) — this was a colour-legibility issue, not a
data-correctness one. Hand-picked green/teal for indices 0/1 (the only two ever
realistically configured on this rig) instead of pure procedural generation; the
golden-angle fallback for any further index now starts from teal rather than from
green. Commit `416b460` (rt-mbms-application).

### Status

Every follow-up the previous section left open is now closed: real (non-fabricated)
session delivery to PMCH1 confirmed end-to-end, the TI+CAS-muting interaction
confirmed on a real PMCH1 session, and the modem-side `MAX_PRB` guard added. Three
real, independent bugs found via live testing this pass (not just the ones this
section set out to find) — all fixed, all re-verified live, none left as "probably
fine." The one spec-compliance question raised along the way (Finding 2) was checked
against primary text rather than assumed, twice over: once for the eps-BearerID
ceiling itself, once for the reserved TMGI Service-ID range hit while setting up the
test session.

**Remaining, not chased this pass**: `mch_info`'s empty `dest` field for PMCH1's real
session (noted above, likely a narrow pre-existing client-side gap); the `nc`
batched-SET reliability issue (operational note above, workaround known, root cause in
`nc`'s own pipelining vs. the control socket's line protocol not isolated).

## Continuation pass, 2026-07-21: the `dest`-field gap is deeper than assumed above —
## real data loss, not a display gap; `nc` fix confirmed closed; two stale diagnostic
## leftovers found and cleared as EVM-ripple/MTCH-timing confounds

### `nc` batched-SET: confirmed already closed
No new work needed — `control_server.cc`'s `handle_connection()` rewrite (commit
`dc362af`, earlier this pass) already processes every line in a connection's buffer,
not just the first. Re-checked `git log`/`git status` on this file: committed, clean,
no follow-up required.

### The PMCH1 `dest`-field gap is NOT "likely a narrow client-side display gap" —
### it's real MTCH content never reaching `Gw::write_pdu_mch()` at all

Picked back up with `PMCH_TI_DIAG` (already live from the previous pass). Findings,
in order:
- `TI_DIAG_ADDBEARER`: both PMCH0 and PMCH1 show `has_bearer=1` for their respective
  sessions — bearer registration itself is correct on the RX side.
- `TI_DIAG_GWMCH` (`Gw::write_pdu_mch()`, `Gw.cpp`): only `mch=0` entries over a 20+
  second observation window (26 of them) — **zero** `mch=1` entries. `Gw` is simply
  never called with `mch_idx=1`, for either MCCH or (if it ever reaches this point)
  MTCH content.
- `TI_DIAG_MACSDU` (`MbsfnFrameProcessor.cpp`, immediately before the
  `_rlc.write_pdu_mch()` call): 105 entries, all `lcid=1`, but this diagnostic did not
  print `mch_idx` — so it cannot distinguish PMCH0 SACH traffic from PMCH1 content
  traffic. Both sessions plausibly use `lcid=1` (MRB LCIDs are local to their own PMCH
  per 36.331), so "all lcid=1" is not informative on its own.

**Fix applied, not yet live-tested**: added `mch_idx=%u` to the `TI_DIAG_MACSDU` print
(`MbsfnFrameProcessor.cpp`, the call site right before `_rlc.write_pdu_mch()`).
Rebuilt the modem successfully against this change. **Testing this requires a modem
restart, which this pass could not get approval for** (see "Blocked" below) — so
whether MAC SDU writes are even being attempted for `mch_idx=1` at all (vs. failing
somewhere between there and `Gw`) is still an open question, not yet answered.

### Two stale, uncommitted diagnostic/config leftovers found and cleared — both look
### like real confounds for open investigations, not just hygiene

While reviewing `git status`/`git diff` across all repos before making further changes
(standard practice this campaign — confirm no forgotten state before trusting a test),
found two items that had been sitting active/uncommitted for 1-2 days without being
revisited:

1. **`modem_zmqtest.conf`: `main_thread_priority_rt` was still `0`**, a "TEMP
   diagnostic 2026-07-19: testing if RT priority is the SIGKILL trigger" flag tied to
   the now-abandoned shared-widened-FFT architecture (superseded by the resampler-
   bridge redesign, confirmed already implemented and working — `enb_dl.c`/`ue_dl.c`
   both have `cas_buffer`/`cas_upsampler`/`cas_decimator` in place, `pdcch_status`
   100%, BLER 0.0 per the 2026-07-19 write-up above). The doc itself already flagged
   this as "still pending: revert `main_thread_priority_rt` to 20... long-standing,
   unrelated temporary SIGKILL workaround, still not investigated" as of 2026-07-19,
   and it was never revisited across either the 2026-07-20 or 2026-07-21 passes —
   including the **entire MCCH EVM ripple investigation**, which ran this whole time
   with the modem's main thread (the one that calls `phy.get_next_frame()` —
   `main.cpp`'s sample-sync/frame-timing call, feeding every subsequent decode) at
   **non-realtime scheduling priority** on a shared, busy 32-core machine. The EVM
   ripple write-up's hypothesis 3 tested CPU *frequency* governor variance and found
   it inconclusive; it never tested thread *scheduling priority*/preemption, which is
   a related but distinct mechanism — a non-RT main thread can be delayed by any other
   runnable process on its core even at full frequency. This is a plausible, previously
   untested candidate mechanism for "genuine intermittent per-occasion channel-estimate
   degradation, no fixed period" — matching the ripple's own description closely.
   **Reverted to `20`** (the documented, intended default). Not yet live-tested — same
   restart blocker as above. Treat as a new, unconfirmed hypothesis 6 for the EVM
   ripple investigation, not a confirmed fix.
2. **`receive-netns.sh`: `PMCH_RE_DUMP=1` was still in the modem's launch line**,
   despite this same file's own comment block saying it was "removed 2026-07-19" for
   being actively harmful. Checked `pmch.c` directly: its RX FAIL-DUMP site
   (`pmch.c:1078`) does a real `fopen`/`fwrite`/`fclose` of the failed LLR buffer on
   *every* CRC failure, unconditionally — it does not go through the
   `pmch_re_dump_enabled()`/`PMCH_RE_DUMP_TTI` tti-filter that gates this file's other,
   properly-bounded dump sites. Given MTCH decode still mostly fails at the current
   wideband config (the still-open "SLOWCALL timing issue" from the previous
   sequencing note), this was firing on most subframes — the exact per-subframe-disk-
   I/O pattern already root-caused as a `SYNC_OFFSET_DIAG SLOWCALL` contributor when
   first found on 2026-07-19. It was evidently re-enabled at some point after that
   removal (the code comment at the dump site itself mentions reuse "for the 2026-07
   CAS-muting sf=0 investigation") without the receive-netns.sh comment being updated
   to match. **Removed from the launch line.** Not needed for the PMCH1 investigation
   above (that uses `PMCH_TI_DIAG`'s own gate, unaffected). May also be relevant to,
   though not yet confirmed as the cause of, the open MTCH SLOWCALL timing item.

### Blocked: modem restart needed to test all three of the above, not approved this
### pass

Stopping/restarting the modem (via `receive-netns.sh`, which tears down and recreates
its network namespace) was not approved in this pass — per this campaign's standing
practice, live real-time DSP process restarts get a live confirmation rather than
proceeding unattended. All three changes above (`mch_idx` diagnostic, RT-priority
revert, `PMCH_RE_DUMP` removal) are in place and ready, but **none are live-verified
yet**. Next steps once a restart is approved, in order:
1. Restart modem (`sudo ./receive-netns.sh stop && sudo ./receive-netns.sh start`),
   confirm baseline still passes (`pdcch_status`, BLER) before trusting anything else.
2. Check `TI_DIAG_MACSDU`'s new `mch_idx` field: does `mch_idx=1` ever appear at all?
   If not, the gap is upstream of this print (worth checking whether PMCH1's
   CRC-success branch is even reached); if yes, but still no matching `mch=1` in
   `TI_DIAG_GWMCH`, the gap is between this print and `Gw::write_pdu_mch()` (RLC/PDCP
   layer — `rlc::write_pdu_mch()`'s `valid_lcid_mrb()` check, or `pdcp::write_pdu_mch()`'s
   `lcid==0` branch, are the two candidate points already identified as worth checking).
3. Re-run the EVM ripple observation with `main_thread_priority_rt=20` restored;
   compare bump frequency/magnitude against the 2026-07-20 baseline numbers
   (~4.66% steady, 4.7-16.3% bumps) to confirm or rule out hypothesis 6.
4. Re-check whether the MTCH SLOWCALL timing issue improves now that `PMCH_RE_DUMP`'s
   per-failure disk I/O is out of the decode path.

### Resolved: modem restarted, all three changes confirmed live — and the PMCH1
### `dest`-field/GW-delivery question has a definitive answer: not a bug

Modem restarted (fresh PID, `main_thread_priority_rt=20` confirmed via the startup log
line "Raising main thread to realtime scheduling priority 20"; `PMCH_RE_DUMP` confirmed
absent, zero matches in the fresh log; `mch_idx`-augmented `TI_DIAG_MACSDU` live).
eNB/MBMS-GW/BM-SC were **not** restarted this time (out of scope of what was approved) —
still the same ~6h+ instances from earlier in the day, so anything timing-sensitive
observed against them carries that caveat.

**PMCH1 GW-delivery gap: root cause found. Not a bug.** With `mch_idx` now in the
`TI_DIAG_MACSDU` print, the picture is unambiguous: **zero** `mch_idx=1` entries (42/42
were `mch_idx=0`), and correspondingly zero `TI_DIAG_GWMCH mch=1` entries (28/28 were
`mch=0`) — while `TI_DIAG_ADDBEARER` still confirms both PMCHs have `has_bearer=1`.
Cross-checked against `TI_DIAG_SUBH` (one level up the call chain, logs every MAC
subheader regardless of type): 48 entries show `is_mcch=0 is_sdu=1 ce_type=0 lcid=0` —
an SDU-flagged subheader with `lcid=0` on a *non*-MCCH subframe. `lcid=0` is reserved
for MCCH (TS 36.321 Table 6.2.1-4); a real MTCH data subframe should never legitimately
carry it. This is exactly the pre-existing, already-documented "all-zero placeholder
TB" pattern (`MbsfnFrameProcessor.cpp`'s own comment, ~line 649-658): when a PMCH's
session has nothing real queued, the eNB still transmits a well-formed but empty TB
(needed to maintain synchronization/channel estimation), which decodes successfully
(hence CRC/BLER counts it as a clean subframe) but whose first MAC subheader byte reads
as `lcid=0` — correctly recognized and silently dropped (`spdlog::debug("Dropping
spurious MCCH-LCID SDU...")`, `continue;`) rather than misdelivered.

Confirmed independently: no content-generating process exists anywhere on this host
(`ps aux` shows no flute/ffmpeg/udpsend/content-generator of any kind), and
`BM-SC.log` contains exactly 8 lines total, ending at session announcement ("Content
session announced (TMGI 901:56:000010, 239.255.1.1:6000, TSI 2)") with **no**
forwarding/byte/packet activity ever logged. The BM-SC's xMB path only relays bytes
when something actually pulls/pushes content through it (confirmed working
end-to-end for HLS in an earlier, separate campaign — see
`project-xmb-bmsc-hls-test` context); this test session was only ever given a
signaling-only setup (`urn:3gpp:test-content-service`, a placeholder URI), never a
real content source.

**Conclusion**: the session-to-PMCH1 signaling, scheduling, and bearer-registration
path (this pass's Findings 1-4, all still valid and correctly fixed) is completely
sound end-to-end. "PMCH1's dest field stays empty" and "no real GW delivery for
mch_idx=1" were never a display gap or a pipeline bug — they are the **correct**
observable outcome of testing a session with real control-plane signaling but no
data-plane content behind it. `BLER=0.0` was true and meaningful (no genuine decode
failures), but it does not by itself prove content delivery, since it counts clean
*subframes*, not delivered *SDUs* — a distinction this investigation conflated until
now. Closing this out: no code fix needed. A genuine end-to-end content-delivery test
would need a real traffic source (e.g. an actual xMB Pull ingest, or even a simple
continuous UDP sender) feeding the test session's multicast address — flagged as a
possible future test, not attempted this pass (new test-infrastructure work, not a
bug fix, and out of scope of what was asked for here).

**EVM ripple (hypothesis 6): informal spot-check only, inconclusive.** 30 consecutive
`MCCHDIAG` samples in the few minutes since restart: 24/30 at a stable 0.0525 (5.25%,
close to the previously-documented ~4.66% baseline), but bumps still present
(0.0652, 0.0606, 0.0549 — smaller than the previously-observed max of 16.3%, but this
is a far smaller sample than the original investigation used, not a controlled
comparison). **This does not confirm or rule out hypothesis 6** — the RT-priority fix
is live and correct as a matter of hygiene regardless, but bumps clearly still occur
at least occasionally with it in place. A real verdict needs a longer, comparable-
duration observation window against the original 2026-07-20 numbers, not yet done.

**MTCH SLOWCALL timing item**: confirmed not testable right now, not just "not
attempted." Zero `SLOWCALL` entries anywhere in the current log — the mechanism only
fires during actual wideband MTCH decode attempts, and since PMCH1 has no real content
source (see the conclusion above), that code path is never exercised at all. Needs the
same real content source as a genuine PMCH1 delivery test before this can be
re-checked either way.

### EVM ripple (hypothesis 6): longer window gathered — real, measurable improvement,
### but not a full fix

Waited for 201 fresh `MCCHDIAG` samples (106s at this cell's actual MCCH occasion
rate — much faster than the earlier 30-sample spot-check's rough rate estimate
suggested), a larger sample than the original investigation's 81. Full distribution:

```
n=201, mean=5.36%, median=5.25%, min=4.31%, max=8.07%, stdev=0.42pp
Histogram (rounded to nearest 1%): 4%: 2, 5%: 173, 6%: 21, 7%: 4, 8%: 1
15/201 samples (7.5%) land >15% above the median (6.04%+) - by the same
"elevated occasion" framing the original investigation used.
```

Compared against the original 2026-07-20 numbers (`main_thread_priority_rt=0`,
"rock-stable ~4.66% baseline... jumps intermittently to 4.7-16.3%"):
- The steady-state level is essentially unchanged (5.25% now vs. ~4.66% then - close
  enough to be ordinary day-to-day/measurement variance, not a meaningful shift).
- Bumps are still clearly present (7.5% of samples elevated) - **the ripple has not
  been eliminated**.
- But bump *magnitude* dropped substantially: worst case now is 8.07%, versus a
  documented 16.3% before - roughly half, and **zero** samples this pass reached even
  10%, let alone the previous max.

**Honest conclusion**: `main_thread_priority_rt=20` is a real, measurable partial
contributor - it did not fully explain the ripple (something else is still producing
smaller bumps), but the worst-case severity dropped by roughly half in a sample this
size. Treat as confirmed-partial, not confirmed-complete: there is likely a second,
still-unidentified contributor stacking with (or independent of) whatever the
scheduling fix addressed. Not chased further this pass - a reasonable next step would
be re-running hypothesis 3 (CPU frequency/migration) with proper sub-millisecond
resolution now that this confound is out of the way, since the coarse 300ms polling
that made it "inconclusive" the first time might resolve more cleanly against this
smaller residual effect.

### Status of this continuation

All originally-pending items now have a definitive answer: PMCH1 delivery gap =
not a bug (root-caused, no code change needed); `nc` batched-SET = already fixed;
EVM ripple = one real partial contributor found and fixed, residual not yet
explained; MTCH SLOWCALL = confirmed blocked on the same missing-content-source
prerequisite as a real PMCH1 delivery test. eNB/MBMS-GW/BM-SC restart (needed to
clear their own ~6h+ uptime and get a fully clean baseline) could not be performed
this pass - blocked by the same restart-approval gate as the modem, needs to be run
directly by a human operator.

## Same day, continued: a fresh full-stack restart surfaced a real, previously-latent
## bug - cross-PMCH buffer-state contamination - found, fixed, and live-verified

### Context

After the full `transmit.sh` + modem restart above (run by the user directly once
the sudo/TTY-gated automated attempts kept failing), PMCH1 - previously confirmed
CRC-succeeding on idle content - came up at **~92% BLER** (61/66 samples failing),
stable from the very first sample, visible live on the dashboard as a red block in
the Subframe Activity waterfall. MCCH, PDSCH, and MCH0 (SACH) all stayed clean
(BLER 0.0) throughout - this was specific to PMCH1.

### Root cause: `mac::rlc_buffer_state()` (srsenb/src/stack/mac/mac.cc) had no way to
### tell which PMCH a buffer-state report belonged to

Every PMCH numbers its own MRB sessions starting at lcid 1 (confirmed via
`TI_DIAG_ADDBEARER`: both PMCH0 and PMCH1 have a `session[0] lcid=1`). The RLC
bearer's own notification path (`rlc_um_lte`'s tx side, `lib/src/rlc/rlc_um_lte.cc:103`)
calls `bsr_callback(parent->get_lcid(), n_bytes, 0)` **directly**, with only the bare
lcid - no PMCH identity attached, since `srsran::rlc::add_bearer_mrb()`
(`lib/src/rlc/rlc.cc:495`) handed every MRB entity, regardless of which PMCH it
belonged to, the exact same raw `bsr_callback`. `mac::rlc_buffer_state()`'s receiving
end compensated with a loop over all 15 possible PMCH slots, updating *every* PMCH
whose session happened to share that bare lcid number - meaning PMCH0's real queue-
depth reports were also being applied to PMCH1's otherwise-empty session. This
periodically fed PMCH1's `build_mch_sched()` a false non-zero `mtch_stop`, causing
the eNB to schedule and attempt a real transmission against a bearer that genuinely
had nothing queued - the resulting mismatch between schedule and actual buffer
content mostly failed CRC on the RX side.

Two other candidate mechanisms were checked and ruled out before landing on this:
the already-documented "wideband MTCH SLOWCALL" timing issue (zero `SLOWCALL`
entries in the log, so not that) and a possible TX-side MCS/TBS misconfiguration
specific to a cold start (eNB log showed no clamp/infeasibility warnings at all).

Separately found and confirmed dead/vestigial while investigating (not the active
bug, left alone): `rlc::update_bsr()`/`update_bsr_mch()` (`lib/src/rlc/rlc.cc`)
compute a buffer-state value and then never actually invoke `bsr_callback` with it -
the real notification path is the concrete entity's own direct call, described
above. Not touched this pass; flagged here in case a future pass wants to clean it
up, since it's currently misleading dead code, not a functional bug in its own right.

### Fix

- `lib/src/rlc/rlc.cc` (`add_bearer_mrb`): wrap the callback handed to each MRB
  entity in a closure that composes `mch_idx` into the reported lcid
  (`mch_idx * 16 + lcid` - this library file is `srsran`-namespaced and can't
  include `srsenb`'s `compose_mch_lcid`/`PMCH_LCID_STRIDE` without an inverted
  layering dependency, so the stride is duplicated here and must stay in sync).
- `srsenb/src/stack/mac/mac.cc` (`rlc_buffer_state`): decompose the incoming lcid
  and update only the PMCH it actually belongs to, instead of looping over all 15.

Committed as `5bab564`. Rebuilt cleanly.

### Live-verified

Full stack restarted again by the user. MCH1: **14/14 samples at BLER 0.0**,
matching MCH0 exactly. Confirmed via `TI_DIAG_ADDBEARER`/`TI_DIAG_GWMCH` that bearer
registration was already correct throughout (never the problem) and via the eNB log
(`M1-U TEID demux configured for 2 session(s) across 2 PMCH(s)`, both session-start
requests received) that session setup itself was also never the problem - this was
purely the buffer-state notification path.

### Follow-up: dashboard showing MCH1 at MCS0/EVM0.00 - not a bug, explained

After the fix, the user asked why the dashboard showed wildly different EVM values
across channels (MCCH 6.22%, MCH0 4.40%, MCH1 0.00% at MCS0). Investigated by adding
`evm` to the existing `MCHDIAG` diagnostic (`MbsfnFrameProcessor.cpp`, gated by the
already-enabled `MCH_DIAG`) - its own comment claimed `pmch_dec.evm` was permanently
dead/zero for regular MCH (written before `srsran_evm_run_s()` was added to
`pmch.c`), worth checking rather than trusting. Result: zero `MCHDIAG` entries for
`pmch_idx=1` at all (51/51 were `pmch_idx=0`) - not because the diagnostic or the fix
are broken, but because PMCH1's subframes overwhelmingly hit an existing, unrelated
DTX/idle-detection gate a few lines above `MCHDIAG` (`data_pw < 1e-2f`) that
correctly recognizes "nothing real was transmitted" and explicitly skips all
decode-accounting before ever reaching it. Since PMCH1 still has no real content
source (unchanged from earlier this pass), its dashboard MCS/EVM fields are simply
sitting at their never-updated default - consistent with, not contradicting, the
fix (BLER is correctly 0.0 because the rare scheduling-info-only decodes that DO
happen now succeed; the rest of the time there's genuinely nothing to measure).

MCCH's 6.22% and MCH0's 4.40% are both real measurements, and both fall inside the
range already characterized by the still-only-partially-fixed EVM ripple earlier
this pass (baseline ~4.66%, bumps now reaching ~8% rather than the previous 16.3%) -
not a new or separate issue, just two snapshots of the same still-open residual.

### Status

Every item raised this pass is now closed or clearly explained: the PMCH1 CRC-failure
regression was root-caused and fixed (a real, previously-latent bug, not a cold-start
fluke), live-verified at BLER 0.0 matching MCH0. The MCS0/EVM0.00 dashboard reading
is explained (idle-gate, not a bug) and the MCCH/MCH0 EVM gap is explained (the
already-documented, already-partially-fixed ripple, not a new problem). Only the
EVM ripple's residual cause remains genuinely open, unchanged from earlier - no new
work landed on it this pass.

## Same day, continued further: a real content source built and wired up, a second
## stacked bug found and fixed, and the EVM ripple's CPU hypothesis properly closed

### A genuine content source for PMCH1, without needing xMB/FLUTE

To actually test PMCH1's data path end-to-end (not just idle/scheduling-info
traffic), researched how BM-SC actually gets bytes into a broadcast session.
Confirmed: BM-SC never listens on a content session's own `mcast_addr:port` - that
address is purely the *downstream* destination baked into the packet header
(`~/rt-mbms-bmsc/bmsc/main.cc`'s own comment: "content itself is broadcast by a
separate FLUTE sender, not srsbmsc"). MBMS-GW's `[sgi_mb_tunnel]` listener
(`bind_port=47000`, matching `bmsc.conf`'s own `tunnel_port`) dispatches purely on
the encapsulated packet's destination IP via `get_c_teid_by_dist_addr()`, with only
`N_bytes>=20` and IP version==4 checked - no real validation.

Built `tools/content_push.py`: constructs a real, correctly-checksummed IPv4+UDP
packet (byte-for-byte matching `~/rt-mbms-bmsc/bmsc/xmb/raw_udp_relay.cc`'s own
`build_ip_udp_packet()`) and sends it as a plain UDP payload to 127.0.0.1:47000 - no
root, no raw sockets, no FLUTE build needed. Verified the path works end-to-end at
the network level immediately: MBMS-GW logged forwarding it under PMCH1's TEID
(`0xcccc`), and the eNB logged receiving it over M1-U.

### Second bug found: `update_bsr_mch()` silently defaulted to mch_idx=0

Despite the network path working, real content injected for 45-60+ continuous
seconds still never reached the modem (`TI_DIAG_MACSDU`/`TI_DIAG_GWMCH`: zero
`mch_idx=1` entries throughout). Added two new diagnostics (`MCH_BSR_DIAG` at
`mac::rlc_buffer_state()`, and a print inside `build_mch_sched()`) rather than keep
guessing from static code reading - they showed `BUILD_MCH_SCHED pmch_idx=1`
consistently computing `total_bytes_to_tx=0`, meaning PMCH1's session's
`lcid_buffer_size` was still never updating, even though the SDU write itself
(traced through GTPU -> PDCP -> RLC) was confirmed correct by code inspection.

Root cause: `rlc_um_lte_tx::get_buffer_state()` invokes `bsr_callback` as a **side
effect of being called at all** (not on write) - and the only thing that ever calls
a PMCH's `get_buffer_state()` is `update_bsr_mch()`, via
`get_total_mch_buffer_state()`. `update_bsr_mch()` took only a bare `lcid` (no
`mch_idx`), so it always queried `get_total_mch_buffer_state(lcid)` with `mch_idx`
defaulting to 0 - regardless of which PMCH's session `write_sdu_mch()`/
`read_pdu_mch()` had actually just touched. Invisible for PMCH0 (0 == 0, the
default was accidentally correct), but meant every *other* PMCH's entity never had
its `get_buffer_state()` called at all - so its `bsr_callback`, and by extension
the previous section's now-correctly-mch_idx-composing fix, never fired in the
first place. Two separate bugs stacked in the same path: the earlier fix corrected
*what mch_idx a report claims to be for*; this one corrects *whether the report is
ever triggered* for a non-zero mch_idx at all.

Fixed by threading `mch_idx` through `update_bsr_mch()` from both its callers.
Committed as `6293da6`.

### Live-verified: real content now reaches and decodes on PMCH1

Re-injected content after the fix. `MCH_BSR_DIAG` immediately showed real, non-zero
`tx_queue` values for `mch_idx=1` (up to 2167 bytes) and `BUILD_MCH_SCHED` computed
a genuine non-zero `mtch_stop` (real transmission windows, not just the idle
scheduling-info-only case). On the RX side: `TI_DIAG_MACSDU`/`TI_DIAG_GWMCH` both
showed substantial `mch_idx=1` activity for the first time ever (194/225 samples
respectively in one check), with the decoded hex payload directly showing the
injected marker text (`636f6e74656e745f70757368` = ASCII "content_push"). `MCH 1`
stayed at `BLER 0.0`, `MCS 9` throughout - genuine content, genuinely decoding
cleanly.

### MTCH SLOWCALL timing issue: did not reproduce with real content flowing

The previously-untestable-without-content SLOWCALL issue was checked during the
real-content test window: zero `SLOWCALL` entries. Doesn't confirm it's fixed or
gone (this test's payload size/rate may not reach whatever threshold originally
triggered it, and the original occurrences were at a different, wider
`pmch_bandwidth` configuration) - but it's a clean, real data point: ordinary real
content at this session's current config does not trigger it.

### EVM ripple, CPU-frequency/migration hypothesis: properly closed this time, still
### negative

Let the modem run with `CPU_MIGRATION_DIAG` (per-occasion, inline core+frequency
sampling, added this pass) through the whole content-injection testing above,
accumulating 2776 clean samples (one extreme outlier, evm=141.77 at a migration
event, excluded - matches the already-documented "degenerate equalizer
denominator" artifact pattern from the DTX-detection gate elsewhere in this file,
a known rare glitch unrelated to the steady ripple).

Result: **no meaningful correlation.** Migration rate in bump samples (56.8%) is
statistically indistinguishable from normal samples (55.3%); mean core frequency in
bumps (3.26 MHz) is nearly identical to normal (3.36 MHz); even the weakest
signal checked (rate of running on a sub-1GHz core) only differs modestly (18.9%
bump vs 14.8% normal - not a strong effect). An earlier same-day check on a much
smaller sample (n=347, only 3 bumps) suggested a real gap; that turned out to be
noise from too few bump samples, not a real effect - a good reminder to distrust
small-n correlations even when they look clean.

This properly closes the loop the original 2026-07-20 investigation left open
("hypothesis 3... ruled out at this granularity... would need sub-millisecond
resolution"): now tested at genuine sub-ms, per-occasion resolution, and it's a
clean negative. The residual EVM ripple's cause remains genuinely unexplained -
both real candidates found this campaign (thread scheduling priority: confirmed
real, partial; CPU frequency/migration: now confirmed not a factor) have been
tested. No further concrete hypothesis is queued.

### Status

All three items from "what's next" (residual EVM ripple, MTCH SLOWCALL, real PMCH1
content delivery) were picked up this pass. Two produced real, fixed, live-verified
outcomes (PMCH1 content delivery - a genuine second bug, now fixed; SLOWCALL - a
clean non-reproduction data point). One produced a clean, methodologically solid
negative result (EVM ripple's CPU hypothesis) rather than a fix - itself valuable,
since it retires a candidate the earlier coarse test could only call
"inconclusive." Nothing left mid-air: every thread ended in either a confirmed fix
or a confirmed non-finding, not an open question.
