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

# The Cell Broadcast Centre. It sits outside $RTMBMS_ROOT because it is not an MBMS
# component: Public Warning System alerts reach handsets over the cell's own system
# information, so this part of the demo works with no MBMS bearer and no content session.
CBC_DIR="${CBC_DIR:-$REPOS_ROOT/rt-pws-cbc}"

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

# rt-pws-cbc (the Cell Broadcast Centre), same arrangement: it refuses to start without
# AUTH_TOKEN, and its own .env is the single source of that token.
# Whether ./start-all.sh also brings up the emergency-alert path. Off by default: a warning
# needs none of the content machinery, so a broadcast demo should not require the extra
# repository, and an operator should choose to put alerting on the air rather than have it
# appear. ./start-all.sh --with-alerts sets it for one run; local.env can set it permanently.
DEMO_ALERTS="${DEMO_ALERTS:-0}"

CBC_ENV="${CBC_ENV:-$CBC_DIR/.env}"
CBC_HOST="${CBC_HOST:-127.0.0.1}"
CBC_PORT="${CBC_PORT:-8081}"

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

# ------------------------------------------------------------------------------------
# Positioning assistance (option 1 of the broadcast positioning proposal: the relative
# transmit delay of each cell travels in an assistance document, FLUTE-delivered in its own
# MBMS session, while one SFN'ed SIB16 gives the network time). Optional, started by
# 09-start-positioning.sh. The document describes a virtual four-cell network around the one
# eNB the radio has, and the UE's RSTDs are computed, so what is live is the delivery of the
# document and the network time from SIB16. The code is positioning-assistance/ at the root of
# this repository.
#
# The session uses its own TMGI, TSI, multicast group and C-TEID. The C-TEID must differ from
# the content session's and must be listed in pmch1.session_teids of enb_baseline.conf
# ("0xCCCC,0xCCCD"): the eNB gives a PMCH session by C-TEID and the first match wins.
# ------------------------------------------------------------------------------------
BPS_DIR="${BPS_DIR:-$(cd "$DEMO_ROOT/../../positioning-assistance" && pwd)}"
POS_TMGI_SERVICE_ID="${POS_TMGI_SERVICE_ID:-22}"
POS_TSI="${POS_TSI:-5}"
POS_MCAST_ADDR="${POS_MCAST_ADDR:-239.255.0.2}"
POS_MCAST_PORT="${POS_MCAST_PORT:-2155}"
POS_C_TEID="${POS_C_TEID:-52429}"            # 0xCCCD
POS_FILE_PATH="${POS_FILE_PATH:-positioning/assistance.json}"
POS_INTERVAL="${POS_INTERVAL:-5}"            # seconds between documents
POS_VALIDITY="${POS_VALIDITY:-90}"           # seconds a document stays usable
# Where the UE reads the document: rt-mbms-client serves the files it receives under the session's Display Base URL.
POS_DOC_URL="${POS_DOC_URL:-http://$RX_ADDR:$CLIENT_API_PORT/$POS_FILE_PATH}"
POS_MEASURE="${POS_MEASURE:-formula}"        # formula: from the geometry; prs: simulated PRS waveforms; modem: measured by the modem on the PRS it receives (needs POS_RIG=1)
POS_SNR_DB="${POS_SNR_DB:-}"                 # prs: SNR per sample (UE default 0); sync: SNR per resource element (UE default 10)
# The rig (POS_RIG=1): the transmitter sends PRS and its IQ goes through a mixer (bps.rig) that adds emulated neighbouring cells, so the modem has several cells to
# measure on one eNB. The mixer takes the transmitter's stream on POS_RIG_TX_PORT and publishes it on the port the modem reads (2000).
POS_RIG="${POS_RIG:-0}"
POS_RIG_TX_PORT="${POS_RIG_TX_PORT:-2002}"
POS_RIG_OUT_PORT="${POS_RIG_OUT_PORT:-2000}"
POS_RIG_POWER_DB="${POS_RIG_POWER_DB:--6}"    # emulated cells against the real cell's PSS and SSS elements (-12: barely detected; -6: measured to about 10 ns)
POS_RIG_PRS_INDEX="${POS_RIG_PRS_INDEX:-2435}" # T_PRS 40, Delta_PRS 0: PRS in the cell acquisition subframe
POS_RIG_CP="${POS_RIG_CP:-}"                 # extended or normal: the real cell's cyclic prefix. Unset: whatever the baseline configuration has (extended_cp)
POS_SFN_FROM_MODEM="${POS_SFN_FROM_MODEM:-1}"   # 1: the document states the reference cell's SFN (OMA-LPPe systemFrameNumber), read from the modem's SIB16 boundary and time; 0: leave it out
POS_REF_ECGI="${POS_REF_ECGI:-}"                 # the reference cell's global identity as MCC-MNC-CELLIDENTITY (cellGlobalId, optional); empty: derived from the eNB configuration (mcc, mnc, enb_id and rr.conf cell_id); "none": not stated
POS_SERVICE_RADIUS_M="${POS_SERVICE_RADIUS_M:-}"   # how far from the centre of the cells a UE may be, for the search window of expectedRSTD; unset: the largest distance from the centre to a cell
POS_BROADCAST_AREA="${POS_BROADCAST_AREA:-}"       # the broadcastArea named in the file's envelope (the deck's example has "Area1"); unset: left out
POS_FEC="${POS_FEC:-false}"                    # true: application-layer FEC on the positioning session (the deck, slide 18 step 7: "FEC decoding"); off by default; needs the rt-libflute fix "File: send only as many encoding symbols in a packet as fit in it" in the BM-SC build (see bps.apps publish --fec)
POS_DELIVER="${POS_DELIVER:-file}"              # file: the assistance file goes over the air; url: its URL does (on the demo origin), and the UE fetches the file (the deck, slide 18)
POS_CELL_CFO="${POS_CELL_CFO:-}"                # per-cell carrier frequency offsets of the emulated cells in Hz, e.g. "3=150 4=-80" (separate oscillators); unset: none
POS_CELL_CP="${POS_CELL_CP:-}"                 # per-cell cyclic prefix, e.g. "3=normal 4=normal": stated in the document (publisher) and used by the mixer for that emulated cell. Unset: every cell has the serving cell's
POS_RIG_CELL_CP="${POS_RIG_CELL_CP-$POS_CELL_CP}"   # the cyclic prefixes the mixer gives its emulated cells; by default those the document states (set it empty, or differently, to test a document that does not match what is on the air)
POS_RIG_PRS="${POS_RIG_PRS:-true}"             # false: the transmitter sends no PRS but the mixer stays in the path (a control for measuring what PRS costs)
POS_RIG_SNR_DB="${POS_RIG_SNR_DB:-}"           # set: the mixer starts with white noise (it can be changed while running through $STATE_DIR/pos_rig_snr), Es/N0 per resource element at the modem (for block error rate against signal to noise ratio)
POS_RIG_FREEZE="${POS_RIG_FREEZE:-}"         # set: the emulated cells' delays do not drift (for checks against exact values)
LEAP_SECONDS="${LEAP_SECONDS:-18}"           # GPS - UTC, as in sib.conf.mbsfn

set +a
