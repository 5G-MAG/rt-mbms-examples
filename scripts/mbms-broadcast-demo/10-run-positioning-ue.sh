#!/bin/bash
# Positioning assistance, option 1: the UE side. Reads the assistance document from rt-mbms-client, which received it from the service
# announcement and the FLUTE session, and the SIB16 anchor from the modem's REST API, computes the network time of each measurement, and prints a position fix against the known position of the
# synthetic UE. POS_MEASURE=prs or sync takes the time differences from simulated PRS waveforms, or from the PSS and SSS, instead of the geometry; POS_MEASURE=modem (with POS_RIG=1) from the modem's own measurement of the PRS.   ./10-run-positioning-ue.sh [seconds]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
require_cmd python3
[[ -f "$STATE_DIR/pos_world_epoch" ]] || die "the publisher is not running: ./09-start-positioning.sh first"
env PYTHONPATH="$BPS_DIR" python3 -u -m bps.apps ue \
    --doc-url "$POS_DOC_URL" \
    --modem-url "http://$RX_ADDR:$MODEM_API_PORT/modem-api/sib_info" \
    --world-epoch-gps-ns "$(cat "$STATE_DIR/pos_world_epoch")" \
    --measure "$POS_MEASURE" ${POS_SNR_DB:+--snr-db "$POS_SNR_DB"} \
    --leap-seconds "$LEAP_SECONDS" --seconds "${1:-60}"
