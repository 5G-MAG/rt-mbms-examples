#!/usr/bin/env bash
#
# run-rtve-demo.sh -- bring up the WHOLE 5G Broadcast / MBMS demo stack from
# scratch (transmit chain, receive chain, both web UIs) and load + activate
# the RTVE live demo content.
#
# Covers, in order:
#   1. Stop any previous run (idempotent -- safe to re-run any time)
#   2. Transmit side: EPC, eNB, MBMS-GW, BM-SC
#   3. Application Provider (xMB control portal, http://127.0.0.1:8080)
#   4. Receive side: modem, client, Application/WUI (http://<rx-ip>:3000),
#      inside the mbms-rx network namespace
#   5. Load the rtve-24h.json demo template and activate its xMB session
#
# The BM-SC pulls RTVE's HLS manifest/segments directly over HTTPS (RTVE's
# CDN requires TLS 1.3 + HTTP/2) -- this only works if libcurl's active TLS
# backend actually supports that handshake. It does on this host (libcurl is
# OpenSSL-backed). If your host's libcurl is GnuTLS-backed instead, that
# combination is known to fail this specific CDN's handshake ("received
# handshake message out of context"); demo-content/hls-http-proxy.js exists
# as a fallback for exactly that case -- start it and point rtve-24h.json's
# applicationEntryPointURL at it instead (see the comment in that file).
#
# This host's sandbox kills any process that requests SCHED_RR/FIFO realtime
# scheduling, and only allows non-interactive `sudo -n` (no `sudo -v`) -- both
# worked around below. If you run this on a normal host, these workarounds are
# harmless no-ops.
#
#   ./run-rtve-demo.sh
#
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$SCRIPT_DIR/conf"
DEMO_DIR="$SCRIPT_DIR/demo-content"
LOG_DIR="$HOME/.local/state/mbms-broadcast-tutorial"

TX_DIR="$HOME/rt-mbms-tx"
GW_DIR="$HOME/rt-mbms-gw"
BMSC_DIR="$HOME/rt-mbms-bmsc"
PORTAL_DIR="$HOME/rt-mbms-application-provider"
CLIENT_BIN="$HOME/rt-mbms-client/build/client"
MODEM_BIN="$HOME/rt-mbms-modem/build/modem"
SOAPY_DIR="${SOAPY_SDR_PLUGIN_PATH:-$HOME/soapy-zmq-bridge}"
USER_NAME="${SUDO_USER:-$(whoami)}"

RX_IP=10.80.0.2
TUN_DEV=mbms_modem_tun
CLIENT_IFACE=192.168.180.10

mkdir -p "$LOG_DIR"

log() { echo; echo "=== $* ==="; }

# -----------------------------------------------------------------------
# 1. Stop any previous run
# -----------------------------------------------------------------------
log "Stopping any previous run"
sudo -n "$SCRIPT_DIR/receive-netns.sh" stop >/dev/null 2>&1
for b in srsenb mbms-gw bmsc; do sudo -n pkill -x "$b" 2>/dev/null; done
pkill -f 'node --env-file=.env server.js' 2>/dev/null
pkill -f 'node hls-http-proxy.js' 2>/dev/null
sudo -n pkill -x srsepc 2>/dev/null
sleep 2

# -----------------------------------------------------------------------
# 2. Transmit side
# -----------------------------------------------------------------------
log "Launching transmit chain: EPC"
sudo -n bash -c "cd '$CONF' && exec nohup '$TX_DIR/build/srsepc/src/srsepc' epc.conf" > "$LOG_DIR/EPC.log" 2>&1 &
disown -a
sleep 2

log "Launching transmit chain: eNB"
( cd "$CONF" && exec nohup "$TX_DIR/build/srsenb/src/srsenb" enb_baseline.conf > "$LOG_DIR/eNB.log" 2>&1 & )
sleep 3

log "Launching transmit chain: MBMS-GW"
( cd "$CONF" && exec nohup "$GW_DIR/build/mbms-gw/mbms-gw" mbms-gw.conf > "$LOG_DIR/MBMS-GW.log" 2>&1 & )
sleep 1

log "Launching transmit chain: BM-SC"
( cd "$CONF" && exec nohup "$BMSC_DIR/build/bmsc/bmsc" bmsc.conf > "$LOG_DIR/BM-SC.log" 2>&1 & )
sleep 2

# -----------------------------------------------------------------------
# 3. Application Provider (xMB control portal / website)
# -----------------------------------------------------------------------
log "Launching Application Provider portal (http://127.0.0.1:8080)"
( cd "$PORTAL_DIR" && exec nohup node --env-file=.env server.js > "$LOG_DIR/Portal.log" 2>&1 & )
sleep 2

# -----------------------------------------------------------------------
# 4. Receive side (modem/client/Application-WUI in the mbms-rx netns)
# -----------------------------------------------------------------------
log "Launching receive chain (netns mbms-rx)"
sudo -n "$SCRIPT_DIR/receive-netns.sh" start
sleep 2

