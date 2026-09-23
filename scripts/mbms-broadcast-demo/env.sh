#!/bin/bash
# Shared configuration for the MBMS Broadcast end-to-end demo scripts (01-05 below).
# Source this file, don't execute it: `source env.sh`.
#
# This is the LTE-broadcast counterpart of rt-mbs-examples/scripts/mbs-broadcast-demo/env.sh,
# and deliberately keeps the same shape: one file naming every path, port and identity, with
# everything else derived from it.
#
# All paths default to this development machine's checkout layout. If the repositories live
# somewhere else, edit REPOS_ROOT below; everything else follows from it.

# Every path below is a default, and every one can be overridden. Three ways, in the order
# they are applied:
#
#   1. local.env beside this file, if it exists. Gitignored, so machine-specific paths live
#      there rather than in a tracked script. Copy local.env.example and edit.
#   2. Environment variables, which win over local.env: REPOS_ROOT=/srv/code ./demo up
#   3. Editing the defaults here, which is the option that makes your checkout diverge.
#
# The layout the defaults assume is one development machine's habit ($HOME/Repos, with the
# MBMS repositories grouped under rt-mbms/) and nothing more. If your checkouts are somewhere
# else, or scattered, set the roots or the individual directories.

set -a

# Machine-specific overrides, if the operator has written any. Read before every default
# below, so it can set the roots as well as individual directories. Gitignored on purpose.
_DEMO_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$_DEMO_ENV_DIR/local.env" ] && . "$_DEMO_ENV_DIR/local.env"

# ------------------------------------------------------------------------------------
# Repository locations
# ------------------------------------------------------------------------------------
REPOS_ROOT="${REPOS_ROOT:-$HOME/Repos}"
RTMBMS_ROOT="${RTMBMS_ROOT:-$REPOS_ROOT/rt-mbms}"

TX_DIR="${TX_DIR:-$RTMBMS_ROOT/rt-mbms-tx}"
GW_DIR="${GW_DIR:-$RTMBMS_ROOT/rt-mbms-gw}"
BMSC_DIR="${BMSC_DIR:-$RTMBMS_ROOT/rt-mbms-bmsc}"
MODEM_DIR="${MODEM_DIR:-$RTMBMS_ROOT/rt-mbms-modem}"
CLIENT_DIR="${CLIENT_DIR:-$RTMBMS_ROOT/rt-mbms-client}"
APP_DIR="${APP_DIR:-$RTMBMS_ROOT/rt-mbms-application}"
PORTAL_DIR="${PORTAL_DIR:-$RTMBMS_ROOT/rt-mbms-application-provider}"

# The two launchers this demo drives rather than reimplements. They own the component
# start order, the sudo handling for srsepc, the netns/veth for the receive side and the
# per-component log files; duplicating any of that here would be a second copy to keep in
# step with the first.
TUTORIAL_DIR="${TUTORIAL_DIR:-$RTMBMS_ROOT/rt-mbms-examples/scripts/tmux/mbms-broadcast-tutorial}"
TRANSMIT_SH="${TRANSMIT_SH:-$TUTORIAL_DIR/transmit.sh}"
RECEIVE_SH="${RECEIVE_SH:-$TUTORIAL_DIR/receive-netns.sh}"
LOAD_DEMO_JS="${LOAD_DEMO_JS:-$TUTORIAL_DIR/demo-content/load-demo.js}"
# Where transmit.sh/receive-netns.sh write their own component logs. Named here so
# status.sh and the troubleshooting output can point at them.
STACK_LOG_DIR="${STACK_LOG_DIR:-$HOME/.local/state/mbms-broadcast-tutorial}"

# ------------------------------------------------------------------------------------
# Run-time state: logs, pidfiles, generated templates, encoded content -- all under this
# script directory's own `run/` subdirectory, not /tmp and not scattered across the trees.
# ------------------------------------------------------------------------------------
DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$DEMO_ROOT/run"
LOG_DIR="$RUN_DIR/logs"
PID_DIR="$RUN_DIR/pids"
STATE_DIR="$RUN_DIR/state"          # ids of the service/session this run created
MEDIA_ROOT="$RUN_DIR/media"         # this demo's own origin document root

