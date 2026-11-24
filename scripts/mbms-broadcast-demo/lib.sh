#!/bin/bash
# Common helpers for the mbms-broadcast-demo scripts. Source after env.sh:
#   source env.sh; source lib.sh

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing command: $1 (install it and re-run)"
}

require_file() {
    [[ -e "$1" ]] || die "missing required file/executable: $1"
}

ensure_dirs() {
    mkdir -p "$LOG_DIR" "$PID_DIR" "$STATE_DIR" "$MEDIA_ROOT"
}

# Start a background process with its own log file and pidfile named after $2.
#   run_bg <name> <logfile-basename> <cmd...>
# Records the real child PID so stop-all.sh can signal precisely.
run_bg() {
    local name="$1" logbase="$2"; shift 2
    local logfile="$LOG_DIR/${logbase}.log"
    local pidfile="$PID_DIR/${logbase}.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
        log "$name already running (pid $(cat "$pidfile")), skipping"
        return 0
    fi
    log "starting $name (log: $logfile)"
    nohup "$@" >"$logfile" 2>&1 &
    echo $! > "$pidfile"
    disown
}

wait_for_tcp() {
    local host="$1" port="$2" timeout="${3:-20}"
    local waited=0
    until (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; do
        exec 3>&- 2>/dev/null || true
        sleep 1
        waited=$((waited+1))
        [[ $waited -ge $timeout ]] && return 1
    done
    exec 3>&- 2>/dev/null || true
    return 0
}

wait_for_http() {
    local url="$1" timeout="${2:-20}"
    local waited=0
    until curl -s -o /dev/null -m 2 "$url"; do
        sleep 1
        waited=$((waited+1))
        [[ $waited -ge $timeout ]] && return 1
    done
    return 0
}

netns_exists() {
    # `ip netns list` prints "<name> (id: N)" per line, not just the bare name, so match
    # the first field rather than the whole line.
    sudo -n ip netns list 2>/dev/null | awk '{print $1}' | grep -qx "$NETNS"
}

# Reads HOST/PORT/AUTH_USER/AUTH_TOKEN out of a service's own .env, so this demo and the
# service can never disagree about the token. Both services that need this refuse to start
# without AUTH_TOKEN, which is why an empty one is fatal here rather than a warning.
#   _read_service_env <env-file> <default-host> <default-port> <what-it-is>
# Sets _SVC_URL, _SVC_AUTH, _SVC_USER, _SVC_TOKEN.
_read_service_env() {
    local env_file="$1" default_host="$2" default_port="$3" what="$4"
    [[ -f "$env_file" ]] || die "$what .env not found at $env_file (it refuses to start without AUTH_TOKEN; see that repository's README)"
    local host port user token line k v
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*([A-Z0-9_]+)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
        k="${BASH_REMATCH[1]}"; v="${BASH_REMATCH[2]}"
        v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
        case "$k" in
            HOST) host="$v" ;;
            PORT) port="$v" ;;
            AUTH_USER) user="$v" ;;
            AUTH_TOKEN) token="$v" ;;
        esac
    done < "$env_file"
    [[ -n "$token" ]] || die "AUTH_TOKEN is empty in $env_file"
    _SVC_URL="http://${host:-$default_host}:${port:-$default_port}"
    _SVC_USER="${user:-admin}"
    _SVC_TOKEN="$token"
    _SVC_AUTH="${user:-admin}:${token}"
}

# Curl against a service's API, with the credentials already read.
#   _service_api <base-url> <user:token> <method> <path> [json-body]
_service_api() {
    local url="$1" auth="$2" method="$3" path="$4" body="${5:-}"
    if [[ -n "$body" ]]; then
        curl -s -m 90 -u "$auth" -X "$method" -H 'Content-Type: application/json' \
             --data "$body" "$url$path"
    else
        curl -s -m 90 -u "$auth" -X "$method" "$url$path"
    fi
}

# Portal (rt-mbms-application-provider): media service provisioning. Sets PORTAL_URL/PORTAL_AUTH.
portal_creds() {
    _read_service_env "$PORTAL_ENV" "$PORTAL_HOST" "$PORTAL_PORT" "portal"
    PORTAL_URL="$_SVC_URL"; PORTAL_AUTH="$_SVC_AUTH"; PORTAL_USER="$_SVC_USER"; PORTAL_TOKEN="$_SVC_TOKEN"
}

# Curl against the portal API. portal_creds must have run.
portal_api() {
    _service_api "$PORTAL_URL" "$PORTAL_AUTH" "$@"
}

