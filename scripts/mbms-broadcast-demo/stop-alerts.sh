#!/bin/bash
# Stops the Cell Broadcast Centre and leaves the broadcast demo running.
#
# The counterpart to ./08-start-alerts.sh. ./stop-all.sh stops this too, along with everything
# else; this exists so the alert path can be taken down on its own, which is also the quickest
# way to prove the content path does not depend on it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh
ensure_dirs

pidfile="$PID_DIR/cbc.pid"
pid="$(cat "$pidfile" 2>/dev/null || true)"

if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    log "stopping the Cell Broadcast Centre (pid $pid)"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    kill -0 "$pid" 2>/dev/null && { log "  did not exit on TERM, sending KILL"; kill -KILL "$pid" 2>/dev/null || true; }
    rm -f "$pidfile"
    log "stopped"
else
    rm -f "$pidfile"
    log "no Cell Broadcast Centre was running (nothing to stop)"
fi
