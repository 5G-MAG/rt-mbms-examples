#!/bin/bash
# Sends an ETWS/CMAS emergency alert through the Cell Broadcast Centre (rt-pws-cbc) and
# waits until the receiving modem reports it, so the script says whether the alert actually
# arrived over the air rather than only that the CBC accepted it.
#
# The alert path is independent of the content path: it runs over the cell's own warning
# signalling (SIB10/11/12 via the MME's SBc-AP bridge), not over the MBMS bearer, so it
# works whether or not a content session is active, and it does not involve the media
# provisioning portal at all.
#
# Needs the alert path started first: ./08-start-alerts.sh. That is separate from the broadcast
# demo on purpose -- a warning travels over the cell's own system information, not over an MBMS
# bearer, so neither path needs the other.
#
#   ./07-send-alert.sh [alert-type] [headline] [description]
#   ./07-send-alert.sh --cancel                  send a Stop Warning for the active alert
#   ./07-send-alert.sh --list                    print the alert types the CBC accepts
#
# Alert types come from rt-pws-cbc/lib/cap.js, and --list asks the running CBC for them.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_cmd curl
require_cmd python3
cbc_creds

# The alert path has its own entry point, so the CBC may legitimately not be running even with
# the whole broadcast demo up. Say which script starts it rather than failing on the POST below.
if ! curl -s -m 3 -o /dev/null "$CBC_URL/api/health"; then
    die "no Cell Broadcast Centre answering on $CBC_URL. Start the alert path first:
  ./08-start-alerts.sh
  (it needs the transmit side up: ./01-start-transmit.sh)"
fi

# Kept in step with rt-pws-cbc/lib/cap.js. --list prefers the running service's own answer
# (GET /api/alert-types) and falls back to this copy when the CBC is not up yet.
ALERT_TYPES=(etws_earthquake etws_tsunami etws_earthquake_tsunami etws_test etws_other
             cmas_presidential cmas_extreme cmas_severe cmas_amber)

if [[ "${1:-}" == "--list" ]]; then
    live=$(cbc_api GET "/api/alert-types" 2>/dev/null \
           | python3 -c 'import json,sys
try:
    print("\n".join(json.load(sys.stdin)["alertTypes"]))
except Exception:
    pass' 2>/dev/null)
    if [[ -n "$live" ]]; then printf '%s\n' "$live"; else printf '%s\n' "${ALERT_TYPES[@]}"; fi
    exit 0
fi

if [[ "${1:-}" == "--cancel" ]]; then
    log "sending Stop Warning (cancel)"
    resp=$(cbc_api POST "/api/alerts/cancel" '{}')
    grep -q '"ok"[[:space:]]*:[[:space:]]*true' <<<"$resp" || die "cancel failed: ${resp:-no response}"
    log "cancel accepted by the CBC"
    exit 0
fi

ALERT_TYPE="${1:-etws_test}"
HEADLINE="${2:-MBMS demo test alert}"
DESCRIPTION="${3:-Test warning message issued by the MBMS broadcast demo. No action required.}"

printf '%s\n' "${ALERT_TYPES[@]}" | grep -qx "$ALERT_TYPE" \
    || die "unknown alert type '$ALERT_TYPE'. Run ./07-send-alert.sh --list"

body=$(python3 - "$ALERT_TYPE" "$HEADLINE" "$DESCRIPTION" <<'PY'
import json, sys
print(json.dumps({"alertType": sys.argv[1], "headline": sys.argv[2], "description": sys.argv[3]}))
PY
)

log "sending $ALERT_TYPE via $CBC_URL"
resp=$(cbc_api POST "/api/alerts" "$body")
grep -q '"ok"[[:space:]]*:[[:space:]]*true' <<<"$resp" \
    || die "the CBC rejected the alert: ${resp:-no response}"
log "CBC accepted the alert"

# Which of the modem's three warning lists the alert lands in depends on its type: ETWS
# primary carries the warning type only, ETWS secondary the message, and PWS/CMAS its own.
case "$ALERT_TYPE" in
    etws_*) endpoints=(etws_primary_alerts etws_secondary_alerts) ;;
    cmas_*) endpoints=(pws_alerts) ;;
esac

log "waiting for the modem to report it (SIB warning signalling can take ~10-30s)"
deadline=$((SECONDS + 90))
found=""
while (( SECONDS < deadline )); do
    for ep in "${endpoints[@]}"; do
        n=$(curl -s -m 3 "http://$RX_ADDR:$MODEM_API_PORT/modem-api/$ep" 2>/dev/null \
            | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print(0); raise SystemExit
if isinstance(d,list): print(len(d))
elif isinstance(d,dict):
    for v in d.values():
        if isinstance(v,list): print(len(v)); raise SystemExit
    print(0)
else: print(0)' 2>/dev/null || echo 0)
        if [[ "${n:-0}" -gt 0 ]]; then found="$ep ($n)"; break; fi
    done
    [[ -n "$found" ]] && break
    sleep 3
done

if [[ -n "$found" ]]; then
    log "alert received by the modem: $found"
    log "  see it in the application UI: http://$RX_ADDR:$APP_PORT/cellbroadcast"
else
    log "WARNING: the CBC accepted the alert but the modem reported none within 90s."
    log "  Check $STACK_LOG_DIR/Modem.log and $LOG_DIR/EPC.log (the SBc-AP bridge is on :$SBC_BRIDGE_PORT),"
    log "  and confirm the modem is still synced: curl -s http://$RX_ADDR:$MODEM_API_PORT/modem-api/sib_info"
    exit 1
fi
