#!/bin/bash
# Health check of every component this demo starts, plus what the radio and the client
# actually report: whether the modem is synced, whether the content PMCH is decoding, and
# whether the session this demo provisioned is still active.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh

check_tcp() {
    local name="$1" host="$2" port="$3"
    if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
        exec 3>&- 2>/dev/null
        echo "  [up]   $name ($host:$port)"
    else
        echo "  [down] $name ($host:$port)"
    fi
}

echo "Transmit side:"
pgrep -x srsepc  >/dev/null 2>&1 && echo "  [up]   EPC (srsepc)"  || echo "  [down] EPC (srsepc)"
pgrep -x srsenb  >/dev/null 2>&1 && echo "  [up]   eNB (srsenb)"  || echo "  [down] eNB (srsenb)"
check_tcp "eNB control"     127.0.0.1 "$ENB_CTRL_PORT"
check_tcp "MBMS-GW control" 127.0.0.1 "$GW_CTRL_PORT"
check_tcp "BM-SC xMB-C"     127.0.0.1 "$XMB_C_PORT"
check_tcp "portal"          "$PORTAL_HOST" "$PORTAL_PORT"
# The alert path is started separately (./08-start-alerts.sh), so "down" here is a normal
# state for a broadcast-only run rather than a fault.
if curl -s -m 2 -o /dev/null "http://$CBC_HOST:$CBC_PORT/api/health" 2>/dev/null; then
    check_tcp "CBC (alerts)"    "$CBC_HOST" "$CBC_PORT"
    _cbc_up=1
else
    echo "  [--]   CBC (alerts) not started    ./08-start-alerts.sh  (separate path, optional)"
    _cbc_up=0
fi
print_portal_credentials 2>/dev/null || true
# Only when it is actually up: a login for a service that is not running reads as though
# something is wrong with it.
[[ "$_cbc_up" == "1" ]] && { print_cbc_credentials 2>/dev/null || true; }

echo "Local origin:"
check_tcp "media server" "$MEDIA_HOST" "$MEDIA_PORT"
ext=$([[ "$LIVE_FORMAT" == "hls" ]] && echo m3u8 || echo mpd)
while IFS=$'\t' read -r ch_id ch_stream ch_source ch_type; do
    air=$(onair_rows | cut -f3 | grep -qx "$ch_stream" && echo "on air" || echo "origin only")
    segs=$(ls "$MEDIA_ROOT/$ch_stream" 2>/dev/null | grep -cE '\.(m4s|ts)$' || echo 0)
    if pgrep -f "ffmpeg .*$MEDIA_ROOT/$ch_stream" >/dev/null 2>&1 \
       && curl -s -o /dev/null -m 3 "http://$MEDIA_HOST:$MEDIA_PORT/$ch_stream/manifest.$ext"; then
        echo "  [up]   $ch_stream  ($air, $segs segments)  http://$MEDIA_HOST:$MEDIA_PORT/$ch_stream/manifest.$ext"
    else
        echo "  [down] $ch_stream  ($air)"
    fi
done < <(channel_rows)

echo "Receive side (netns $NETNS):"
if netns_exists; then echo "  [up]   netns $NETNS"; else echo "  [down] netns $NETNS"; fi
check_tcp "modem API"   "$RX_ADDR" "$MODEM_API_PORT"
check_tcp "client API"  "$RX_ADDR" "$CLIENT_API_PORT"
check_tcp "application" "$RX_ADDR" "$APP_PORT"
    # The player's Manifest URL box starts empty, so print what to paste into it.
    echo "  player stream   : $(broadcast_presentation_url)"

sib=$(curl -s -m 3 "http://$RX_ADDR:$MODEM_API_PORT/modem-api/sib_info" 2>/dev/null || true)
if [[ -n "$sib" ]]; then
    python3 - "$sib" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    print("  (modem returned no parsable SIB info)"); raise SystemExit
mcch = d.get("mcch") or {}
pmch = mcch.get("pmch_list") or []
print(f"  MCCH: {len(pmch)} PMCH(s) advertised")
for i, p in enumerate(pmch):
    tmgis = ",".join(s.get("tmgi", "?") for s in (p.get("sessions") or []))
    print(f"    PMCH{i}: mcs={p.get('data_mcs')} ti_n={p.get('time_interleaving_n')} "
          f"ti_m={p.get('time_interleaving_m')} sessions=[{tmgis}]")
PY
fi
for mch in 0 1; do
    st=$(curl -s -m 3 "http://$RX_ADDR:$MODEM_API_PORT/modem-api/mch_status/$mch" 2>/dev/null || true)
    [[ -n "$st" ]] && echo "  mch_status/$mch: $st"
done

echo "Provisioned session:"
if [[ -f "$STATE_DIR/session_id" && -f "$STATE_DIR/service_id" ]]; then
    portal_creds 2>/dev/null || true
    svc=$(cat "$STATE_DIR/service_id"); sess=$(cat "$STATE_DIR/session_id")
    echo "  service $svc / session $sess"
    portal_api GET "/api/xmb/services/$svc/sessions/$sess" 2>/dev/null \
        | python3 -m json.tool 2>/dev/null | sed 's/^/    /' || echo "    (portal not reachable)"
else
    echo "  none provisioned by this demo (run ./06-provision-live-service.sh)"
fi
