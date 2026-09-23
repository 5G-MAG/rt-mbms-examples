#!/bin/bash
# Transmit side of the broadcast path: EPC (srsepc), eNB (srsenb), MBMS-GW, BM-SC and the
# portal (rt-mbms-application-provider).
#
# Emergency alerts are NOT started here. They are a separate path with its own entry point,
# ./08-start-alerts.sh, because they have nothing to do with delivering content: a warning
# travels over the cell's own system information, not over an MBMS bearer, and needs no
# content session. Either path runs without the other.
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

log "transmit side up (eNB :$ENB_CTRL_PORT, MBMS-GW :$GW_CTRL_PORT, BM-SC :$XMB_C_PORT, portal $PORTAL_URL)"
