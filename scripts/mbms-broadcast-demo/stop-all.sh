#!/bin/bash
# Stops everything this demo started, in reverse dependency order: the xMB session it
# provisioned, the receive chain and its namespace, the local origin and encoder, then
# the transmit side.
#
# The receive side and the transmit side are torn down by the same two launchers that
# started them, so this does not need to know how they run.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh

kill_pidfile() {
    local logbase="$1" pidfile="$PID_DIR/$1.pid"
    [[ -f "$pidfile" ]] || return 0
    local pid; pid=$(cat "$pidfile" 2>/dev/null)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        log "stopping $logbase (pid $pid)"
        kill -TERM "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
}

# The encoder is exec'd by live-encoder.sh, so the pidfile's process is ffmpeg itself.
# Match only an ffmpeg writing into this demo's own media root, so an unrelated encode on
# the same machine is left alone.
while IFS=$'\t' read -r ch_id ch_stream ch_source ch_type; do
    kill_pidfile "live-encoder-$ch_id"
done < <(channel_rows)
for pid in $(pgrep -f "ffmpeg .*$MEDIA_ROOT" 2>/dev/null || true); do
    [[ "$pid" == "$$" ]] && continue
    log "stopping live encoder (pid $pid)"
    kill -TERM "$pid" 2>/dev/null || true
done
kill_pidfile media-server

if netns_exists; then
    log "tearing down the receive chain and netns $NETNS"
    sudo "$RECEIVE_SH" stop >/dev/null 2>&1 || true
fi

if [[ -x "$TRANSMIT_SH" ]]; then
    log "stopping the transmit side"
    "$TRANSMIT_SH" --stop >/dev/null 2>&1 || true
fi

# The ids of the session this demo created are meaningless once the BM-SC that held them
# is gone; leaving them behind makes status.sh report a session that cannot exist.
rm -f "$STATE_DIR/service_id" "$STATE_DIR/session_id"

log "stopped"
