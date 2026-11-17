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
| **Status** | Experimental. With `POS_RIG=1` the chain is real from the document to the position: rt-mbms-tx sends PRS, a mixer adds three emulated neighbouring cells to its IQ, rt-mbms-modem measures all four cells' time of arrival on the PRS it receives, and the UE pushes the PRS information of the document down to the modem (slide 15 of the proposal) and solves from what comes back. Cells 2 to 4 are emulated, the channel is one path per cell, and whether an MBMS-dedicated cell may send PRS at all is not established (SPEC-CLAIMS.md C28 to C32). Without the rig the time differences come from the geometry or from simulated PRS waveforms |

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
| `bps/sync.py` | The PSS and SSS (TS 36.211 clause 6.11) of a cell acquisition subframe, which the rig correlates with to find the real cell's subframes in the IQ stream. Nothing measures a time of arrival from them |
| `bps/modem_prs.py` | The UE side of slide 15: pushes the PRS information of the document (not the positions or the RTD) down to the modem over its REST interface, and takes the per-cell times of arrival back as RSTDs against the reference cell, using a cell only if its correlation peak stands out (peak over median at least 9) |
| `bps/rig.py` | A mixer for the transmitter's IQ stream: locks onto the real cell's cell acquisition subframes and adds emulated neighbouring cells (their PRS, at their propagation plus RTD delay, sub-sample accurate), so a receiver sees several cells on one eNB. Not a channel model |
| `bps/live.py`, `bps/sim.py` | A reader of the modem's SIB16 output; the software-only simulation |

`SPEC-CLAIMS.md` lists every specification sentence the code relies on, with the document and version, and what is this project's own design.
`results/` holds the simulation output and the comparison of option 1 with the other option in the proposal (the UE reading each cell's own SIB16
label), which is why that option is not implemented.

## Run it

Software only, no radio: `python3 -m unittest discover -s tests` and `python3 -m bps.sim --trials 300 --seed 7` (needs numpy).

Over the demo stack: see "Positioning assistance" in `scripts/mbms-broadcast-demo/README.md` (`09-start-positioning.sh`, `10-run-positioning-ue.sh`).
In that demo the document and the network time are live, on a ZMQ software radio with one eNB. By default the time differences are computed from a known position;
`POS_MEASURE=prs` (`--measure prs` on the UE) estimates them from simulated PRS waveforms of the four virtual cells. `POS_RIG=1 POS_MEASURE=modem`
(`--measure modem`) takes them from the modem, which measures the PRS it receives: see "The rig" below.

### Accuracy on the real platform (scenario runner)

`python3 -m bps.scenario_run scenario.json [--out DIR] [--seconds N]` runs one scenario through the real chain (the demo with `POS_RIG=1`, the publisher, the UE with
`POS_MEASURE=modem`) and stores the UE's fixes as JSON; `python3 -m bps.scenario_report result.json [more.json] -o report.html` turns one or several results into a page
(summary table, error distribution, east and north error of every fix, real against claimed error, time-difference error per cell). A scenario file sets the UE position,
the cell positions, the emulated RTDs, the received power of each emulated cell (explicitly or from a path loss exponent), the noise (`snr_db`), the carrier offset of a
cell, the noise of the monitoring receiver the document's RTD is estimated from (`monitor_noise_ns`, default 20) and the cyclic prefix; `bps/scenario.py` documents the
fields. A run is real time: about a minute to start, 20 s of warmup, then the UE's duration, a fix every 2 s at best. The error is the horizontal distance between the fix
and the position the scenario gives the UE.