# ------------------------------------------------------------------------------------
# Receive side (rt-mbms-modem, rt-mbms-client, rt-mbms-application)
#
# receive-netns.sh puts all three in their own network namespace: on a single host the
# eNB's M1-U receiver and the client's content receiver both want UDP :2153 and would
# otherwise collide. These are that namespace's own addresses, which is where the player
# UI and both APIs are reached from the host.
# ------------------------------------------------------------------------------------
# These mirror receive-netns.sh's own values, which that script fixes internally; setting
# them here changes what this demo reports and checks, not what it creates.
NETNS="${NETNS:-mbms-rx}"
RX_ADDR="${RX_ADDR:-10.80.0.2}"
APP_PORT="${APP_PORT:-3000}"
MODEM_API_PORT="${MODEM_API_PORT:-3010}"
CLIENT_API_PORT="${CLIENT_API_PORT:-3020}"

# ------------------------------------------------------------------------------------
# Transmit-side control ports (as configured in the tutorial's ./conf)
# ------------------------------------------------------------------------------------
ENB_CTRL_PORT="${ENB_CTRL_PORT:-2100}"
GW_CTRL_PORT="${GW_CTRL_PORT:-2101}"
SBC_BRIDGE_PORT="${SBC_BRIDGE_PORT:-2102}"
XMB_C_PORT="${XMB_C_PORT:-8543}"

# rt-mbms-application-provider (the portal). Host, port and Basic-auth credentials come
# from its own .env, so this demo cannot disagree with the portal about its own token.
PORTAL_ENV="${PORTAL_ENV:-$PORTAL_DIR/.env}"
PORTAL_HOST="${PORTAL_HOST:-127.0.0.1}"
PORTAL_PORT="${PORTAL_PORT:-8080}"

# ------------------------------------------------------------------------------------
# Local content origin
#
# The BM-SC pulls the presentation over plain HTTP from this origin, which runs on the
# transmit side (root namespace) like the BM-SC itself. This replaces the CDN source the
# older demo-content template uses: the BM-SC's libcurl/GnuTLS build cannot always
# negotiate a modern CDN's TLS1.3/HTTP2 handshake, and a local origin removes both that
# and the content-rights question from the demo.
#
# Port 3005, not rt-mbs-examples' 3004, so the MBS demo's own media server and this one
# can be up at the same time on one host.
# ------------------------------------------------------------------------------------
MEDIA_HOST="${MEDIA_HOST:-127.0.0.1}"
MEDIA_PORT="${MEDIA_PORT:-3005}"

# What the looping live encoder plays and what it writes. Named here rather than in
# live-encoder.sh so the encoder and the xMB session cannot disagree about which
# presentation they mean. A missing source file is not a failure: the encoder generates a
# test pattern instead, so a fresh checkout runs with no content prerequisite at all.
LIVE_SOURCE_MEDIA="${LIVE_SOURCE_MEDIA:-$HOME/MWC_TV_RADIO/TV_1.mp4}"
LIVE_STREAM_NAME="${LIVE_STREAM_NAME:-tv_1_live}"
# Directory holding the source clips the channel line-up names, and the line-up itself. Every
# channel in it is encoded and served by the origin; the ones marked onAir are also carried over
# the air as their own xMB service. The same four channels, under the same names, are in
# rt-mbs-examples' and rt-dvb-i-examples' own channels.json.
CONTENT_ROOT="${CONTENT_ROOT:-$HOME/MWC_TV_RADIO}"
CHANNELS_FILE="${CHANNELS_FILE:-$DEMO_ROOT/channels.json}"
# hls or dash. The BM-SC ingests either and rt-mbms-application plays either (its
# player select offers hls.js and dash.js), but only HLS completes the chain today.
#
# The two are announced differently: for HLS the BM-SC's USBD references the Master
# Playlist through r12:appService, for DASH it references the MPD through
# r9:mediaPresentationDescription and omits r12:appService (see the citation of
# TS 26.346 Annex L.2.5 at service_manager.cc's usbd assembly). rt-mbms-client builds a
# content stream only from r12:appService, so a DASH service is announced, received and
# registered, but never becomes a playable stream. Confirmed live: the SA bundle arrives
# and lists the service, and the client's own /client-api/services stays empty.
#
# Default hls so the demo delivers video. Set dash to reproduce the gap above.
LIVE_FORMAT="${LIVE_FORMAT:-hls}"
# Segment duration, in seconds, and so also how often a segment is published and put
# on the air: the encoder runs in real time (-re), so one 5 s segment appears every 5 s.
# It also sets the player's window, because the BM-SC broadcasts only the last 10 segments
# of a live playlist (kHlsLiveWindowSegments in its service_manager.cc, a compiled-in
# constant with no configuration option): 10 x 5 s = 50 s of buffer at the receiver, which
# stays comfortably inside the client's own 90 s media cache (max_file_age in
# client_recv.conf). Matches rt-mbs-examples' own demo, which also uses 5 s.
LIVE_SEG_DURATION="${LIVE_SEG_DURATION:-5}"
# Keep more segments on disk than the presentation advertises: the BM-SC fetches each
# segment after it appears in the manifest, and a short retention lets one be deleted
# before that fetch happens, which shows up as 404s and a sender that never catches up.
LIVE_WINDOW="${LIVE_WINDOW:-24}"
LIVE_EXTRA_WINDOW="${LIVE_EXTRA_WINDOW:-48}"
LIVE_VIDEO_BITRATE="${LIVE_VIDEO_BITRATE:-400k}"
LIVE_AUDIO_BITRATE="${LIVE_AUDIO_BITRATE:-64k}"
# The encode shares this host with a software radio that has to keep real time: the modem
# runs a bandwidth-blind cell search and then decodes every subframe on a deadline, and it
# reports "SYNC_OFFSET_DIAG SLOWCALL" and stops decoding MCCH when it does not get the CPU
# it needs. So the default encode is deliberately cheap (small frame, 25 fps, x264's
# fastest preset) rather than the highest quality the box could manage: the demo is about
# the delivery chain, not the picture. Raise these on a host with cores to spare, or when
# encoding on a different machine from the radio.
LIVE_SCALE="${LIVE_SCALE:-640:360}"
LIVE_FPS="${LIVE_FPS:-25}"
LIVE_X264_PRESET="${LIVE_X264_PRESET:-ultrafast}"

