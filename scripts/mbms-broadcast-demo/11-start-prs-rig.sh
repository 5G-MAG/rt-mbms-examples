#!/bin/bash
# Positioning with several cells: starts the mixer that sits between the transmitter and the modem and adds emulated neighbouring cells to the transmitter's IQ.
# start-all.sh runs this before the transmit side when POS_RIG=1 (and gives the transmitter PRS and the moved output port, see rig_enb_conf in lib.sh).
#
#   POS_RIG=1 ./start-all.sh     then     ./09-start-positioning.sh     then     POS_MEASURE=modem ./10-run-positioning-ue.sh
#
# The mixer reads the publisher's world epoch from the state file 09-start-positioning.sh writes, so the emulated cells' relative transmit delays drift the way the
# broadcaster's monitor sees them. Stopped by stop-all.sh.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

require_cmd python3
[[ -d "$BPS_DIR/bps" ]] || die "no positioning code at $BPS_DIR (set BPS_DIR)"
python3 -c "import numpy, zmq" 2>/dev/null || die "the rig needs python3 with numpy and pyzmq"

rm -f "$STATE_DIR/pos_rig_snr"   # echo 12 > it, or `off`, to change the noise of a running mixer
run_bg "positioning rig (mixer)" pos-rig \
    env PYTHONPATH="$BPS_DIR" python3 -u -m bps.apps rig \
    --input "tcp://127.0.0.1:$POS_RIG_TX_PORT" --output "tcp://*:$POS_RIG_OUT_PORT" --power-db "$POS_RIG_POWER_DB" \
    --world-epoch-file "$STATE_DIR/pos_world_epoch" --leap-seconds "$LEAP_SECONDS" --cp "$(rig_cp)" ${POS_RIG_FREEZE:+--freeze} \
    ${POS_RIG_SNR_DB:+--snr-db "$POS_RIG_SNR_DB"} --snr-file "$STATE_DIR/pos_rig_snr" $([[ "$POS_RIG_PRS" == false ]] && echo --no-real-prs) \
    $(for c in $POS_RIG_CELL_CP; do printf -- '--cell-cp %s ' "$c"; done) $(for c in $POS_CELL_CFO; do printf -- '--cell-cfo %s ' "$c"; done) $(for c in $POS_CELL_POWER; do printf -- '--cell-power %s ' "$c"; done)
log "mixer up: transmitter on :$POS_RIG_TX_PORT in, modem side on :$POS_RIG_OUT_PORT out, emulated cells 2 3 4 at $POS_RIG_POWER_DB dB, $(rig_cp) cyclic prefix"