What it shows: the accuracy of this implementation (modem time of arrival, document, SIB16, solver) under the conditions given, where measurement stops working, and
whether the solver's claimed accuracy holds. What it does not: field accuracy (cells 2 to 4 are PRS-only subframes added by the rig, each on one path: no multipath, no fading),
accuracy over an area (one run is one position), or a fixed monitor calibration error (only the monitor's timestamp noise is a parameter). Results of the first twelve runs
are in the Standards2Deployments register under `projects/mbms/evidence/2026-11-17-accuracy-simulator`.

#### Dashboard (5G Broadcast Positioning Emulator)

`python3 -m bps.dashboard [--port N]` serves a page at `http://127.0.0.1:8765/` (the first free port from there; standard library only) for the scenario runner, in the look of the
other 5G-MAG dashboards. **Scenario**: a preset (the files in `scenarios/`) or your own values, with a layout canvas to drag the four cells and the UE (plain metres, zoom from metres
to kilometres, no map data), the received power per cell as the run will use it, and Start run. **Live run**: the phase, the fixes as the UE records them, the error plots and the run
log, with a Stop button that brings the demo down. **Results**: the stored runs, one run's detail, a comparison of several, and a report page. One run at a time; a run is refused
while the MBS demo (open5gs, srsue, mbs-client) or another positioning demo is up, because they share the radio ports and the memory. The server binds to the loopback address,
answers only requests for that address, takes changes only as JSON from its own page, and never uses a path the page sends. A run goes on if the server is stopped.
The results and the scenarios the page started are in `results/scenarios/` (not tracked). Not in the dashboard yet: running a list of scenarios one after another (a sweep).

Verification: 205 unit tests (`python3 -m unittest discover -s tests`, about 60 s), and reset-rig runs of the demo. With the time differences from the
formula: 30 fixes, rmse 8.9 m against 10.0 m claimed. From PRS waveforms (three runs, 25 fixes each, same random seed so the same noise): rmse 10.3, 10.3 and 9.9 m against 11.9 m claimed, worst
fix 2.02 claimed sigma. The PRS path uses the
reporting step of TS 36.133 clause 9.1.10.3 (1 Ts up to 4096 Ts, 5 Ts above) to weight each RSTD; with one weight for all, the cell above 4096 Ts gave a slow error of up to 18 m against 10 m claimed.
With the TS 36.133 reporting step the PRS path is limited to about 10 m.
With the modem as the source (`POS_RIG=1 POS_MEASURE=modem`, three runs, 26, 27 and 28 fixes, the last on the final modem build, the emulated RTDs drifting as the monitor sees them): rmse 5.1, 5.1 and 5.0 m against 6.5 m claimed, worst fix 1.22, 1.36 and 1.16
claimed sigma, HLS and FILE services both present and the HLS media sequence advancing during the run.

The rig (`POS_RIG=1 ./start-all.sh`, see the demo README): the real transmitter sends PRS in its cell acquisition subframes (index 2435), its IQ goes through `bps.rig`, which adds cells 2 to 4 as
PRS-only subframes at the delays the lab world gives them, and the real modem measures all four. Live, with the modem's own correlator and the assistance data pushed over its REST
interface: at -12 dB the emulated cells are barely told from noise (peak over median 5.5 to 6.7 against 4.8 for a cell that is not there), at -6 dB the RSTDs of cells 2, 3 and 4 against cell 1 came out
at errors of -8.8, -5.4 and -9.5 ns with standard deviations of 8.7, 11.2 and 7.1 ns over 32 occasions (peak over median 12 to 13; cell 1 itself 25). The cyclic prefix of the real cell is a parameter (`--cp`, `POS_RIG_CP`; TS 36.211 clause 6.1 leaves it to the cell): the mixer and the Python reference handle both, and the rig locks on a live
normal-CP cell and, with the modem's cell search following the cyclic prefix (`modem.phy.detect_cp`, finding 19), the whole chain runs on a normal-CP cell. Each cell may also state its own cyclic prefix in the document (`Cell.cp_length`, from the per-cell `cpLength` of TS 37.355, SPEC-CLAIMS.md C41; mandatory on the reference cell, left out on a neighbour that has the reference cell's, C51): the UE pushes it to the modem, which builds that cell's reference with it. Live, with the real cell extended and cell 3 normal (`POS_CELL_CP="3=normal"`), fixes were made with the document stating it; with the document not stating it (`POS_RIG_CELL_CP` set for the mixer alone) the modem did not detect cell 3 (peak over background 4 to 5, needs 9) and gave no fix. The emulated cells' power is a
parameter (`POS_RIG_POWER_DB`, default -6): higher means better measurements and more interference to the real cell's own subframes.
Known limits: the PRS of the real cell is only on the air if the transmitter is given `[prs]` (the rig does that); cells 2 to 4 are emulated, one path each, by default with no frequency offset; with one (`POS_CELL_CFO`, an offset against the real cell, which the modem does not compensate) cell 3 was still measured at 100 and 300 Hz (fewer fixes) and lost at 1000 Hz (peak over background 5 against the 9 it needs), so the cells of an SFN network must be frequency-locked to within a few hundred Hz; PRS punctures the PDSCH of the cell acquisition subframe and is measured about once a second in the unoptimised modem build (most occasions are skipped); the waveform model has one path per cell, no frequency offset, and the PBCH, PSS and
SSS exclusion is the pattern of an MBMS-dedicated cell without PBCH repetition or CAS muting; PRS is defined for 15 kHz spacing only and the MBSFN subframes of the demo carrier
are 1.25 kHz, so the lab puts every cell's PRS in the cell acquisition subframes (index 2435), and that an MBMS-dedicated cell may send PRS is **not established**
(SPEC-CLAIMS.md C28 to C32); the BM-SC does not send file objects of about 1.5 to 3 kB with application-layer FEC on, so the
session runs with FEC off.
