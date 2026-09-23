<h1 align="center">MBMS Examples</h1>
<p align="center">
  <img src="https://img.shields.io/badge/Status-Under_Development-yellow" alt="Under Development">
  <img src="https://img.shields.io/github/v/tag/5G-MAG/rt-mbms-examples?label=version" alt="Version">
  <img src="https://img.shields.io/badge/License-5G--MAG%20Public%20License%20(v1.0)-blue" alt="License">
</p>

## Introduction

Example projects that make use of other 5G-MAG repositories such as rt-mbms-client and rt-mbms-modem.

## Running the demo

**Before the first run**, once per machine: the stack must already be built, and the ZeroMQ
software radio needs a SoapySDR `zmqrx` bridge that is not shipped anywhere and has to be built
from source. These scripts run what is built; they build nothing. The full list is in
[the demo's Prerequisites](scripts/mbms-broadcast-demo/README.md#prerequisites).

After that, every run is the four commands below.

```bash
./demo doctor              # will it start here? changes nothing
./demo up
./demo status
./demo down                # stops it and verifies nothing of it is left
./demo down --all          # stops all three demos, in every repository it can find
```

`./demo up` runs the checks first and refuses to start on a conflict, naming what to stop. The
checks exist because three demos in this project share a machine and cannot see each other:

| | this repository | rt-mbs-examples | rt-dvb-i-examples |
|---|---|---|---|
| media origin | :3005 | :3004 | :3004 |
| ZMQ radio control | **2100, 2101** | **2100, 2101, same as this one** | none |
| network namespace | `mbms-rx` | `ns-gnb` | none |
| player / portal | :3000, :8080 | :3050, :8091 | :5000, :4000 |
| service list registry | none | none | :7000 |

So this demo and the MBS one cannot both hold the radio. Whichever starts second used to fail in
the radio rather than anywhere informative; `./demo doctor` now says which demo is in the way and
how to stop it. Restarting this demo while it is already up is fine and is not treated as a
conflict, because `start-all.sh` stops it first.

**This demo and the DVB-I one can run at the same time.** They share no port, no network namespace
and no process name: this one's origin is its own `media-server.js` on :3005, the DVB-I demo runs
`express-mock-media-server` on :3004, and neither `down` can reach the other's processes. Start
them in either order. The only thing they compete for is memory, which is why `doctor` warns about
a neighbouring demo rather than refusing to start; on a machine that is tight, bring this one up
first, since it is the larger of the two.

`./demo down` does not trust the stop scripts. It runs them, then checks this demo's processes,
ports and network namespace are actually gone and clears anything left, so the next run starts
from nothing. `--all` does the same for the other two demos, finding their checkouts beside this
one; set `MBS_EXAMPLES_DIR` or `DVBI_EXAMPLES_DIR` if they live somewhere unusual.

Add `--force` to `up` to start anyway. `DEMO_MIN_FREE_MB` overrides the memory floor.

The scripts under `scripts/mbms-broadcast-demo/` are unchanged and can still be run directly.

### Demo content

Content starts with the demo. `./demo up` launches a looping ffmpeg encoder that publishes an HLS
presentation into this demo's own origin on :3005, so there is nothing extra to run.

The source clips live in `~/MWC_TV_RADIO/` (`TV_1.mp4`, `RADIO.mp4` and their logos). That
directory is required: without it the encoder has nothing to loop and the demo comes up with an
empty origin.

```bash
# check content is actually flowing, once the demo is up
curl -s http://127.0.0.1:3005/tv_1_live/manifest.m3u8 | head
./demo status                       # shows segment count, MCCH sessions and the MCH error rates
```

The player picks the format from a select box; pick the one matching `LIVE_FORMAT`. To change what
plays or how it is encoded, set these before `./demo up`:

| variable | default | what it does |
|---|---|---|
| `LIVE_SOURCE_MEDIA` | `~/MWC_TV_RADIO/TV_1.mp4` | the clip the encoder loops |
| `LIVE_STREAM_NAME` | `tv_1_live` | the path it publishes under |
| `LIVE_FORMAT` | `hls` | `hls` or `dash` |
| `LIVE_VIDEO_BITRATE`, `LIVE_SCALE` | `400k`, `640:360` | keep these modest: the ZeroMQ radio is the bottleneck, not the encoder |

```bash
LIVE_SOURCE_MEDIA=~/MWC_TV_RADIO/RADIO.mp4 LIVE_STREAM_NAME=radio_live ./demo up
```

### FLUTE ffmpeg

The goal of this example project is to provide a tool that enables rt-mbms-client development without the need for the
rt-mbms-modem. We use ffmpeg to create a DASH or HLS live stream from a VoD file. The resulting manifest files and
segments are written to a watchfolder and send via rt-libflute as a multicast to rt-mbms-client for further processing.
rt-mbms-application or a plain dash.js/hls.js can be used for playback.

More information can be found in the
corresponding [subfolder](https://github.com/5G-MAG/rt-mbms-examples/tree/development/flute-ffmpeg).

### mbms-broadcast-tutorial

`scripts/tmux/mbms-broadcast-tutorial/` (on the `development` branch) launches the *entire* LTE
Broadcast chain -- EPC, eNB, MBMS-GW, BM-SC, modem, client, and the web application -- as a
software-radio (ZeroMQ) loopback on one host, with no SDR hardware. It also ships the `demo-content/`
tooling to load and activate a real xMB Application/DASH-HLS session (loading external content, not
just the FLUTE-direct shortcut the flute-ffmpeg example above uses). See that directory's own
`README.md` for day-to-day usage, or its
[`TUTORIAL.html`](scripts/tmux/mbms-broadcast-tutorial/TUTORIAL.html) for a complete from-scratch
walkthrough across all ten repos. It needs one small piece that isn't shipped anywhere -- a
SoapySDR `zmqrx` bridge device plugin bridging the eNB's ZeroMQ transmit output into the modem's
usual `SoapySDR::Device::make()` radio path -- both docs above include the full working source.
