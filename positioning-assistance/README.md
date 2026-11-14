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
| **Status** | Experimental. The RSTD measurements are simulated: there is no PRS generation or ToA measurement in rt-mbms-tx or rt-mbms-modem. `bps/prs.py` models the UE side in software (PRS per TS 36.211, a simulated channel, a ToA estimator), so the demo can take its RSTDs from waveforms instead of a formula |

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
| `bps/live.py`, `bps/sim.py` | A reader of the modem's SIB16 output; the software-only simulation |

`SPEC-CLAIMS.md` lists every specification sentence the code relies on, with the document and version, and what is this project's own design.
`results/` holds the simulation output and the comparison of option 1 with the other option in the proposal (the UE reading each cell's own SIB16
label), which is why that option is not implemented.

## Run it

Software only, no radio: `python3 -m unittest discover -s tests` and `python3 -m bps.sim --trials 300 --seed 7` (needs numpy).

Over the demo stack: see "Positioning assistance" in `scripts/mbms-broadcast-demo/README.md` (`09-start-positioning.sh`, `10-run-positioning-ue.sh`).
In that demo the document and the network time are live and the RSTDs are computed from a known position, on a ZMQ software radio with one eNB.
With `POS_MEASURE=prs` (`--measure prs` on the UE) they are estimated from simulated PRS waveforms of the four virtual cells instead: still not from the radio.

Verification: 68 unit tests (`python3 -m unittest discover -s tests`), and reset-rig runs of the demo. With RSTDs from the formula, the UE's fixes had
rmse 9.0 to 10.0 m against 9.7 to 10.0 m claimed. With RSTDs from waveforms (two runs, 27 fixes each, same random seed so the same noise), rmse 10.2 and
10.4 m against 11.9 m claimed, and one fix in each run at 2.05 claimed sigma. The waveform path uses the reporting step of TS 36.133 clause 9.1.10.3
(1 Ts up to 4096 Ts, 5 Ts above) to weight each RSTD; with one weight for all, the cell above 4096 Ts gave a slow error of up to 18 m against 10 m claimed.
Known limits: PRS generation and ToA measurement are missing from the radio; the waveform model has one path per cell, no frequency offset, and the PBCH, PSS and
SSS exclusion is the pattern of an MBMS-dedicated cell without PBCH repetition or CAS muting; PRS is defined for 15 kHz spacing only and the MBSFN subframes of the demo carrier
are 1.25 kHz, so the lab puts every cell's PRS in the cell acquisition subframes (index 2435), and that an MBMS-dedicated cell may send PRS is **not established**
(SPEC-CLAIMS.md C28 to C32); the BM-SC does not send file objects of about 1.5 to 3 kB with application-layer FEC on, so the
session runs with FEC off.
