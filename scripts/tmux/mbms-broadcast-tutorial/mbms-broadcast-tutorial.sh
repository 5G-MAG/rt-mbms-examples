#!/usr/bin/env bash
#
# mbms-broadcast-tutorial.sh
#
# Launches a full LTE-based 5G Terrestrial Broadcast (FeMBMS / MBMS) reference
# deployment in a single tmux session, one window per function, with per-window
# logging. Modelled on 5G-MAG/rt-mbs-examples' mbs-function-tutorial.sh, adapted
# to the LTE-based 5G Broadcast reference-tools stack:
#
#   Transmit side:  srsepc (EPC/MME) -> srsenb (eNB) -> mbms-gw -> bmsc (BM-SC)
#   Receive side:   modem -> client -> application (web UI)
#   Control:        application-provider (portal)
#
# The default configuration reproduces the software-radio (ZeroMQ) end-to-end
# setup, so no SDR hardware is required. Edit the CONFIG block below (or export
# the variables before running) to point at your own checkouts / config files.
#
# Usage:   ./mbms-broadcast-tutorial.sh            # launch + attach
#          ./mbms-broadcast-tutorial.sh --kill     # tear the session down
#
set -u

# =============================================================================
# CONFIG  -- adjust to your environment (or override via the environment)
# =============================================================================
SESSION="${SESSION:-mbms-broadcast}"

# Repository checkouts (where each component was built).
TX_DIR="${TX_DIR:-$HOME/rt-mbms-tx}"                       # srsepc + srsenb
GW_DIR="${GW_DIR:-$HOME/rt-mbms-gw}"                       # mbms-gw
BMSC_DIR="${BMSC_DIR:-$HOME/rt-mbms-bmsc}"                 # bmsc (BM-SC)
MODEM_DIR="${MODEM_DIR:-$HOME/rt-mbms-modem}"             # modem
CLIENT_DIR="${CLIENT_DIR:-$HOME/rt-mbms-client}"          # rt-mbms-client
APP_DIR="${APP_DIR:-$HOME/rt-mbms-application}"           # web UI (formerly rt-wui)
PORTAL_DIR="${PORTAL_DIR:-$HOME/rt-mbms-application-provider}"

# Built binaries (derived from the checkouts above).
SRSEPC="${SRSEPC:-$TX_DIR/build/srsepc/src/srsepc}"
SRSENB="${SRSENB:-$TX_DIR/build/srsenb/src/srsenb}"
MBMSGW="${MBMSGW:-$GW_DIR/build/mbms-gw/mbms-gw}"
BMSC="${BMSC:-$BMSC_DIR/build/bmsc/bmsc}"
MODEM="${MODEM:-$MODEM_DIR/build/modem}"
CLIENT_BIN="${CLIENT_BIN:-$CLIENT_DIR/build/client}"

# Config directory. Put every *.conf (and the SIB/RR/RB files the eNB config
# references, plus user_db.csv) here. Defaults to ./conf next to this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${CONF:-$SCRIPT_DIR/conf}"

# SoapySDR plugin dir holding the "zmqrx" bridge (libzmqrxSupport.so) the modem's
# ZeroMQ RX uses. This bridge is NOT shipped with the tutorial -- you build it
# yourself (see README "ZeroMQ software radio"). Point this at the directory
# holding your built libzmqrxSupport.so, or export SOAPY_SDR_PLUGIN_PATH.
# Ignored for a real SDR.
SOAPY_ZMQ_DIR="${SOAPY_SDR_PLUGIN_PATH:-$HOME/soapy-zmq-bridge}"

# Config filenames (looked up inside $CONF; the transmit/receive C++ components
# are launched with $CONF as their working directory so relative includes such
# as the eNB's [enb_files] SIB/RR/RB paths resolve).
EPC_CONF="${EPC_CONF:-epc.conf}"
ENB_CONF="${ENB_CONF:-enb_baseline.conf}"
GW_CONF="${GW_CONF:-mbms-gw.conf}"
BMSC_CONF="${BMSC_CONF:-bmsc.conf}"
MODEM_CONF="${MODEM_CONF:-modem_zmqtest.conf}"
CLIENT_CONF="${CLIENT_CONF:-client_recv.conf}"
CLIENT_IFACE="${CLIENT_IFACE:-192.168.180.10}"   # local iface IP the client binds for the MBMS user plane

# Logs (one file per window).
LOG_DIR="${LOG_DIR:-$HOME/.local/state/mbms-broadcast-tutorial}"

# Seconds to wait after a dependency before starting the next stage.
STAGE_PAUSE="${STAGE_PAUSE:-2}"

