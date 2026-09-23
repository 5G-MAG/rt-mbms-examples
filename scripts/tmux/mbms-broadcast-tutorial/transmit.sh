#!/usr/bin/env bash
#
# transmit.sh -- bring up the transmit side of the LTE-based 5G Broadcast
# stack (EPC/eNB/MBMS-GW/BM-SC/Portal) in the background, one log file per
# component. Pair with receive-netns.sh, which runs modem/client/application
# in a network namespace so their UDP :2153 doesn't collide with the eNB's
# M1-U receiver on the same host.
#
#   ./transmit.sh            launch the transmit side (background) and report
#   ./transmit.sh --stop     stop everything this script started
#
# Config is shared with the tmux tutorial (same ./conf, same defaults); override
# any variable via the environment.
#
set -u

# =============================================================================
# CONFIG  (mirrors mbms-broadcast-tutorial.sh)
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${CONF:-$SCRIPT_DIR/conf}"

TX_DIR="${TX_DIR:-$HOME/rt-mbms-tx}"
GW_DIR="${GW_DIR:-$HOME/rt-mbms-gw}"
BMSC_DIR="${BMSC_DIR:-$HOME/rt-mbms-bmsc}"
PORTAL_DIR="${PORTAL_DIR:-$HOME/rt-mbms-application-provider}"

SRSEPC="${SRSEPC:-$TX_DIR/build/srsepc/src/srsepc}"
SRSENB="${SRSENB:-$TX_DIR/build/srsenb/src/srsenb}"
MBMSGW="${MBMSGW:-$GW_DIR/build/mbms-gw/mbms-gw}"
BMSC="${BMSC:-$BMSC_DIR/build/bmsc/bmsc}"

EPC_CONF="${EPC_CONF:-epc.conf}"
ENB_CONF="${ENB_CONF:-enb_baseline.conf}"
GW_CONF="${GW_CONF:-mbms-gw.conf}"
BMSC_CONF="${BMSC_CONF:-bmsc.conf}"

LOG_DIR="${LOG_DIR:-$HOME/.local/state/mbms-broadcast-tutorial}"
PID_FILE="$LOG_DIR/transmit.pids"
STAGE_PAUSE="${STAGE_PAUSE:-2}"

# Which components run under sudo (see the tmux tutorial for the rationale).
SUDO_EPC="${SUDO_EPC:-sudo}"; SUDO_ENB="${SUDO_ENB:-}"; SUDO_GW="${SUDO_GW:-}"

# NOTE on RF frequency: the eNB (network side) and the TV Service Configuration
# MO (receiver-side provisioning, in the modem's tv_config) are configured
# INDEPENDENTLY and MANUALLY -- the eNB does NOT read the MO. They must simply
# carry the SAME EARFCN by default so transmit and receive agree: keep
# enb_baseline.conf's dl_earfcn and modem_zmqtest.conf's tv_config ran_info in
# sync when you change the cell frequency (in receive-netns.sh's conf).

# "Name|WorkingDir|Command|pause|sudo"
COMPONENTS=(
  "EPC|$CONF|$SRSEPC $EPC_CONF|$STAGE_PAUSE|$SUDO_EPC"
  "eNB|$CONF|$SRSENB $ENB_CONF|1|$SUDO_ENB"
  "MBMS-GW|$CONF|$MBMSGW $GW_CONF|1|$SUDO_GW"
  "BM-SC|$CONF|$BMSC $BMSC_CONF|1|"
  "Portal|$PORTAL_DIR|node --env-file=.env server.js|0|"
)

die() { echo "ERROR: $*" >&2; exit 1; }
require_exec() { [ -x "$1" ] || die "not executable: $1 (build it, or set its path variable)"; }

