# rt-mbms-examples
Example projects that make use of other 5G-MAG repositories such as the mw and the modem

## FLUTE ffmpeg
The goal of this example project is to provide a tool that enables rt-mbms-mw development without the need for the rt-mbms-modem. We use ffmpeg to create a DASH or HLS live stream from a VoD file. The resulting manifest files and segments are written to a watchfolder and send via rt-libflute as a multicast to the rt-mbms-mw for further processing. rt-wui or a plain dash.js/hls.js can be used for playback.

More information can be found in the corresponding [subfolder](https://github.com/5G-MAG/rt-mbms-examples/tree/development/flute-ffmpeg).