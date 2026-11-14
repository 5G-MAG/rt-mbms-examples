#!/bin/bash
# Positioning assistance, option 1: starts the document publisher (monitoring -> assistance document -> xMB-U push into its own
# Files/Push session). The session is announced to the UE as an application service, so rt-mbms-client finds it from the service
# announcement, receives the file and serves it over HTTP; nothing else needs starting on the receive side.
#
#   ./start-all.sh     then     ./09-start-positioning.sh     then     ./10-run-positioning-ue.sh
#
# Stop it with ./stop-positioning.sh, or ./stop-all.sh which stops everything.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_cmd python3
require_cmd curl
[[ -d "$BPS_DIR/bps" ]] || die "no positioning code at $BPS_DIR (set BPS_DIR)"
netns_exists || die "the receive side is not up (namespace $NETNS): run ./start-all.sh first"
portal_creds

# The xMB-U push is authenticated with the portal's own client certificate; read the paths and the lab-only TLS setting from the
# portal's .env so this and the portal cannot disagree.
env_value() { sed -n "s/^$1=//p" "$PORTAL_ENV" | tail -1 | tr -d '"'"'"; }
CERT="$(env_value XMB_CLIENT_CERT_PATH)"; KEY="$(env_value XMB_CLIENT_KEY_PATH)"
[[ -f "$CERT" && -f "$KEY" ]] || die "the portal's xMB client certificate ($CERT) or key ($KEY) is missing"
INSECURE=""; [[ "$(env_value XMB_INSECURE_SKIP_SERVER_VERIFY)" == "true" ]] && INSECURE="--insecure"

run_bg "assistance publisher" pos-publisher \
    env PYTHONPATH="$BPS_DIR" python3 -u -m bps.apps publish \
    --xmbu-url "https://127.0.0.1:$XMB_C_PORT" --cert "$CERT" --key "$KEY" $INSECURE \
    --portal-url "$PORTAL_URL" --portal-user "$PORTAL_USER" --portal-password "$PORTAL_TOKEN" \
    --tmgi-service-id "$POS_TMGI_SERVICE_ID" --tsi "$POS_TSI" --mcast "$POS_MCAST_ADDR" --port "$POS_MCAST_PORT" \
    --c-teid "$POS_C_TEID" --path "$POS_FILE_PATH" --interval "$POS_INTERVAL" --validity "$POS_VALIDITY" \
    --leap-seconds "$LEAP_SECONDS" --cp "$(rig_cp)" $([[ "$POS_DELIVER" == url ]] && echo --deliver url --serve-dir "$MEDIA_ROOT/positioning" --serve-url "http://$MEDIA_HOST:$MEDIA_PORT/positioning/assistance.json") $([[ "$POS_FEC" == true ]] && echo --fec) ${POS_BROADCAST_AREA:+--broadcast-area "$POS_BROADCAST_AREA"} $(e="$(pos_ref_ecgi)"; [[ -n "$e" ]] && echo --ref-ecgi "$e") ${POS_SERVICE_RADIUS_M:+--service-radius-m "$POS_SERVICE_RADIUS_M"} $([[ "$POS_SFN_FROM_MODEM" == 1 ]] && echo --sfn-from-modem "http://$RX_ADDR:$MODEM_API_PORT/modem-api/sib_info") $(for c in $POS_CELL_CP; do printf -- '--cell-cp %s ' "$c"; done)

# The publisher prints the session it created and the epoch of its synthetic world; keep both for the UE and for stop.
for _ in $(seq 1 60); do
    if grep -q '^session ' "$LOG_DIR/pos-publisher.log" 2>/dev/null && grep -q '^world_epoch_gps_ns=' "$LOG_DIR/pos-publisher.log"; then break; fi
    sleep 1
done
grep -q '^session ' "$LOG_DIR/pos-publisher.log" || die "the publisher did not create its session (see $LOG_DIR/pos-publisher.log)"
sed -n 's/^world_epoch_gps_ns=//p' "$LOG_DIR/pos-publisher.log" | head -1 > "$STATE_DIR/pos_world_epoch"
sed -n 's/^session \(sess-[0-9]*\) on service \(svc-[0-9]*\) active.*/\2 \1/p' "$LOG_DIR/pos-publisher.log" | head -1 > "$STATE_DIR/pos_session"
log "positioning assistance up: session $(cut -d' ' -f2 "$STATE_DIR/pos_session"); the UE reads it at $POS_DOC_URL"
log "  run the UE: ./10-run-positioning-ue.sh"