# ------------------------------------------------------------------------------------
# xMB service and session identity for this demo (TS 26.348 field names)
# ------------------------------------------------------------------------------------
DEMO_SERVICE_NAME="${DEMO_SERVICE_NAME:-5G-MAG.tv 1}"
DEMO_SERVICE_CLASS="${DEMO_SERVICE_CLASS:-urn:example:live-tv}"
DEMO_SERVICE_LANG="${DEMO_SERVICE_LANG:-eng}"

# TMGI. Service id 21 is free on this rig: 16 is the BM-SC's own built-in content session,
# 17 and 20 belong to the two demo-content templates, and 0 is the SACH.
DEMO_TMGI_MCC="${DEMO_TMGI_MCC:-901}"
DEMO_TMGI_MNC="${DEMO_TMGI_MNC:-56}"
DEMO_TMGI_SERVICE_ID="${DEMO_TMGI_SERVICE_ID:-21}"
DEMO_TSI="${DEMO_TSI:-4}"
DEMO_MCAST_ADDR="${DEMO_MCAST_ADDR:-239.255.0.1}"
DEMO_MCAST_PORT="${DEMO_MCAST_PORT:-2153}"
DEMO_SERVICE_AREA_CODE="${DEMO_SERVICE_AREA_CODE:-1}"
# The C-TEID decides which PMCH carries this session, and it is not free: the eNB's
# enb_baseline.conf assigns 0xbbbb to PMCH0 (which carries MCCH and the SACH) and 0xCCCC
# to PMCH1 (content). 52428 is 0xCCCC, so content lands on PMCH1. Changing this without
# changing pmch1.session_teids in that config puts the session on no PMCH at all.
DEMO_C_TEID="${DEMO_C_TEID:-52428}"
DEMO_MME_SM_PEERS="${DEMO_MME_SM_PEERS:-127.0.0.1:2123}"
DEMO_MAX_BITRATE="${DEMO_MAX_BITRATE:-4000000}"
# Application-layer FEC, the per-session yes/no of TS 26.348 Table 5.4-1. The scheme
# itself is the BM-SC's own configuration (xmb.content_fec_scheme in bmsc.conf).
#
# On by default, because unprotected delivery does not survive this rig. Broadcast has no
# retransmission, so one lost packet destroys the whole object: measured here with FEC
# off, a packet loss small enough to leave the radio's own BLER at 0 still lost ~7% of
# segments, and since rt-mbms-client publishes only the contiguous run of segments it
# holds, one hole truncated the player's playlist to two entries and playback stalled.
DEMO_FEC_ENABLED="${DEMO_FEC_ENABLED:-true}"

# How long 05-start-client-and-app.sh waits for the modem and client REST APIs after
# receive-netns.sh returns. receive-netns.sh does its own wait for the modem's TUN device
# (bandwidth-blind cell search, slow on a loaded host) and that timeout lives in that
# script; this one only covers the APIs coming up behind it.
MODEM_API_WAIT_SECS="${MODEM_API_WAIT_SECS:-90}"

set +a
