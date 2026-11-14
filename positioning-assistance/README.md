# Positioning assistance over 5G Broadcast

Example project for rt-mbms-examples: the broadcaster and UE sides of one way to position a receiver from a 5G Broadcast signal, built on the
existing rt-mbms components. It follows option 1 of the "Positioning and Navigation using 5G Broadcast" proposal.

## At a glance

|  |  |
|---|---|
| **Implements** | No specification. It is an experiment on top of what 3GPP defines: the time in SIB16 (TS 36.331 clauses 6.3.1 and 6.3.2), RSTD (TS 36.214 clause 5.1.12) and PRS occasions (TS 36.211 clause 6.10.4.3). LPP defines no UE-based LTE OTDOA mode (TS 37.355 V19.3.0, `otdoa-Mode`), so the UE-based processing here is outside LPP |
| **Role** | Example: a broadcaster that keeps the cells' timing current and publishes it, and a UE that uses it |
| **Works with** | [rt-mbms-tx](https://github.com/5G-MAG/rt-mbms-tx) (SIB16 stamped with the network time), [rt-mbms-modem](https://github.com/5G-MAG/rt-mbms-modem) (time reference and sample-clock anchor), [rt-mbms-bmsc](https://github.com/5G-MAG/rt-mbms-bmsc) and [rt-mbms-application-provider](https://github.com/5G-MAG/rt-mbms-application-provider) (the session), [rt-libflute](https://github.com/5G-MAG/rt-libflute) (`flute-receiver`) |
| **Part of** | [5G Broadcast - TV and Radio Services](https://www.5g-mag.com/reference-tools/5g-broadcast) |
| **Status** | Experimental. With `POS_RIG=1` the chain is real from the document to the position: rt-mbms-tx sends PRS, a mixer adds three emulated neighbouring cells to its IQ, rt-mbms-modem measures all four cells' time of arrival on the PRS it receives, and the UE pushes the PRS information of the document down to the modem (slide 15 of the proposal) and solves from what comes back. Cells 2 to 4 are emulated, the channel is one path per cell, and whether an MBMS-dedicated cell may send PRS at all is not established (SPEC-CLAIMS.md C28 to C32). Without the rig the time differences come from the geometry, from simulated PRS waveforms or from simulated PSS and SSS |

## The idea (option 1)

The eNBs need not be time synchronised, as long as each has a delay against the reference eNB that stays constant. A single SFN'ed SIB16 gives
the network time. The relative transmit delay (RTD) of each cell, with its drift and uncertainty, travels to the UE in an assistance document,
FLUTE-delivered in a reserved MBMS session and refreshed every few seconds.

| Module | Role |
|---|---|
| `bps/rtd_monitor.py` | Broadcaster: RTD and drift of every cell from a monitoring receiver's timestamps, with a calibrated uncertainty |
| `bps/publisher.py` | Broadcaster: the document, with a version, a validity window and the RTD. Nothing is sent until the monitor has data |
| `bps/xmb.py` | Provisions the Files/Push session through the portal and pushes the document over xMB-U |
| `bps/assistance.py` | The document format (a draft schema, `bps-assistance/0-draft`, not a standard) and its validation |
| `bps/ue.py` | UE: keeps the newest valid document, takes the network time from the SIB16 anchor, applies RTD plus drift at that time, and solves |
| `bps/solver.py`, `bps/timebase.py`, `bps/geo.py` | Weighted least squares on the RSTDs; PRS occasions and SIB16 time decoding; coordinates |
| `bps/lab.py`, `bps/apps.py` | A synthetic world for the demo and the two command lines the demo scripts use |
| `bps/prs.py` | PRS sequence and mapping (TS 36.211 clauses 6.10.4.1, 6.10.4.2, 7.2), an OFDM waveform of one PRS subframe, and a correlation ToA estimator that gives RSTDs from simulated received waveforms. Not part of the radio |
| `bps/sync.py` | The alternative of slide 13, note 1: PSS and SSS (TS 36.211 clause 6.11) in the cell acquisition subframe, and a time-difference estimator with interference cancellation over a simulated channel. Not part of the radio |
| `bps/modem_prs.py` | The UE side of slide 15: pushes the PRS information of the document (not the positions or the RTD) down to the modem over its REST interface, and takes the per-cell times of arrival back as RSTDs against the reference cell, using a cell only if its correlation peak stands out (peak over median at least 9) |
| `bps/rig.py` | A mixer for the transmitter's IQ stream: locks onto the real cell's cell acquisition subframes and adds emulated neighbouring cells (their PRS, at their propagation plus RTD delay, sub-sample accurate), so a receiver sees several cells on one eNB. Not a channel model |
| `bps/compare.py` | The experiments behind the numbers: the error table of `bps/sync.py` and the PRS against PSS/SSS comparison |
| `bps/live.py`, `bps/sim.py` | A reader of the modem's SIB16 output; the software-only simulation |

`SPEC-CLAIMS.md` lists every specification sentence the code relies on, with the document and version, and what is this project's own design.
`results/` holds the simulation output and the comparison of option 1 with the other option in the proposal (the UE reading each cell's own SIB16
label), which is why that option is not implemented.

## Run it

Software only, no radio: `python3 -m unittest discover -s tests` and `python3 -m bps.sim --trials 300 --seed 7` (needs numpy).

Over the demo stack: see "Positioning assistance" in `scripts/mbms-broadcast-demo/README.md` (`09-start-positioning.sh`, `10-run-positioning-ue.sh`).
In that demo the document and the network time are live, on a ZMQ software radio with one eNB. By default the time differences are computed from a known position;
`POS_MEASURE=prs` (`--measure prs` on the UE) estimates them from simulated PRS waveforms of the four virtual cells and `--measure sync` from simulated PSS and SSS. `POS_RIG=1 POS_MEASURE=modem`
(`--measure modem`) takes them from the modem, which measures the PRS it receives: see "The rig" below.

Verification: 151 unit tests (`python3 -m unittest discover -s tests`, about 80 s), and reset-rig runs of the demo. With the time differences from the
formula: 30 fixes, rmse 8.9 m against 10.0 m claimed. From PRS waveforms (three runs, 25 fixes each, same random seed so the same noise): rmse 10.3, 10.3 and 9.9 m against 11.9 m claimed, worst
fix 2.02 claimed sigma. From the PSS and SSS (two runs, 13 fixes each, the estimate takes about 2 s): rmse 5.2 and 5.3 m against 7.0 m claimed, worst fix 1.26 claimed sigma. The PRS path uses the
reporting step of TS 36.133 clause 9.1.10.3 (1 Ts up to 4096 Ts, 5 Ts above) to weight each RSTD; with one weight for all, the cell above 4096 Ts gave a slow error of up to 18 m against 10 m claimed.
PSS and SSS as the source (`--measure sync`): the same chain, with the time differences estimated from the cells' PSS and SSS, which are on the carrier whatever the answer
to the PRS question. Under equal conditions (UE-internal estimates, one path per cell, same noise, same solver, no reporting step) `python3 -m bps.compare fixes` gives fixes
with PRS at 0.6 and 1.1 m rmse (12.5 and 2.5 dB per resource element) and with PSS/SSS at 1.4, 4.4 and 14.6 m (20, 10 and 0 dB per resource element): PRS is about 5 times
more accurate, as its bandwidth is (4.5 against 0.93 MHz). The PSS/SSS estimator needed interference cancellation: without it the cells' own signals bias each other (69 to
94 ns at any SNR, `results/sync-sigma-2026-11-14.txt`) and a cell 10 dB stronger than another captures it. With the TS 36.133 reporting step the PRS path is limited to about 10 m.
With the modem as the source (`POS_RIG=1 POS_MEASURE=modem`, three runs, 26, 27 and 28 fixes, the last on the final modem build, the emulated RTDs drifting as the monitor sees them): rmse 5.1, 5.1 and 5.0 m against 6.5 m claimed, worst fix 1.22, 1.36 and 1.16
claimed sigma, HLS and FILE services both present and the HLS media sequence advancing during the run.

The rig (`POS_RIG=1 ./start-all.sh`, see the demo README): the real transmitter sends PRS in its cell acquisition subframes (index 2435), its IQ goes through `bps.rig`, which adds cells 2 to 4 as
PRS-only subframes at the delays the lab world gives them, and the real modem measures all four. Live, with the modem's own correlator and the assistance data pushed over its REST
interface: at -12 dB the emulated cells are barely told from noise (peak over median 5.5 to 6.7 against 4.8 for a cell that is not there), at -6 dB the RSTDs of cells 2, 3 and 4 against cell 1 came out
at errors of -8.8, -5.4 and -9.5 ns with standard deviations of 8.7, 11.2 and 7.1 ns over 32 occasions (peak over median 12 to 13; cell 1 itself 25). The cyclic prefix of the real cell is a parameter (`--cp`, `POS_RIG_CP`; TS 36.211 clause 6.1 leaves it to the cell): the mixer and the Python reference handle both, and the rig locks on a live
normal-CP cell and, with the modem's cell search following the cyclic prefix (`modem.phy.detect_cp`, finding 19), the whole chain runs on a normal-CP cell. Each cell may also state its own cyclic prefix in the document (`Cell.cp_length`, from the per-cell `cpLength` of TS 37.355, SPEC-CLAIMS.md C41; mandatory on the reference cell, left out on a neighbour that has the reference cell's, C51): the UE pushes it to the modem, which builds that cell's reference with it. Live, with the real cell extended and cell 3 normal (`POS_CELL_CP="3=normal"`), fixes were made with the document stating it; with the document not stating it (`POS_RIG_CELL_CP` set for the mixer alone) the modem did not detect cell 3 (peak over background 4 to 5, needs 9) and gave no fix. The emulated cells' power is a
parameter (`POS_RIG_POWER_DB`, default -6): higher means better measurements and more interference to the real cell's own subframes.
Known limits: the PRS of the real cell is only on the air if the transmitter is given `[prs]` (the rig does that); cells 2 to 4 are emulated, one path each, no frequency offset (the modem does not compensate one); PRS punctures the PDSCH of the cell acquisition subframe and is measured about once a second in the unoptimised modem build (most occasions are skipped); the waveform model has one path per cell, no frequency offset, and the PBCH, PSS and
SSS exclusion is the pattern of an MBMS-dedicated cell without PBCH repetition or CAS muting; PRS is defined for 15 kHz spacing only and the MBSFN subframes of the demo carrier
are 1.25 kHz, so the lab puts every cell's PRS in the cell acquisition subframes (index 2435), and that an MBMS-dedicated cell may send PRS is **not established**
(SPEC-CLAIMS.md C28 to C32); the BM-SC does not send file objects of about 1.5 to 3 kB with application-layer FEC on, so the
session runs with FEC off.