# Which components run under sudo (set to "sudo" to enable, empty to disable).
# srsepc needs root: its SP-GW brings up a TUN interface and edits routing.
# The others need root ONLY for specific modes, so they default to no sudo:
#   SUDO_ENB / SUDO_MODEM  -> set to "sudo" for a real SDR (realtime / USB access)
#   SUDO_GW                -> set to "sudo" if you enable the mbms-gw sgi_mb TUN
SUDO_EPC="${SUDO_EPC:-sudo}"
SUDO_ENB="${SUDO_ENB:-}"
SUDO_GW="${SUDO_GW:-}"
SUDO_MODEM="${SUDO_MODEM:-}"

# =============================================================================
# Components:  "Window|WorkingDir|Command|pause-after-seconds|sudo"
# Launched top-to-bottom; dependency order matters (EPC before eNB; modem
# before client; modem+client before the web UI). The 5th field, when set to
# "sudo", runs that component's command under sudo.
# =============================================================================
COMPONENTS=(
  "EPC|$CONF|$SRSEPC $EPC_CONF|$STAGE_PAUSE|$SUDO_EPC"
  "eNB|$CONF|$SRSENB $ENB_CONF|1|$SUDO_ENB"
  "MBMS-GW|$CONF|$MBMSGW $GW_CONF|1|$SUDO_GW"
  "BM-SC|$CONF|$BMSC $BMSC_CONF|1|"
  "Modem|$CONF|env SOAPY_SDR_PLUGIN_PATH=$SOAPY_ZMQ_DIR $MODEM -c $MODEM_CONF -b 10 -l 2 -s 4|$STAGE_PAUSE|$SUDO_MODEM"
  "Client|$CONF|$CLIENT_BIN -c $CLIENT_CONF -i $CLIENT_IFACE -l 2|1|"
  "Application|$APP_DIR|node app.js|1|"
  "Portal|$PORTAL_DIR|node --env-file=.env server.js|0|"
)

# =============================================================================
# Helpers
# =============================================================================
die() { echo "ERROR: $*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH${2:+ ($2)}"; }

require_exec() { [ -x "$1" ] || die "not an executable: $1  (build it, or set the matching path variable)"; }

# Wrap a command so the window: line-buffers, tees to a log, and stays open
# (dropping to a shell) if the process exits, so you can read the error.
wrap_cmd() {
  local name="$1"; shift
  local log="$LOG_DIR/${name}.log"
  printf 'echo "[%s] $ %s"; stdbuf -oL -eL %s 2>&1 | tee -a %q; echo; echo "[%s] exited ($?). Log: %s"; exec bash' \
    "$name" "$*" "$*" "$log" "$name" "$log"
}

cleanup_hint() {
  cat <<EOF

Session '$SESSION' is running. Useful keys/commands:
  detach ................ Ctrl-b d
  next / prev window .... Ctrl-b n  /  Ctrl-b p
  pick a window ......... Ctrl-b w
  re-attach ............. tmux attach -t $SESSION
  tear everything down .. $0 --kill   (or: tmux kill-session -t $SESSION)
  logs .................. $LOG_DIR/<Window>.log
EOF
}

# =============================================================================
# --kill
# =============================================================================
if [ "${1:-}" = "--kill" ] || [ "${1:-}" = "-k" ]; then
  tmux kill-session -t "$SESSION" 2>/dev/null && echo "Killed tmux session '$SESSION'." \
    || echo "No tmux session '$SESSION'."
  # srsepc runs as root, so kill-session (non-root tmux) cannot stop it -- do it here.
  if command -v pgrep >/dev/null 2>&1 && pgrep -x srsepc >/dev/null 2>&1; then
    echo "Stopping srsepc (EPC/MME, runs as root)..."
    sudo pkill -x srsepc 2>/dev/null || echo "  could not stop srsepc; run: sudo pkill -x srsepc"
  fi
  exit 0
fi

# =============================================================================
# Pre-flight
# =============================================================================
require_cmd tmux "sudo apt install tmux"
require_cmd node "the Application and Portal are Node.js apps"
require_cmd stdbuf "coreutils"
[ -d "$CONF" ] || die "config dir not found: $CONF  (set CONF=... or create ./conf)"