# Cell Broadcast Centre (rt-pws-cbc): emergency alerts. Sets CBC_URL/CBC_AUTH.
cbc_creds() {
    _read_service_env "$CBC_ENV" "$CBC_HOST" "$CBC_PORT" "Cell Broadcast Centre"
    CBC_URL="$_SVC_URL"; CBC_AUTH="$_SVC_AUTH"; CBC_USER="$_SVC_USER"; CBC_TOKEN="$_SVC_TOKEN"
}

# Curl against the CBC API. cbc_creds must have run.
cbc_api() {
    _service_api "$CBC_URL" "$CBC_AUTH" "$@"
}

# One block of the start-all summary, phrased for whichever way the run was started: the alert
# path is optional, so the reader needs to know whether it is up and what to do either way.
alert_path_summary() {
    if [[ "${DEMO_ALERTS:-0}" == "1" ]]; then
        echo "  ./07-send-alert.sh          send an ETWS/CMAS alert and confirm the modem receives it"
        echo "  ./stop-alerts.sh            stop the alert path, leaving this demo running"
    else
        echo "  ./08-start-alerts.sh        add the emergency-alert path (not started: this run is broadcast only),"
        echo "                              then ./07-send-alert.sh to send one. Or start it with the demo next"
        echo "                              time: ./start-all.sh --with-alerts"
    fi
}

# Where the receiving client republishes what it got over the air, which is what the player in
# the application UI has to be pointed at. The client serves its reception cache on its own API
# port, under a directory named after the xMB session (the BM-SC names the objects that way), so
# this needs the session id recorded at provisioning time. Falls back to the session directory
# pattern with no id when no session has been provisioned yet.
broadcast_presentation_url() {
    local sid presentation
    sid="$(cat "$STATE_DIR/session_id" 2>/dev/null)"
    presentation="stream.m3u8"
    [[ "$LIVE_FORMAT" == "hls" ]] || presentation="stream.mpd"
    if [[ -n "$sid" ]]; then
        echo "http://$RX_ADDR:$CLIENT_API_PORT/xmb-app-manifest-$sid/$presentation"
    else
        echo "http://$RX_ADDR:$CLIENT_API_PORT/xmb-app-manifest-<session-id>/$presentation"
    fi
}

# The manifest this demo distributes, relative to the origin's document root.
live_presentation_path() {
    if [[ "$LIVE_FORMAT" == "hls" ]]; then
        echo "$LIVE_STREAM_NAME/manifest.m3u8"
    else
        echo "$LIVE_STREAM_NAME/manifest.mpd"
    fi
}

live_presentation_mime() {
    if [[ "$LIVE_FORMAT" == "hls" ]]; then
        echo "application/vnd.apple.mpegurl"
    else
        echo "application/dash+xml"
    fi
}

