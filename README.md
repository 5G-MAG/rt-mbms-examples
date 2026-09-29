<p align="center">
  <img src=".github/banner.svg" width="100%" alt="Reference Tools · 5G Broadcast - TV and Radio Services: MBMS Tools and Examples">
</p>

<p align="center">
  Example projects built on other 5G-MAG MBMS repositories, such as rt-mbms-mw and rt-mbms-modem.
</p>

<p align="center">
  <img alt="Status: under development"
    src="https://img.shields.io/badge/Status-Under%20Development-e67e22">
  <a href="https://github.com/5G-MAG/rt-mbms-examples/releases"><img alt="Version"
    src="https://img.shields.io/github/v/release/5G-MAG/rt-mbms-examples?label=Version"></a>
  <a href="License"><img alt="License: 5G-MAG Public License v1.0"
    src="https://img.shields.io/badge/License-5G--MAG%20PL%20v1.0-blue"></a>
</p>

<p align="center">
  <a href="https://www.5g-mag.com/reference-tools/5g-broadcast/">Project page</a> &nbsp;&middot;&nbsp;
  <a href="https://github.com/5G-MAG/rt-mbms-examples/issues">Issues</a> &nbsp;&middot;&nbsp;
  <a href="https://www.5g-mag.com/contributing">Contributing</a>
</p>

---

## At a glance

|  |  |
|---|---|
| **Part of** | [5G Broadcast - TV and Radio Services](https://www.5g-mag.com/reference-tools/5g-broadcast/), alongside [rt-libflute](https://github.com/5G-MAG/rt-libflute), [rt-mbms-application](https://github.com/5G-MAG/rt-mbms-application), [rt-mbms-application-provider](https://github.com/5G-MAG/rt-mbms-application-provider), [rt-mbms-bmsc](https://github.com/5G-MAG/rt-mbms-bmsc), [rt-mbms-client](https://github.com/5G-MAG/rt-mbms-client), [rt-mbms-gw](https://github.com/5G-MAG/rt-mbms-gw), [rt-mbms-modem](https://github.com/5G-MAG/rt-mbms-modem), [rt-mbms-mw-android](https://github.com/5G-MAG/rt-mbms-mw-android), [rt-mbms-tx](https://github.com/5G-MAG/rt-mbms-tx) and [rt-mbms-tx-for-qrd-and-crd](https://github.com/5G-MAG/rt-mbms-tx-for-qrd-and-crd) |

## Introduction

This repository collects example projects that use other 5G-MAG MBMS repositories. It holds two:
FLUTE ffmpeg, in [`flute-ffmpeg/`](flute-ffmpeg/), and a Docker setup for the MBMS client
components, in [`mbms-client-docker-setup/`](mbms-client-docker-setup/).

### FLUTE ffmpeg

FLUTE ffmpeg lets you develop rt-mbms-mw without rt-mbms-modem. ffmpeg turns a VoD file into a
DASH or HLS live stream. The manifests and segments are written to a watch folder and sent through
rt-libflute as multicast to rt-mbms-mw for processing. Playback uses rt-wui, or plain dash.js or
hls.js.

Installation, building, configuration and running are described in the
example's [subfolder](https://github.com/5G-MAG/rt-mbms-examples/tree/development/flute-ffmpeg).

### MBMS client Docker setup (work in progress)

Dockerfiles for rt-mbms-modem, rt-mbms-mw and rt-wui, and a `docker-compose.yml` that at present
starts only the modem container. The modem image clones rt-mbms-modem from GitHub and builds it;
the container decodes a recorded sample file from a `recordings` folder, which you provide (it is
not in the repository), and routes the multicast output of its tun interface. The rt-mbms-mw and
rt-wui services are commented out. The folder has no instructions of its own yet.

## Contributing

Contributions are welcome. How to raise an issue, fork the repository and open a pull request, and
the Contributor License Agreement required before code can be merged, are described at
<https://www.5g-mag.com/contributing>.

## License

Distributed under the 5G-MAG Public License v1.0. See [License](License). Third-party software the
examples use is listed in [Attribution_notice](Attribution_notice).