# This sandbox kills any thread that requests realtime (SCHED_RR/FIFO)
# scheduling, which the modem does by default -- receive-netns.sh's own
# regenerated config carries the real (nonzero) priorities every run, so
# patch them to 0 here and restart the modem. Harmless on a normal host
# (0 just means "don't bother asking for realtime").
MODEM_CONF="$LOG_DIR/modem_zmqtest.netns.conf"
if [ -f "$MODEM_CONF" ]; then
  log "Patching modem config to disable realtime scheduling (sandbox workaround)"
  sudo -n sed -i \
    -e 's/reader_thread_priority_rt = [0-9]*/reader_thread_priority_rt = 0/' \
    -e 's/thread_priority_rt = [0-9]*/thread_priority_rt = 0/' \
    -e 's/main_thread_priority_rt = [0-9]*/main_thread_priority_rt = 0/' \
    "$MODEM_CONF"

  log "Restarting modem with the patched config"
  sudo -n pkill -9 -f "$MODEM_BIN" 2>/dev/null
  sleep 2
  # The killed modem can leave its TUN device orphaned in a broken state
  # (write errors on the replacement modem's otherwise-identical device of
  # the same name) -- delete it explicitly so the restarted modem creates a
  # genuinely fresh one.
  sudo -n ip netns exec mbms-rx ip link del "$TUN_DEV" 2>/dev/null
  sudo -n ip netns exec mbms-rx env HOME="$HOME" PATH="/usr/local/bin:/usr/bin:/bin" \
    SOAPY_SDR_PLUGIN_PATH="$SOAPY_DIR" nohup bash -c \
    "cd '$CONF' && exec '$MODEM_BIN' -c '$MODEM_CONF' -b 10 -l 2 -s 4" \
    > "$LOG_DIR/Modem.log" 2>&1 &
  disown -a
fi

log "Waiting for the modem's TUN device ($TUN_DEV)"
tun_ok=0
# 90s: on this sandbox's (slower/contended) CPU, cell search + sync can take
# ~50-60s before the modem creates the TUN device (confirmed live, repeatedly).
for _ in $(seq 1 90); do
  sudo -n ip netns exec mbms-rx ip link show "$TUN_DEV" >/dev/null 2>&1 && { tun_ok=1; break; }
  sleep 1
done
if [ "$tun_ok" != 1 ]; then
  echo "WARNING: $TUN_DEV never appeared after 90s -- check $LOG_DIR/Modem.log"
  echo "         (attempting to configure it anyway, in case it appears moments later)"
fi
{
  log "Configuring $TUN_DEV and routing content multicast onto it"
  sudo -n ip netns exec mbms-rx ip addr add "$CLIENT_IFACE/24" dev "$TUN_DEV" 2>/dev/null
  sudo -n ip netns exec mbms-rx ip link set "$TUN_DEV" up
  sudo -n ip netns exec mbms-rx ip route replace 224.0.0.0/4 dev "$TUN_DEV"
  sudo -n ip netns exec mbms-rx sysctl -q -w "net.ipv4.conf.${TUN_DEV}.rp_filter=0"
  sudo -n ip netns exec mbms-rx sysctl -q -w net.ipv4.conf.all.rp_filter=0
  sudo -n ip netns exec mbms-rx nft add table ip mbmsfix 2>/dev/null
  sudo -n ip netns exec mbms-rx nft add chain ip mbmsfix pre '{ type filter hook prerouting priority -300; policy accept; }' 2>/dev/null
  sudo -n ip netns exec mbms-rx nft add rule ip mbmsfix pre iifname "$TUN_DEV" ip saddr 127.0.0.0/8 ip saddr set 192.168.180.1 2>/dev/null

  # Restart the client only after the TUN/route exist: its multicast group
  # joins resolve the receiving interface from the routing table at join
  # time, so joining before this route exists can bind to the wrong
  # interface for that socket's whole lifetime.
  log "Restarting client (http://$RX_IP:3020, so its multicast joins bind correctly)"
  sudo -n pkill -9 -f "$CLIENT_BIN" 2>/dev/null
  sleep 1
  sudo -n ip netns exec mbms-rx runuser -u "$USER_NAME" -- env HOME="$HOME" PATH="/usr/local/bin:/usr/bin:/bin" \
    bash -c "cd '$CONF' && exec '$CLIENT_BIN' -c client_recv.conf -i 0.0.0.0 -l 2" \
    > "$LOG_DIR/Client.log" 2>&1 &
  disown -a
}
sleep 3

# -----------------------------------------------------------------------
# 5. Load and activate the RTVE demo content
# -----------------------------------------------------------------------
log "Loading RTVE demo content"
( cd "$DEMO_DIR" && node load-demo.js rtve-24h.json )

AUTH_TOKEN="$(awk -F= '/^AUTH_TOKEN=/{print $2}' "$PORTAL_DIR/.env")"
AUTH="$(printf 'admin:%s' "$AUTH_TOKEN" | base64)"

log "Activating the RTVE xMB session"
curl -s -X PUT http://127.0.0.1:8080/api/xmb/services/svc-1/sessions/sess-1 \
  -H "Authorization: Basic $AUTH" -H "Content-Type: application/json" \
  -d '{"state":"Active"}' -w "\n[HTTP %{http_code}]\n"

log "Done"
cat <<EOF
  Application Provider portal : http://127.0.0.1:8080
  Player UI (WUI)             : http://$RX_IP:3000
  Client API                  : http://$RX_IP:3020/client-api/
  Logs                        : $LOG_DIR/*.log

  Tear down with: sudo $SCRIPT_DIR/receive-netns.sh stop && $SCRIPT_DIR/transmit.sh --stop
EOF