require_exec "$SRSEPC"; require_exec "$SRSENB"; require_exec "$MBMSGW"
require_exec "$BMSC";   require_exec "$MODEM";  require_exec "$CLIENT_BIN"
[ -f "$APP_DIR/app.js" ]       || die "not found: $APP_DIR/app.js"
[ -f "$PORTAL_DIR/server.js" ] || die "not found: $PORTAL_DIR/server.js"
# The portal refuses to start without an auth secret.
[ -f "$PORTAL_DIR/.env" ]      || echo "WARNING: $PORTAL_DIR/.env not found -- the portal needs AUTH_TOKEN set (see its .env.example)."
# The modem's ZeroMQ RX needs a user-built SoapySDR "zmqrx" bridge (not shipped).
[ -f "$SOAPY_ZMQ_DIR/libzmqrxSupport.so" ] || echo "WARNING: no libzmqrxSupport.so in '$SOAPY_ZMQ_DIR' -- the modem's ZeroMQ RX needs the SoapySDR 'zmqrx' bridge, which you build yourself (see README 'ZeroMQ software radio'). Set SOAPY_ZMQ_DIR/SOAPY_SDR_PLUGIN_PATH to its location. Not needed for a real SDR."

mkdir -p "$LOG_DIR"
tmux kill-session -t "$SESSION" 2>/dev/null   # start clean

# If any component runs under sudo, authenticate once up front (as the reference
# tutorial does), then keep the sudo timestamp warm while the windows launch.
# NOTE: we deliberately do NOT store the password anywhere; we just prime sudo's
# own cached credential. Depending on your sudoers `tty_tickets` setting, a sudo
# window may still prompt once in its own pane -- enter the same password there.
NEED_SUDO=0
printf '%s\n' "${COMPONENTS[@]}" | grep -q '|sudo$' && NEED_SUDO=1
SUDO_KEEPALIVE_PID=""
cleanup() { [ -n "${SUDO_KEEPALIVE_PID:-}" ] && kill "$SUDO_KEEPALIVE_PID" 2>/dev/null; }
trap cleanup EXIT
if [ "$NEED_SUDO" = 1 ]; then
  require_cmd sudo
  echo "Some functions need root -- authenticating with sudo once..."
  sudo -n true 2>/dev/null || sudo -v || die "sudo authentication failed"
  ( while true; do sudo -n true 2>/dev/null || exit; sleep 50; done ) &   # keep-alive
  SUDO_KEEPALIVE_PID=$!
fi

# --- Preflight guard 1: clear a leftover EPC/MME ----------------------------
# srsepc runs as root, so the 'tmux kill-session' above cannot stop it (a
# non-root tmux can't signal root). A leftover instance is why a re-run hits
# 'bind(): Address already in use' on the S1-MME socket. Stop it here.
if command -v pgrep >/dev/null 2>&1 && pgrep -x srsepc >/dev/null 2>&1; then
  echo "A previous srsepc (EPC/MME) is still running -- stopping it so the S1-MME socket is free..."
  if [ "$NEED_SUDO" = 1 ]; then sudo pkill -x srsepc 2>/dev/null || true; else pkill -x srsepc 2>/dev/null || true; fi
  for _ in 1 2 3 4 5; do pgrep -x srsepc >/dev/null 2>&1 || break; sleep 1; done
  pgrep -x srsepc >/dev/null 2>&1 && echo "  WARNING: srsepc still running -- stop it manually: sudo pkill -x srsepc"
fi

# --- Preflight guard 2: warn on already-bound stack ports -------------------
# The tmux components were just cleared by kill-session; any port still bound is
# a leftover from a manual launch (or another deployment) and will collide.
for _p in 2100 2101 3000 3010 3020 8080 8543; do
  ss -ltn 2>/dev/null | grep -q ":${_p} " && \
    echo "NOTE: port ${_p} is already in use -- a leftover component may conflict with this launch."
done

# =============================================================================
# Launch: first component in a new session, the rest as new windows
# =============================================================================
first=1
for entry in "${COMPONENTS[@]}"; do
  IFS='|' read -r name workdir cmd pause usesudo <<<"$entry"
  [ "$usesudo" = "sudo" ] && cmd="sudo $cmd"
  echo "Starting $name ..."
  if [ "$first" = 1 ]; then
    tmux new-session -d -s "$SESSION" -n "$name" -c "$workdir"
    first=0
  else
    tmux new-window -t "$SESSION" -n "$name" -c "$workdir"
  fi
  tmux send-keys -t "$SESSION:$name" "$(wrap_cmd "$name" "$cmd")" C-m
  sleep "${pause:-1}"
done

tmux has-session -t "$SESSION" 2>/dev/null || die "tmux session failed to start"
tmux select-window -t "$SESSION:EPC"

cleanup_hint

# Attach unless we are already inside tmux (then just report).
if [ -z "${TMUX:-}" ]; then
  tmux attach -t "$SESSION"
else
  echo "(already inside tmux) -> switch with: tmux switch-client -t $SESSION"
fi
