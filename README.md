<h1 align="center">MBMS Examples</h1>
<p align="center">
  <img src="https://img.shields.io/badge/Status-Under_Development-yellow" alt="Under Development">
  <img src="https://img.shields.io/github/v/tag/5G-MAG/rt-mbms-examples?label=version" alt="Version">
  <img src="https://img.shields.io/badge/License-5G--MAG%20Public%20License%20(v1.0)-blue" alt="License">
</p>

## Introduction

Example projects that make use of other 5G-MAG repositories such as rt-mbms-client and rt-mbms-modem.

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
