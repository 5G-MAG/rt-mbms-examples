#!/bin/bash
# Transmit side: EPC (srsepc), eNB (srsenb), MBMS-GW, BM-SC, the portal
# (rt-mbms-application-provider) and the Cell Broadcast Centre (rt-pws-cbc).
#
# This delegates to the tutorial's own transmit.sh rather than starting the five
# components itself: that script owns the start order, the single sudo authentication
# srsepc needs, the clearing of a leftover root srsepc and the per-component log files.
# A second implementation here would be a second thing to keep in step with the configs.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_file "$TRANSMIT_SH"
require_cmd curl

log "starting the transmit side via $TRANSMIT_SH"
"$TRANSMIT_SH"

# transmit.sh backgrounds each component and returns; confirm the ones this demo goes on
# to drive are actually listening before the next step depends on them.
wait_for_tcp 127.0.0.1 "$ENB_CTRL_PORT" 30 || die "eNB control port $ENB_CTRL_PORT never opened (see $LOG_DIR/eNB.log)"
wait_for_tcp 127.0.0.1 "$GW_CTRL_PORT"  30 || die "MBMS-GW control port $GW_CTRL_PORT never opened (see $LOG_DIR/MBMS-GW.log)"
wait_for_tcp 127.0.0.1 "$XMB_C_PORT"    30 || die "BM-SC xMB-C port $XMB_C_PORT never opened (see $LOG_DIR/BM-SC.log)"

portal_creds
wait_for_http "$PORTAL_URL/api/health" 30 || die "portal not answering on $PORTAL_URL (see $LOG_DIR/Portal.log)"

# The Cell Broadcast Centre is started here rather than by the tutorial's transmit.sh because it
# is not part of that tutorial's five-component transmit chain: it emits Public Warning System
# alerts over the MME's SBc-AP bridge, which srsepc above has already opened. Skipped without a
# complaint when the repository is not checked out, so a reader who only wants the content path
# is not forced to clone it.
if [[ -f "$CBC_DIR/server.js" ]]; then
    cbc_creds
    run_bg "cell broadcast centre" cbc \
        env MME_SBC_BRIDGE_ENDPOINT="127.0.0.1:$SBC_BRIDGE_PORT" \
        node --env-file="$CBC_ENV" "$CBC_DIR/server.js"
    wait_for_http "$CBC_URL/api/health" 30 || die "Cell Broadcast Centre not answering on $CBC_URL (see $LOG_DIR/cbc.log)"
    log "transmit side up (eNB :$ENB_CTRL_PORT, MBMS-GW :$GW_CTRL_PORT, BM-SC :$XMB_C_PORT, portal $PORTAL_URL, CBC $CBC_URL)"
else
    log "transmit side up (eNB :$ENB_CTRL_PORT, MBMS-GW :$GW_CTRL_PORT, BM-SC :$XMB_C_PORT, portal $PORTAL_URL)"
    log "  no Cell Broadcast Centre at $CBC_DIR -- ./07-send-alert.sh needs it (git clone rt-pws-cbc)"
fi
