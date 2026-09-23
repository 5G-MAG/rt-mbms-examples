#!/usr/bin/env bash
#
# receive-netns.sh -- run the MBMS RECEIVE chain (modem + client + application)
# in a dedicated network namespace, so its multicast / UDP :2153 is isolated
# from the transmit side (the eNB's M1-U receiver) on the same host. Without
# this, both want UDP :2153 and whichever starts second fails to bind.
#
#   sudo ./receive-netns.sh start     # create netns + launch modem/client/application in it
#   sudo ./receive-netns.sh stop      # tear it down
#
# Topology:
#   root netns : srsepc, srsenb (ZMQ TX on tcp://*:2000), mbms-gw, bmsc   -> run via ./transmit.sh
#   mbms-rx    : modem (ZMQ RX), client, application (:3000)
#   veth: 10.80.0.1 (root) <-> 10.80.0.2 (mbms-rx). The modem connects its ZMQ
#         RX to tcp://10.80.0.1:2000 (the eNB TX, which binds all interfaces).
#
# Reach the receiver from the host at 10.80.0.2:  the player UI is
# http://10.80.0.2:3000  (modem API :3010, client API :3020 also on 10.80.0.2).
#
set -u
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo $0 ${1:-start}"; exit 1; }

NS=mbms-rx
ROOT_IP=10.80.0.1
RX_IP=10.80.0.2
MASK=24
ZMQ_PORT=2000
# The modem writes recovered MTCH content to this TUN, which must carry
# CLIENT_IFACE and be up, and the content multicast must route via the TUN (not
# the veth). The client's FLUTE receiver binds to CLIENT_BIND: this MUST be
# 0.0.0.0, not the interface's unicast address -- a UDP socket bound to a
# unicast address does not receive multicast on Linux even with the group
# joined. The group is still joined on the TUN via the 224.0.0.0/4 route.
TUN_DEV=mbms_modem_tun
CLIENT_IFACE=192.168.180.10
CLIENT_BIND=0.0.0.0
CONTENT_MCAST=239.255.0.1

USER_NAME="${SUDO_USER:-jordijoan}"
UH="$(getent passwd "$USER_NAME" | cut -d: -f6)"
TUT="$UH/rt-mbms-examples/scripts/tmux/mbms-broadcast-tutorial"
CONF="$TUT/conf"
MODEM="$UH/rt-mbms-modem/build/modem"
CLIENT_BIN="$UH/rt-mbms-client/build/client"
APP_DIR="$UH/rt-mbms-application"
SOAPY_DIR="${SOAPY_SDR_PLUGIN_PATH:-$UH/soapy-zmq-bridge}"
LOG="$UH/.local/state/mbms-broadcast-tutorial"
MODEM_NS_CONF="$LOG/modem_zmqtest.netns.conf"

nsrun() { # nsrun <Name> <workdir> <command string>  -- run as the unprivileged user
  local name="$1" workdir="$2"; shift 2
  ip netns exec "$NS" runuser -u "$USER_NAME" -- \
    env HOME="$UH" PATH="/usr/local/bin:/usr/bin:/bin" SOAPY_SDR_PLUGIN_PATH="$SOAPY_DIR" \
    nohup bash -c "cd '$workdir' && exec $*" > "$LOG/$name.log" 2>&1 &
  echo "  $name launched in netns '$NS'  (log: $LOG/$name.log)"
}

nsrun_root() { # nsrun_root <Name> <workdir> <command string>  -- run as ROOT
  # The modem creates a TUN (mbms_modem_tun) to emit the recovered MTCH IP
  # packets; that needs CAP_NET_ADMIN. Under runuser (unprivileged) it fails
  # with "TUN/TAP not up - dropping gw RX message" and drops all content, so
  # the modem must keep root here. The log is chowned back to the user after.
  local name="$1" workdir="$2"; shift 2
  ip netns exec "$NS" \
    env HOME="$UH" PATH="/usr/local/bin:/usr/bin:/bin" SOAPY_SDR_PLUGIN_PATH="$SOAPY_DIR" \
    nohup bash -c "cd '$workdir' && exec $*" > "$LOG/$name.log" 2>&1 &
  chown "$USER_NAME": "$LOG/$name.log" 2>/dev/null || true
  echo "  $name launched in netns '$NS' (root, for TUN)  (log: $LOG/$name.log)"
}

