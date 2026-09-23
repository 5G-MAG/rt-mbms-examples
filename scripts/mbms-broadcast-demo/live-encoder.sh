#!/bin/bash
# Looping live encoder for this demo: plays the configured source on repeat and writes a
# rolling DASH (or HLS) window into the local origin's document root. -stream_loop -1 is
# what makes the content loop forever, so the receiver keeps getting new segments instead
# of a fixed-length presentation that ends.
#
# Same approach as rt-mbs-examples/scripts/mbs-broadcast-demo/live-encoder.sh, with the
# format switchable because rt-mbms-application plays either and the older demo-content
# templates are HLS.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh

SRC="${LIVE_SRC:-$LIVE_SOURCE_MEDIA}"
DST="${LIVE_DST:-$MEDIA_ROOT/$LIVE_STREAM_NAME}"
SEG="$LIVE_SEG_DURATION"

require_cmd ffmpeg

# Nothing in this demo depends on what the picture shows, so a missing source file is not
# a reason to be unable to run it: fall back to a generated test pattern with a tone,
# which ffmpeg synthesises itself. A deployment with its own media sets LIVE_SOURCE_MEDIA.
if [[ -f "$SRC" ]]; then
    log "source: $SRC"
    input=(-stream_loop -1 -i "$SRC")
else
    log "source: none at $SRC, generating a test pattern instead (set LIVE_SOURCE_MEDIA to use your own)"
    # lavfi's size takes WxH, the scale filter takes W:H, so the same setting is spelled
    # both ways rather than configured twice.
    input=(-f lavfi -i "testsrc2=size=${LIVE_SCALE/:/x}:rate=$LIVE_FPS" -f lavfi -i "sine=frequency=440:sample_rate=48000")
fi

mkdir -p "$DST"
rm -f "$DST"/*.m4s "$DST"/*.ts "$DST"/*.tmp "$DST"/*.mpd "$DST"/*.m3u8 2>/dev/null || true

# A radio channel is encoded audio-only, with no video track at all. Two reasons, and the first
# is the one that matters here: a broadcast bearer has no retransmission, so an object is
# recovered only if every one of its blocks arrives, and the video track makes the objects
# several times larger and correspondingly more fragile. Carrying a radio service's picture over
# the air also has no purpose. LIVE_TYPE comes from the channel's own "type" in channels.json,
# using the same vocabulary as the DVB-I demo's: linear or radio.
if [[ "${LIVE_TYPE:-linear}" == "radio" ]]; then
    common=(-re -fflags +genpts "${input[@]}"
            -vn -c:a aac -ar 48000 -b:a "$LIVE_AUDIO_BITRATE")
    DASH_ADAPTATION_SETS="${DASH_ADAPTATION_SETS:-id=0,streams=a}"
else
    common=(-re -fflags +genpts "${input[@]}"
            -vf "scale=$LIVE_SCALE" -c:v libx264 -preset "$LIVE_X264_PRESET" -tune zerolatency
            -profile:v main -pix_fmt yuv420p
            -b:v "$LIVE_VIDEO_BITRATE" -maxrate:v "$LIVE_VIDEO_BITRATE" -bufsize:v 800k
            -g $((SEG*LIVE_FPS)) -keyint_min $((SEG*LIVE_FPS)) -sc_threshold 0 -r "$LIVE_FPS"
            -c:a aac -ar 48000 -b:a "$LIVE_AUDIO_BITRATE")
fi

if [[ "$LIVE_FORMAT" == "hls" ]]; then
    # Two-level HLS: a master playlist (manifest.m3u8, the entry point the xMB session
    # announces) referencing one variant playlist (stream.m3u8) which lists the segments.
    # Not cosmetic: rt-mbms-client treats the announced entry point as a master playlist
    # and republishes its first variant entry to the player. Given a single-level media
    # playlist it publishes a master whose variant URL is a .ts segment, which no player
    # can parse. One muxed rendition is enough; the level of indirection is what matters.
    log "encoding HLS -> $DST (${SEG}s segments, ${LIVE_WINDOW}-segment window)"
    exec ffmpeg "${common[@]}" \
      -f hls -hls_time "$SEG" -hls_list_size "$LIVE_WINDOW" \
      -hls_flags delete_segments+independent_segments \
      -hls_segment_filename "$DST/segment-%05d.ts" \
      -master_pl_name manifest.m3u8 \
      "$DST/stream.m3u8"
else
    # Audio and video are written as two Adaptation Sets because ffmpeg's DASH muxer
    # requires every stream in an Adaptation Set to share a codec type and rejects a
    # mixed set at header-write time. ISO/IEC 23009-1 admits either arrangement, so this
    # is a tool limitation, not a specification one. DASH_ADAPTATION_SETS overrides it
    # for a packager that can do more.
    log "encoding DASH -> $DST (${SEG}s segments, window $LIVE_WINDOW + $LIVE_EXTRA_WINDOW)"
    exec ffmpeg "${common[@]}" \
      -seg_duration "$SEG" -use_template 1 -use_timeline 1 \
      -window_size "$LIVE_WINDOW" -extra_window_size "$LIVE_EXTRA_WINDOW" \
      -adaptation_sets "${DASH_ADAPTATION_SETS:-id=0,streams=v id=1,streams=a}" \
      -f dash "$DST/manifest.mpd"
fi
