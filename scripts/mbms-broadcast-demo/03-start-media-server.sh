#!/bin/bash
# Starts the local content origin and the looping live encoder that feeds it, then waits
# until the presentation is genuinely playable before returning.
#
# Waiting matters: the BM-SC's Pull ingest fetches the manifest once at activation and
# resolves the segments it lists. A manifest activated before the encoder has published
# segments describes nothing, so the session comes up healthy and delivers nothing.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_cmd node
require_cmd ffmpeg
require_cmd curl

PRESENTATION="$(live_presentation_path)"

run_bg "media server" media-server \
    env HOST="$MEDIA_HOST" PORT="$MEDIA_PORT" ROOT="$MEDIA_ROOT" node "$DEMO_ROOT/media-server.js"
wait_for_tcp "$MEDIA_HOST" "$MEDIA_PORT" 20 || die "media server did not come up (see $LOG_DIR/media-server.log)"
log "media server up: http://$MEDIA_HOST:$MEDIA_PORT/"

# One looping encoder per channel in channels.json. Every channel is encoded and served by the
# origin whether or not it is carried over the air, so this demo offers the same four channels
# under the same names as the MBS and DVB-I ones; which of them go on air is decided in step 4.
while IFS=$'\t' read -r ch_id ch_stream ch_source ch_type; do
    if pgrep -f "ffmpeg .*$MEDIA_ROOT/$ch_stream" >/dev/null 2>&1; then
        log "  $ch_id: encoder already running, leaving it alone"
        continue
    fi
    # Remove the previous run's presentation before starting the encoder. Without this the
    # wait below can be satisfied by a stale manifest still on disk from an earlier run, in
    # the moment before the encoder clears it, and a session would then be activated
    # against segments that no longer exist.
    rm -f "$MEDIA_ROOT/$ch_stream"/*.mpd "$MEDIA_ROOT/$ch_stream"/*.m3u8
    log "  $ch_id -> $ch_stream"
    LIVE_SOURCE_MEDIA="$CONTENT_ROOT/$ch_source" LIVE_STREAM_NAME="$ch_stream" \
        LIVE_TYPE="$ch_type" \
        run_bg "live encoder ($ch_id)" "live-encoder-$ch_id" bash "$DEMO_ROOT/live-encoder.sh"
done < <(channel_rows)

while IFS=$'\t' read -r ch_id ch_name ch_stream ch_tmgi ch_tsi ch_addr ch_port; do
    log "waiting for $ch_id to publish a playable $LIVE_FORMAT presentation"
    ch_entry="$ch_stream/$([[ "$LIVE_FORMAT" == "hls" ]] && echo manifest.m3u8 || echo manifest.mpd)"
    deadline=$((SECONDS + 150))
    while (( SECONDS < deadline )); do
        body="$(curl -s -m 3 "http://$MEDIA_HOST:$MEDIA_PORT/$ch_entry" || true)"
        if [[ "$LIVE_FORMAT" == "hls" ]]; then
            # The entry point is the master playlist, so the segments are one level down in
            # the variant it names. Both must be there: the master alone says nothing about
            # whether any media has been published yet.
            if [[ "$body" == *"#EXTM3U"* && "$body" == *"stream.m3u8"* ]]; then
                variant="$(curl -s -m 3 "http://$MEDIA_HOST:$MEDIA_PORT/$ch_stream/stream.m3u8" || true)"
                [[ $(grep -c '\.ts' <<<"$variant") -ge 2 ]] && break
            fi
        else
            # A dynamic MPD with at least one <S ...> entry in its timeline.
            [[ "$body" == *"<MPD"* && "$body" == *"<S "* ]] && break
        fi
        sleep 3
    done
    (( SECONDS < deadline )) || die "$ch_id published no playable presentation in 150s; see $LOG_DIR/live-encoder-$ch_id.log"
    log "presentation ready: http://$MEDIA_HOST:$MEDIA_PORT/$ch_entry"
done < <(onair_rows)
