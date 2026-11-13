#!/bin/bash
# Stops the positioning assistance publisher and its FLUTE receiver and removes the xMB session the publisher created, leaving the
# rest of the demo running.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source env.sh
source lib.sh

for name in pos-publisher pos-receiver; do
    pidfile="$PID_DIR/$name.pid"
    if [[ -f "$pidfile" ]]; then
        pid=$(cat "$pidfile" 2>/dev/null)
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            log "stopping $name (pid $pid)"
            kill -TERM "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    fi
done
# The receiver runs under sudo and runuser; signal it by exact name rather than by a pattern that would match this shell.
sudo -n pkill -x flute-receiver 2>/dev/null || true

if [[ -f "$STATE_DIR/pos_session" ]] && portal_creds 2>/dev/null; then
    read -r svc sess < "$STATE_DIR/pos_session"
    log "removing xMB session $sess of service $svc"
    portal_api DELETE "/api/xmb/services/$svc/sessions/$sess" >/dev/null 2>&1 || true
    portal_api DELETE "/api/xmb/services/$svc" >/dev/null 2>&1 || true
fi
rm -f "$STATE_DIR/pos_session" "$STATE_DIR/pos_world_epoch"
