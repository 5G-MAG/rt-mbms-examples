#!/bin/bash
# Positioning assistance, option 1: starts the document publisher (monitoring -> assistance document -> xMB-U push into its own
# Files/Push session) and the FLUTE receiver in the UE's network namespace that writes the received file.
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
[[ -x "$FLUTE_RECEIVER" ]] || die "no FLUTE receiver at $FLUTE_RECEIVER (build rt-libflute: its examples/flute-receiver; set FLUTE_RECEIVER)"
netns_exists || die "the receive side is not up (namespace $NETNS): run ./start-all.sh first"
portal_creds

# The xMB-U push is authenticated with the portal's own client certificate; read the paths and the lab-only TLS setting from the
# portal's .env so this and the portal cannot disagree.
env_value() { sed -n "s/^$1=//p" "$PORTAL_ENV" | tail -1 | tr -d '"'"'"; }
CERT="$(env_value XMB_CLIENT_CERT_PATH)"; KEY="$(env_value XMB_CLIENT_KEY_PATH)"
[[ -f "$CERT" && -f "$KEY" ]] || die "the portal's xMB client certificate ($CERT) or key ($KEY) is missing"
INSECURE=""; [[ "$(env_value XMB_INSECURE_SKIP_SERVER_VERIFY)" == "true" ]] && INSECURE="--insecure"

# Start from an empty receive directory: a document left by an earlier run belongs to a session that no longer exists, and the UE would take
# it for the current one until the first new document arrives.
rm -rf "$POS_RX_DIR"
mkdir -p "$POS_RX_DIR"
run_bg "assistance FLUTE receiver" pos-receiver \
    sudo -n ip netns exec "$NETNS" runuser -u "$(id -un)" -- \
    "$FLUTE_RECEIVER" -i "$RX_TUN_ADDR" -m "$POS_MCAST_ADDR" -p "$POS_MCAST_PORT" -T "$POS_TSI" -o "$POS_RX_DIR" -l 2

run_bg "assistance publisher" pos-publisher \
    env PYTHONPATH="$BPS_DIR" python3 -u -m bps.apps publish \
    --xmbu-url "https://127.0.0.1:$XMB_C_PORT" --cert "$CERT" --key "$KEY" $INSECURE \
    --portal-url "$PORTAL_URL" --portal-user "$PORTAL_USER" --portal-password "$PORTAL_TOKEN" \
    --tmgi-service-id "$POS_TMGI_SERVICE_ID" --tsi "$POS_TSI" --mcast "$POS_MCAST_ADDR" --port "$POS_MCAST_PORT" \
    --c-teid "$POS_C_TEID" --path "$POS_FILE_PATH" --interval "$POS_INTERVAL" --validity "$POS_VALIDITY" \
    --leap-seconds "$LEAP_SECONDS"

# The publisher prints the session it created and the epoch of its synthetic world; keep both for the UE and for stop.
for _ in $(seq 1 60); do
    if grep -q '^session ' "$LOG_DIR/pos-publisher.log" 2>/dev/null && grep -q '^world_epoch_gps_ns=' "$LOG_DIR/pos-publisher.log"; then break; fi
    sleep 1
done
grep -q '^session ' "$LOG_DIR/pos-publisher.log" || die "the publisher did not create its session (see $LOG_DIR/pos-publisher.log)"
sed -n 's/^world_epoch_gps_ns=//p' "$LOG_DIR/pos-publisher.log" | head -1 > "$STATE_DIR/pos_world_epoch"
sed -n 's/^session \(sess-[0-9]*\) on service \(svc-[0-9]*\) active.*/\2 \1/p' "$LOG_DIR/pos-publisher.log" | head -1 > "$STATE_DIR/pos_session"
log "positioning assistance up: session $(cut -d' ' -f2 "$STATE_DIR/pos_session"), documents in $POS_RX_DIR/$POS_FILE_PATH"
log "  run the UE: ./10-run-positioning-ue.sh"
