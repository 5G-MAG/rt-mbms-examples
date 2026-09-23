#!/bin/bash
# Receive side: rt-mbms-modem, rt-mbms-client and rt-mbms-application, all inside the
# network namespace receive-netns.sh creates.
#
# The namespace is not optional on a single host: the eNB's M1-U receiver and the
# client's content receiver both want UDP :2153, and the modem reaches the eNB's ZeroMQ
# transmitter over the veth pair instead of loopback. receive-netns.sh owns all of that,
# including the wait for the modem's TUN device to appear after cell search, so this
# script delegates to it and then confirms the two REST APIs answer.
#
# Needs sudo (netns creation and the modem's TUN device).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_file "$RECEIVE_SH"

log "starting the receive chain via $RECEIVE_SH"
sudo "$RECEIVE_SH" start

wait_for_http "http://$RX_ADDR:$MODEM_API_PORT/modem-api/sib_info" "$MODEM_API_WAIT_SECS" \
    || die "modem API not answering at $RX_ADDR:$MODEM_API_PORT (see $STACK_LOG_DIR/Modem.log)"
log "modem API up at http://$RX_ADDR:$MODEM_API_PORT/modem-api/"

wait_for_tcp "$RX_ADDR" "$CLIENT_API_PORT" "$MODEM_API_WAIT_SECS" \
    || die "client API not listening at $RX_ADDR:$CLIENT_API_PORT (see $STACK_LOG_DIR/Client.log)"
wait_for_tcp "$RX_ADDR" "$APP_PORT" 30 \
    || die "application not listening at $RX_ADDR:$APP_PORT (see $STACK_LOG_DIR/Application.log)"

log "receive chain up. Player UI: http://$RX_ADDR:$APP_PORT"