# POSTs a JSON body to the portal and echoes the created resource's id, taken from the
# response body or the Location header (the portal's create routes use either). Dies
# quoting the response body, because the interesting failures are reported there and not
# in the header: an xMB server that is not reachable, a TMGI already in use, a malformed
# body would otherwise all present identically as "no id".
portal_post_for_id() {
    local what="$1" path="$2" body="$3"
    local hdr_file resp_file code id
    # Under this demo's own run/ directory, not /tmp: a full /tmp made curl fail to write
    # its response here while the request itself had succeeded, which surfaced as a created
    # service whose id could not be read back. On this host /tmp is a tmpfs shared with
    # whatever else is running.
    mkdir -p "$STATE_DIR"
    hdr_file="$(mktemp -p "$STATE_DIR" hdr.XXXXXX)"; resp_file="$(mktemp -p "$STATE_DIR" resp.XXXXXX)"
    if ! code=$(curl -s -m 90 -u "$PORTAL_AUTH" -X POST -H 'Content-Type: application/json' \
                     -D "$hdr_file" -o "$resp_file" -w '%{http_code}' "$PORTAL_URL$path" --data "$body"); then
        # curl writes the HTTP code even when it then fails locally (a full disk under
        # -o/-D is the case seen here), so keep the two apart instead of appending to it.
        local curl_status=$?
        rm -f "$hdr_file" "$resp_file"
        die "$what failed: curl exited $curl_status (HTTP code reported: ${code:-none}). The
  request may well have succeeded; check the portal's xMB tab before retrying, and check
  for a full filesystem under $STATE_DIR."
    fi
    id=$(python3 -c '
import json,sys
try:
    b=json.load(open(sys.argv[1]))
except Exception:
    b={}
for k in ("id","serviceId","sessionId"):
    if isinstance(b,dict) and b.get(k):
        print(b[k]); sys.exit(0)
if isinstance(b,dict) and isinstance(b.get("service"),dict) and b["service"].get("id"):
    print(b["service"]["id"]); sys.exit(0)
' "$resp_file" 2>/dev/null)
    if [[ -z "$id" ]]; then
        id=$(grep -i '^location:' "$hdr_file" 2>/dev/null | tr -d '\r' | sed 's#.*/##')
    fi
    if [[ -n "$id" ]]; then
        rm -f "$hdr_file" "$resp_file"
        echo "$id"
        return 0
    fi
    local detail; detail=$(head -c 600 "$resp_file" 2>/dev/null)
    rm -f "$hdr_file" "$resp_file"
    die "$what failed (HTTP $code): ${detail:-no response body}"
}

# The channel line-up, as tab-separated rows so a caller can read it with `while IFS=$'\t' read`.
# channels.json is the single definition the encoders and the xMB provisioning step both work
# from, so an encoder and a session cannot disagree about which presentation a channel is.
#
# DEMO_CHANNELS limits both to the ids it names (space or comma separated), for a machine
# that cannot encode the whole line-up at once. Unset, the whole line-up runs.
#
#   channel_rows   every channel:  id  stream  source  type
#   onair_rows     onAir only:     id  name  stream  tmgiServiceId  tsi  mcastAddr  mcastPort
channel_rows() {
    python3 -c '
import json, os, sys
only = [x for x in os.environ.get("DEMO_CHANNELS", "").replace(",", " ").split() if x]
for c in json.load(open(sys.argv[1]))["channels"]:
    if only and c["id"] not in only: continue
    print("\t".join([c["id"], c["stream"], c["source"], c.get("type", "linear")]))
' "$CHANNELS_FILE"
}

onair_rows() {
    python3 -c '
import json, os, sys
only = [x for x in os.environ.get("DEMO_CHANNELS", "").replace(",", " ").split() if x]
for c in json.load(open(sys.argv[1]))["channels"]:
    if not c.get("onAir"): continue
    if only and c["id"] not in only: continue
    missing = [k for k in ("tmgiServiceId","tsi","mcastAddr","mcastPort") if k not in c]
    if missing:
        sys.exit("channels.json: %s is onAir but lacks %s" % (c["id"], ", ".join(missing)))
    print("\t".join([c["id"], c["name"], c["stream"],
                     str(c["tmgiServiceId"]), str(c["tsi"]), c["mcastAddr"], str(c["mcastPort"])]))
' "$CHANNELS_FILE"
}

# Prints the login for each web UI this demo starts that actually asks for one. The portal
# refuses to start without AUTH_TOKEN, so it always has one; rt-mbms-application only
# requires a login when WUI_AUTH_TOKEN is set in its own .env, and says so when it is not.
#
# These are lab credentials on a loopback-bound service, and a reader who cannot open the
# portal cannot drive the demo. Set SHOW_PORTAL_CREDENTIALS=0 to print the location of the
# secret instead of the secret, e.g. when the terminal is on a projector or in a recording.
# Same reasoning as print_portal_credentials: a reader who cannot open the CBC cannot send
# an alert. Honours SHOW_PORTAL_CREDENTIALS so one switch covers both consoles.
print_cbc_credentials() {
    [[ -f "$CBC_ENV" ]] || return 0
    cbc_creds
    if [[ "${SHOW_PORTAL_CREDENTIALS:-1}" == "1" ]]; then
        echo "  CBC login       : $CBC_USER / $CBC_TOKEN"
    else
        echo "  CBC login       : $CBC_USER / AUTH_TOKEN in $CBC_ENV"
    fi
}

print_portal_credentials() {
    portal_creds
    local app_env="$APP_DIR/.env" app_user app_token
    app_token="$(sed -n 's/^WUI_AUTH_TOKEN=//p' "$app_env" 2>/dev/null | tr -d '"'"'"'\"')"
    app_user="$(sed -n 's/^WUI_AUTH_USER=//p' "$app_env" 2>/dev/null | tr -d '"'"'"'\"')"
    if [[ "${SHOW_PORTAL_CREDENTIALS:-1}" == "1" ]]; then
        echo "  portal login    : $PORTAL_USER / $PORTAL_TOKEN"
        if [[ -n "$app_token" ]]; then
            echo "  player UI login : ${app_user:-admin} / $app_token"
        else
            echo "  player UI login : none required (set WUI_AUTH_TOKEN in $app_env to require one)"
        fi
    else
        echo "  portal login    : $PORTAL_USER / AUTH_TOKEN in $PORTAL_ENV"
        if [[ -n "$app_token" ]]; then
            echo "  player UI login : ${app_user:-admin} / WUI_AUTH_TOKEN in $app_env"
        else
            echo "  player UI login : none required"
        fi
    fi
}
