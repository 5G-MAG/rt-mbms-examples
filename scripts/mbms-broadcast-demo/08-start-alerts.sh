#!/bin/bash
# Emergency alerts: starts the Cell Broadcast Centre (rt-pws-cbc) and nothing else.
#
# This is a separate entry point on purpose. A Public Warning System alert reaches handsets
# over the cell's own system information (SIB10/11/12), signalled from the MME over SBc-AP --
# not over an MBMS bearer. So it needs the transmit side up, and nothing else: no content
# origin, no encoders, no xMB session, no receiving client. Equally, the broadcast demo runs
# without this: ./start-all.sh never starts it.
#
#   ./01-start-transmit.sh     then     ./08-start-alerts.sh     then     ./07-send-alert.sh
#
# Stop it with ./stop-alerts.sh, or ./stop-all.sh which stops everything.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_cmd node
require_cmd curl

[[ -f "$CBC_DIR/server.js" ]] || die "no Cell Broadcast Centre at $CBC_DIR.
  git clone https://github.com/5G-MAG/rt-pws-cbc.git \"$CBC_DIR\" && (cd \"$CBC_DIR\" && npm install)
  then copy .env.example to .env and set AUTH_TOKEN (it refuses to start without one).
  Point CBC_DIR elsewhere if you keep it in another place."

[[ -d "$CBC_DIR/node_modules" ]] || die "$CBC_DIR has no node_modules -- run: (cd \"$CBC_DIR\" && npm install)"

# Reads the token out of the CBC's own .env, so this demo and the service cannot disagree
# about it. Fails with a clear message when that file is missing or the token is empty.
cbc_creds

if curl -s -m 3 -o /dev/null "$CBC_URL/api/health"; then
    log "Cell Broadcast Centre already up at $CBC_URL"
else
    # MME_SBC_BRIDGE_ENDPOINT is passed explicitly rather than left to the CBC's own .env: the
    # port is this demo's to choose (env.sh's SBC_BRIDGE_PORT, matching srsepc's [mme_sbc]
    # bridge_port), and a stale value in a hand-edited .env would otherwise send warnings
    # nowhere while still reporting success.
    run_bg "cell broadcast centre" cbc \
        env MME_SBC_BRIDGE_ENDPOINT="127.0.0.1:$SBC_BRIDGE_PORT" \
        node --env-file="$CBC_ENV" "$CBC_DIR/server.js"
    wait_for_http "$CBC_URL/api/health" 30 \
        || die "Cell Broadcast Centre did not answer on $CBC_URL (see $LOG_DIR/cbc.log)"
    log "Cell Broadcast Centre up at $CBC_URL"
fi

# The MME's SBc-AP bridge is what the CBC emits into. Report it rather than failing: the CBC is
# useful to start and inspect before the transmit side is up, and ./07-send-alert.sh gives the
# real error if it sends with nothing listening.
if wait_for_tcp 127.0.0.1 "$SBC_BRIDGE_PORT" 3 >/dev/null 2>&1; then
    log "MME SBc-AP bridge reachable on 127.0.0.1:$SBC_BRIDGE_PORT"
else
    log "WARNING: nothing is listening on 127.0.0.1:$SBC_BRIDGE_PORT (the MME's SBc-AP bridge)."
    log "  Start the transmit side first: ./01-start-transmit.sh"
fi

log ""
log "  alert console : $CBC_URL"
print_cbc_credentials 2>/dev/null || true
log ""
log "  ./07-send-alert.sh                      send an ETWS/CMAS alert, and wait for the modem to report it"
log "  ./07-send-alert.sh --list               the alert types this CBC accepts"
log "  ./07-send-alert.sh --cancel             stop the most recent alert"
log "  ./stop-alerts.sh                        stop the Cell Broadcast Centre"
log ""
log "  The modem has to be running for ./07-send-alert.sh to confirm arrival (./05-start-client-and-app.sh);"
log "  without it the alert is still transmitted, and the script reports that nothing observed it."
