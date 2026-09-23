#!/bin/bash
# The whole MBMS Broadcast demo, in dependency order: transmit side, local live origin,
# receive side, then the xMB service that puts the content on air.
#
# Safe to run twice: it stops anything this demo previously started first. The transmit
# side is restarted from scratch rather than reused, because a session left active in a
# previous run makes the next activation fail in a way only a restart clears (see the
# tutorial's DEMO_RUNBOOK.md).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_cmd node
require_cmd ffmpeg
require_cmd curl
require_cmd python3

log "=== 0/4 clearing anything already running ==="
./stop-all.sh >/dev/null 2>&1 || true

log "=== 1/4 transmit side (EPC, eNB, MBMS-GW, BM-SC, portal) ==="
./01-start-transmit.sh

log "=== 2/4 local live origin (media server + looping encoder) ==="
./03-start-media-server.sh

log "=== 3/4 receive side (modem, client, application) in netns $NETNS ==="
./05-start-client-and-app.sh

log "=== 4/4 xMB services for the on-air channels: create and activate ==="
# One xMB service and one Application/Pull session per on-air channel, each on its own MBMS
# bearer. The origin serves all four channels; these are the ones that go over the air.
while IFS=$'\t' read -r ch_id ch_name ch_stream ch_tmgi ch_tsi ch_addr ch_port; do
    log "  $ch_name ($ch_stream) on TMGI service id $ch_tmgi, $ch_addr:$ch_port"
    LIVE_STREAM_NAME="$ch_stream" \
        DEMO_SERVICE_NAME="$ch_name" \
        DEMO_TMGI_SERVICE_ID="$ch_tmgi" \
        DEMO_TSI="$ch_tsi" \
        DEMO_MCAST_ADDR="$ch_addr" \
        DEMO_MCAST_PORT="$ch_port" \
        ./06-provision-live-service.sh
done < <(onair_rows)

# For the summary below: PORTAL_URL is set by portal_creds, and the stage scripts each ran
# in their own shell.
portal_creds

cat <<TXT

MBMS Broadcast demo is up.

  player UI    : http://$RX_ADDR:$APP_PORT/application   (the default player matches LIVE_FORMAT=$LIVE_FORMAT)
  cell broadcast: http://$RX_ADDR:$APP_PORT/cellbroadcast
  portal       : $PORTAL_URL                         (xMB and RAN tabs)
  alerts (CBC) : ${CBC_URL:-http://$CBC_HOST:$CBC_PORT}
  origin       : http://$MEDIA_HOST:$MEDIA_PORT/$(live_presentation_path)
  modem API    : http://$RX_ADDR:$MODEM_API_PORT/modem-api/
  client API   : http://$RX_ADDR:$CLIENT_API_PORT/client-api/

  To see the video: open the player UI, paste this into its "Manifest URL" box and press Load.
  The box starts empty, and Load does nothing until it has a URL. This is the client's own
  cache of what it received over the air, which is why the player reports segment source 5G-BC:

    $(broadcast_presentation_url)

$(print_portal_credentials)

  ./status.sh                 what is up, and what the radio and the client report
  ./07-send-alert.sh          send an ETWS/CMAS alert and confirm the modem receives it
  ./stop-all.sh               stop everything this demo started

Logs: this demo's own in $LOG_DIR, the stack's own in $STACK_LOG_DIR.
TXT