# Full teardown of the transmit-side stack on this host, by process name -- so
# it clears leftovers regardless of how they were started (manual runs, a
# previous launch). Single-deployment demo host only; do NOT use where a
# separate live srsenb/EPC must keep running. Does not touch the receive side
# (modem/client/application) -- tear that down separately with
# `sudo ./receive-netns.sh stop`, which deletes the whole netns.
stop_stack() {
  # SIGTERM everything (srsepc is root -> sudo).
  for b in srsenb mbms-gw bmsc; do
    pkill -x "$b" 2>/dev/null && echo "  $b"
  done
  pkill -f 'node --env-file=.env server.js' 2>/dev/null && echo "  portal (server.js)"
  pgrep -x srsepc >/dev/null 2>&1 && { sudo pkill -x srsepc 2>/dev/null && echo "  srsepc (root)" || echo "  srsepc: run 'sudo pkill -x srsepc'"; }
  # Wait for them to die; escalate to SIGKILL at 4s (some catch SIGTERM).
  for i in 1 2 3 4 5 6 7 8; do
    left=""; for b in srsepc srsenb mbms-gw bmsc; do pgrep -x "$b" >/dev/null 2>&1 && left=1; done
    [ -z "$left" ] && break
    if [ "$i" = 4 ]; then
      for b in srsenb mbms-gw bmsc; do pkill -9 -x "$b" 2>/dev/null; done
      pgrep -x srsepc >/dev/null 2>&1 && sudo pkill -9 -x srsepc 2>/dev/null
    fi
    sleep 1
  done
  # SIGKILL can leave the S1-MME SCTP socket lingering a moment -- wait it out,
  # else the next srsepc hits "Error binding SCTP socket".
  for i in 1 2 3 4 5 6; do grep -q 36412 /proc/net/sctp/eps 2>/dev/null || break; sleep 1; done
  rm -f "$PID_FILE" 2>/dev/null || true
}

# =============================================================================
# --stop : tear down everything this script started
# =============================================================================
if [ "${1:-}" = "--stop" ] || [ "${1:-}" = "-k" ]; then
  echo "Stopping the transmit-side stack..."
  stop_stack
  echo "Done. (Receive side, if running, is separate: sudo ./receive-netns.sh stop)"
  exit 0
fi

# =============================================================================
# Pre-flight
# =============================================================================
command -v node >/dev/null 2>&1 || die "'node' not found (Portal is Node.js)"
[ -d "$CONF" ] || die "config dir not found: $CONF"
require_exec "$SRSEPC"; require_exec "$SRSENB"; require_exec "$MBMSGW"; require_exec "$BMSC"
[ -f "$PORTAL_DIR/server.js" ] || die "not found: $PORTAL_DIR/server.js"
[ -f "$PORTAL_DIR/.env" ]      || echo "WARNING: $PORTAL_DIR/.env missing -- portal needs AUTH_TOKEN."

mkdir -p "$LOG_DIR"

# sudo once up front + keep-alive (never stores the password).
NEED_SUDO=0; printf '%s\n' "${COMPONENTS[@]}" | grep -q '|sudo$' && NEED_SUDO=1
SUDO_KEEPALIVE_PID=""
cleanup() { [ -n "${SUDO_KEEPALIVE_PID:-}" ] && kill "$SUDO_KEEPALIVE_PID" 2>/dev/null; }
trap cleanup EXIT
if [ "$NEED_SUDO" = 1 ]; then
  command -v sudo >/dev/null 2>&1 || die "'sudo' not found (EPC needs root)"
  echo "Some functions need root -- authenticating with sudo once..."
  sudo -n true 2>/dev/null || sudo -v || die "sudo authentication failed"
  ( while true; do sudo -n true 2>/dev/null || exit; sleep 50; done ) & SUDO_KEEPALIVE_PID=$!
fi

# Clean start: fully tear down any existing transmit-side stack first
# (leftovers, a previous run, or manually-started components) so nothing
# collides on the sockets. sudo is primed above, so the root srsepc is
# stopped too. This makes the launcher idempotent -- safe to run repeatedly
# for a demo.
echo "Clearing any existing transmit-side stack for a clean start..."
stop_stack
sleep 2   # let sockets (especially the SCTP S1-MME socket) release before relaunch
# Sanity: warn if a stack port is somehow still bound after the clean.
for _p in 2100 2101 2102 8080 8543; do
  ss -ltn 2>/dev/null | grep -q ":${_p} " && echo "NOTE: port ${_p} still in use after cleanup -- check for a process outside this stack."
done

# =============================================================================
# Launch: each component backgrounded (nohup), one log file per component
# =============================================================================
: > "$PID_FILE"
for entry in "${COMPONENTS[@]}"; do
  IFS='|' read -r name workdir cmd pause usesudo <<<"$entry"
  [ "$usesudo" = "sudo" ] && cmd="sudo $cmd"
  log="$LOG_DIR/${name}.log"
  echo "Starting $name  (log: $log)"
  ( cd "$workdir" && exec nohup $cmd >"$log" 2>&1 ) &
  echo "$name $!" >> "$PID_FILE"
  sleep "${pause:-1}"
done

echo
echo "Transmit side launched in the background. Logs: $LOG_DIR/<Name>.log"
echo "Listening ports:"; ss -ltn 2>/dev/null | grep -oE ':(2100|2101|2102|8080|8543)\b' | sort -u | sed 's/^/  /'
echo "Bring up the receive side with:  sudo ./receive-netns.sh start"
echo "Stop the transmit side with:     $0 --stop"
