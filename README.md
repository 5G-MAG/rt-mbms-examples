<h1 align="center">MBMS Examples</h1>
<p align="center">
  <img src="https://img.shields.io/badge/Status-Under_Development-yellow" alt="Under Development">
  <img src="https://img.shields.io/github/v/tag/5G-MAG/rt-mbms-examples?label=version" alt="Version">
  <img src="https://img.shields.io/badge/License-5G--MAG%20Public%20License%20(v1.0)-blue" alt="License">
</p>

## Introduction

Example projects that make use of other 5G-MAG repositories such as the mw and the modem

### FLUTE ffmpeg

The goal of this example project is to provide a tool that enables rt-mbms-mw development without the need for the rt-mbms-modem. We use ffmpeg to create a DASH or HLS live stream from a VoD file. The resulting manifest files and segments are written to a watchfolder and send via rt-libflute as a multicast to the rt-mbms-mw for further processing. rt-wui or a plain dash.js/hls.js can be used for playback.

More information can be found in the corresponding [subfolder](https://github.com/5G-MAG/rt-mbms-examples/tree/development/flute-ffmpeg).