start() {
  command -v ip >/dev/null || { echo "iproute2 ('ip') required"; exit 1; }
  [ -x "$MODEM" ] && [ -x "$CLIENT_BIN" ] && [ -f "$APP_DIR/app.js" ] || { echo "build modem/client and check $APP_DIR/app.js"; exit 1; }
  mkdir -p "$LOG"; chown "$USER_NAME": "$LOG" 2>/dev/null || true

  # 1) namespace + veth
  ip netns add "$NS" 2>/dev/null || true
  ip netns exec "$NS" ip link set lo up
  if ! ip link show vrx0 >/dev/null 2>&1; then
    ip link add vrx0 type veth peer name vrx1
    ip link set vrx1 netns "$NS"
  fi
  ip addr add "$ROOT_IP/$MASK" dev vrx0 2>/dev/null || true
  ip link set vrx0 up
  ip netns exec "$NS" ip addr add "$RX_IP/$MASK" dev vrx1 2>/dev/null || true
  ip netns exec "$NS" ip link set vrx1 up
  # NB: multicast is routed onto the modem's TUN (below, once it exists), NOT
  # the veth -- all received MBMS content arrives on the TUN; the veth carries
  # only the unicast ZMQ I/Q. (A 224.0.0.0/4 route via vrx1 here would send the
  # client's joins to the wrong interface and it would receive nothing.)

  # 2) modem config with the ZMQ RX pointed at the root-side veth IP (eNB TX = tcp://*:2000)
  sed "s#tcp://127.0.0.1:${ZMQ_PORT}#tcp://${ROOT_IP}:${ZMQ_PORT}#g; s#tcp://localhost:${ZMQ_PORT}#tcp://${ROOT_IP}:${ZMQ_PORT}#g" \
    "$CONF/modem_zmqtest.conf" > "$MODEM_NS_CONF"
  chown "$USER_NAME": "$MODEM_NS_CONF" 2>/dev/null || true

  # 3) launch the receive chain inside the namespace, as the user
  echo "Starting receive chain in netns '$NS' (eNB TX expected at ${ROOT_IP}:${ZMQ_PORT}) ..."
  # The modem samples the full 10 MHz carrier (-b 10), reads the CAS from the MIB,
  # and learns the (narrower) PMCH bandwidth from SIB13 -- so no PRB override here.
  # For the n_prb=25 PMCH BLER investigation (see
  # rt-mbms-modem/KNOWN_ISSUES.md), set MCH_DIAG=1 and/or
  # SCS_TIMING_DIAG=1 in the environment before running this script.
  #
  # 2026-07-16: dropped SYNC_ERR_DIAG, RACE_DIAG, PMCH_RE_DUMP, DECIM_DUMP --
  # all leftover from earlier, already-concluded investigations (the CFO/
  # sync-error and race-condition bugs they were chasing are fixed; PMCH_RE_DUMP
  # only actually dumps when PMCH_RE_DUMP_TTI also targets a specific tti, unset
  # here). SYNC_ERR_DIAG in particular fires an unconditional fprintf on EVERY
  # MBSFN subframe (~90% of all traffic) -- real per-subframe I/O overhead, on
  # a pipeline already confirmed to run at ~91% of nominal throughput with the
  # ring buffer chronically near 0% occupancy (ZMQRX_RATIO_DIAG), i.e. zero
  # slack. Piling on more per-subframe fprintf calls than a given investigation
  # actually needs eats into that margin and is a plausible contributor to the
  # SYNC_OFFSET_DIAG SLOWCALL overruns/BLER collapse/crash seen during the CAS
  # muting investigation. Keep only what THAT investigation needs live.
  # TEMPORARY 2026-07-17: RACE_DIAG2 to chase the CAS-muting sf=0 anomaly's root
  # cause (already fixed/masked - see SIB13_MBSFN_TEST_RESULTS.md Finding 3).
  # Remove once that follow-up investigation concludes.
  nsrun_root Modem  "$CONF"    "env MCH_DIAG=1 ZMQRX_RATIO_DIAG=1 SYNC_OFFSET_DIAG=1 RACE_DIAG2=1 '$MODEM' -c '$MODEM_NS_CONF' -b 10 -l 2 -s 4"

  # The modem creates $TUN_DEV but leaves it DOWN with no address. Wait for it,
  # then bring it up, give it CLIENT_IFACE (the client binds its FLUTE receiver to
  # that IP -- otherwise bind() -> EADDRNOTAVAIL and the client aborts), and route the
  # content multicast onto the TUN so joined sockets receive the injected
  # packets (rp_filter off so the arrival interface isn't reverse-path-dropped).
  echo "Waiting for $TUN_DEV (modem creates it on start) ..."
  tun_ok=0
  for _ in $(seq 1 20); do
    if ip netns exec "$NS" ip link show "$TUN_DEV" >/dev/null 2>&1; then tun_ok=1; break; fi
    sleep 1
  done
  if [ "$tun_ok" = 1 ]; then
    ip netns exec "$NS" ip addr add "$CLIENT_IFACE/$MASK" dev "$TUN_DEV" 2>/dev/null || true
    ip netns exec "$NS" ip link set "$TUN_DEV" up
    # Route ALL multicast onto the TUN (not just one group): the SACH and the
    # content can use different groups (e.g. 224.0.0.120 for the announcement,
    # $CONTENT_MCAST for media), and the client only learns the media group
    # after it receives the SACH. Sending all of 224.0.0.0/4 to the TUN covers
    # both regardless of what the bmsc assigns.
    ip netns exec "$NS" ip route replace 224.0.0.0/4 dev "$TUN_DEV" 2>/dev/null || true
    ip netns exec "$NS" sysctl -q -w "net.ipv4.conf.${TUN_DEV}.rp_filter=0" 2>/dev/null || true
    ip netns exec "$NS" sysctl -q -w "net.ipv4.conf.all.rp_filter=0" 2>/dev/null || true
    # The BM-SC/gw run on loopback, so recovered broadcast packets arrive on the
    # TUN with a 127.0.0.1 source. The kernel silently drops multicast datagrams
    # whose source is loopback on a non-loopback interface, BEFORE the socket
    # layer (confirmed: IpInReceives climbs but they never reach a UDP socket;
    # route_localnet does not cover the multicast input path). Rewrite the source
    # to a valid on-link address in prerouting -- before the routing/delivery
    # decision -- so the SACH (224.0.0.120) and content reach the client's FLUTE
    # receiver. This is a single-host demo artifact; real BM-SC/gw have real IPs.
    ip netns exec "$NS" nft add table ip mbmsfix 2>/dev/null || true
    ip netns exec "$NS" nft add chain ip mbmsfix pre '{ type filter hook prerouting priority -300; policy accept; }' 2>/dev/null || true
    ip netns exec "$NS" nft add rule ip mbmsfix pre iifname "$TUN_DEV" ip saddr 127.0.0.0/8 ip saddr set 192.168.180.1 2>/dev/null || true
    echo "  $TUN_DEV up, addr $CLIENT_IFACE, $CONTENT_MCAST routed onto it (loopback src rewritten)"
  else
    echo "  WARNING: $TUN_DEV never appeared -- modem not forwarding; the client will fail to bind"
  fi

  nsrun Client      "$CONF"    "'$CLIENT_BIN' -c client_recv.conf -i $CLIENT_BIND -l ${CLIENT_LOG_LEVEL:-2}"
  sleep 2
  nsrun Application "$APP_DIR" "node app.js"
  sleep 2

  echo
  echo "Receive chain is in netns '$NS'. From the host:"
  echo "  player UI  : http://${RX_IP}:3000"
  echo "  modem API  : http://${RX_IP}:3010/modem-api/"
  echo "  client API : http://${RX_IP}:3020/client-api/"
  echo "Optional: to see modem/client in the portal too, set MODEM_HOST=${RX_IP} CLIENT_HOST=${RX_IP}"
  echo "          in ${APP_DIR}/.env and restart the portal (the player UI above already works without this)."
  echo "Tear down with: sudo $0 stop"
}

stop() {
  ip netns pids "$NS" 2>/dev/null | xargs -r kill 2>/dev/null || true
  sleep 1
  ip netns pids "$NS" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
  ip netns del "$NS" 2>/dev/null || true
  ip link del vrx0 2>/dev/null || true
  echo "Receive netns '$NS' and veth torn down."
}

case "${1:-start}" in
  start) start ;;
  stop)  stop ;;
  *) echo "usage: sudo $0 [start|stop]"; exit 1 ;;
esac
