# Tutorial: the whole MBMS Broadcast stack, with a local live origin

Runs the full LTE-based 5G Terrestrial Broadcast (FeMBMS / MBMS) reference stack from a cold
start: EPC, eNB, MBMS-GW, BM-SC and the portal on the transmit side; a local content origin with
a looping live encoder; and rt-mbms-modem, rt-mbms-client and rt-mbms-application on the receive
side, over a ZeroMQ software radio with no SDR hardware.

It is the LTE-broadcast counterpart of `rt-mbs-examples/scripts/mbs-broadcast-demo/`, and keeps
that demo's shape deliberately: one `env.sh` naming every path, port and identity, numbered
scripts that each do one stage and can be run on their own, `run/` holding all state, and
`start-all.sh` / `stop-all.sh` / `status.sh` over the top.

The difference from the older `scripts/tmux/mbms-broadcast-tutorial/` is what the content is, not
how the stack starts: this demo brings its own origin, so nothing is pulled from a third-party
CDN. That removes two problems at once. The BM-SC pulls with libcurl+GnuTLS and cannot always
complete a modern CDN's TLS 1.3 + HTTP/2 handshake (which is why that tutorial ships an HTTPS
proxy), and broadcasting someone else's stream raises a rights question a lab demo does not need
to raise. This demo drives those two launchers rather than reimplementing them, so there is only
one copy of the component start order, the sudo handling and the network namespace.

## Which script

| Script | What it does |
|---|---|
| `./start-all.sh` | Everything: transmit side, origin, receive side, then the xMB service, activated |
| `./status.sh` | What is up, plus what the radio and the provisioned session report |
| `./stop-all.sh` | Stops everything this demo started, encoder and namespace included |
| `./05-send-alert.sh` | Sends an ETWS/CMAS alert and waits for the modem to report it |

`start-all.sh` clears anything already running first, so it is safe to run twice. Each numbered
script can also be run on its own (`./02-start-media-server.sh` to restart just the origin, say).

## Prerequisites

- The stack already builds. These scripts run what is built; they build nothing.
- `node`, `ffmpeg`, `python3`, `curl`, and passwordless or cached `sudo` (srsepc needs root for
  its TUN device and routing; the receive side needs a network namespace).
- The ZeroMQ software radio needs a SoapySDR `zmqrx` bridge, which is not shipped anywhere:
  build it from the source in
  [`../tmux/mbms-broadcast-tutorial/README.md`](../tmux/mbms-broadcast-tutorial/README.md#zeromq-software-radio).
  `SoapySDRUtil --info | grep "Available factories"` must list `zmqrx`.
- `rt-mbms-application-provider/.env` with `AUTH_TOKEN` set. The portal refuses to start without
  it, and these scripts read the host, port and token from that same file so they cannot disagree
  with the portal about its own credentials.
- The BM-SC's mTLS certs, as `../tmux/mbms-broadcast-tutorial/conf/README.md` describes.
- Content is optional. The encoder plays `~/MWC_TV_RADIO/TV_1.mp4` if it is there
  (`LIVE_SOURCE_MEDIA`), and generates a test pattern with a tone if it is not, so a fresh
  checkout runs with no content at all.

If the repositories are not under `$HOME/Repos`, set `REPOS_ROOT` at the top of `env.sh`;
everything else is derived from it.

## Running it

```bash
cd rt-mbms-examples/scripts/mbms-broadcast-demo
./start-all.sh
```

This runs, in order:

| # | Script | What it does |
|---|---|---|
| 01 | `01-start-transmit.sh` | EPC, eNB, MBMS-GW, BM-SC and the portal, via the tutorial's `transmit.sh`, then waits for each control port |
| 02 | `02-start-media-server.sh` | The local origin (`media-server.js`) and the looping encoder (`live-encoder.sh`), then waits until the presentation is genuinely playable |
| 03 | `03-start-receive.sh` | Modem, client and application inside netns `mbms-rx`, via the tutorial's `receive-netns.sh`, then waits for both REST APIs |
| 04 | `04-provision-live-service.sh` | Creates the xMB service and its Application/Pull session and activates it, which is what puts the content on air |

Step 02 waiting is not politeness: the BM-SC's Pull ingest resolves the segment list from the
manifest once, at activation. A session activated against a manifest the encoder has not
populated yet comes up healthy and delivers nothing.

When it finishes:

- **player UI**: <http://10.80.0.2:3000/application> (the application, inside the namespace). The
  default player, hls.js, matches the default `LIVE_FORMAT=hls`.
- **cell broadcast page**: <http://10.80.0.2:3000/cellbroadcast>
- **portal**: <http://127.0.0.1:8080> (xMB, RAN and Emergency Alerts tabs). It is behind Basic
  auth, and `start-all.sh` and `status.sh` both print the login, read from the portal's own
  `.env` so it cannot go stale. `SHOW_PORTAL_CREDENTIALS=0` prints where the secret lives
  instead of the secret itself, for a terminal that is being projected or recorded. The player
  UI has no login unless `WUI_AUTH_TOKEN` is set in `rt-mbms-application/.env`, and the same two
  scripts say which of the two is the case.
- **origin**: <http://127.0.0.1:3005/tv_1_live/manifest.mpd>, which a browser can play directly
  over unicast to separate an origin problem from a broadcast problem
- **modem API**: <http://10.80.0.2:3010/modem-api/>, **client API**: <http://10.80.0.2:3020/client-api/>

### What "it worked" looks like

Transmit-side logs are in `run/logs/`; the receive side keeps its own in
`~/.local/state/mbms-broadcast-tutorial/` (that is `receive-netns.sh`'s own location, which it
fixes internally).

| Check | Where | Healthy |
|---|---|---|
| Content is reaching the receiver | `curl -s http://10.80.0.2:3020/client-api/files` | segments listed, newest only seconds old |
| The modem is synced and sees both PMCHs | `curl -s http://10.80.0.2:3010/modem-api/sib_info` | `mcch.pmch_list` has two entries |
| The content PMCH is decoding | `curl -s http://10.80.0.2:3010/modem-api/mch_status/1` | `present: true`, `bler: 0` |
| The session is on air | `./status.sh` | session state `Active` |
| The client learned the service | `curl -s http://10.80.0.2:3020/client-api/services` | one HLS service with its `flute_info` |
| Content is arriving over the air | `curl -s http://10.80.0.2:3020/client-api/files` | segments listed with `"source": "5G-BC"`, growing |
| No segments are being lost | `grep "has been received" ~/.local/state/mbms-broadcast-tutorial/Client.log \| grep -oE 'segment-[0-9]+' \| sed 's/[^0-9]//g' \| sort -n \| uniq \| awk 'NR>1 && $1!=p+1{print "gap",p,$1} {p=$1}'` | prints nothing |
| The player has real buffer | `curl -s http://10.80.0.2:3020/xmb-app-manifest-sess-1/stream.m3u8 \| grep -c '.ts'` | steady at about 10, not 2 |
| What the player will fetch | `curl -s http://10.80.0.2:3020/manifest.m3u8` | a master playlist naming a variant, not a `.ts` |

`evm` in `mch_status` can freeze at a stale, alarming value while `bler` is genuinely 0 and
content is flowing: trust `bler`.

### Logging, and how to get the packet-level view back

Everything here logs at a level that can be left running: about 25 MB/hour in total across the
stack. Three settings were turned down to get there, and each is worth knowing about when a
problem needs more detail than the checks above give:

| Setting | Where | What it costs when on |
|---|---|---|
| `MODEM_DIAG=1` | environment, read by `receive-netns.sh` | The modem's per-subframe diagnostics (`MCHDIAG`, `TI_DIAG_*`, ...). 353 KB/s measured, 4.25 GB in a 3h16m run. Needed for the MCH/TI path |
| `all_level = debug` | `../tmux/mbms-broadcast-tutorial/conf/mbms-gw.conf` | One line per forwarded M1-U packet, ~13 MB/hour. This is how you count what the MBMS-GW put on each bearer, by C-TEID |
| `gtpu_level = debug` | `../tmux/mbms-broadcast-tutorial/conf/enb_baseline.conf` | One line per M1-U packet received, ~12 MB/hour. Together with the line above it places a loss between the BM-SC, the MBMS-GW and the eNB, by comparing byte totals per minute |

`MODEM_DIAG=1 ./03-start-receive.sh` restarts just the receive side with the modem's
diagnostics on; the two config levels need a transmit-side restart (`./start-all.sh`).

## Emergency alerts

Alerts do not travel over the MBMS bearer. They go over the cell's own warning signalling, so
they work whether or not a content session is active.

```bash
./05-send-alert.sh --list                       # the alert types the portal accepts
./05-send-alert.sh etws_test                    # send one, then wait for the modem to report it
./05-send-alert.sh cmas_severe "Flood warning" "Move to higher ground."
./05-send-alert.sh --cancel                     # Stop Warning for the active alert
```

The script only reports success once the receiving modem actually lists the alert
(`/modem-api/etws_primary_alerts`, `etws_secondary_alerts`, `pws_alerts`), so a portal that
accepted an alert that never reached the air is reported as a failure, not a success. The same
alert appears on the application's cell broadcast page. SIB-carried warning fields propagate
noticeably slower than MCCH ones, so allow tens of seconds.

## Configuration

Everything is in `env.sh`. The values worth knowing:

| Variable | Default | Why it is what it is |
|---|---|---|
| `LIVE_FORMAT` | `hls` | Only HLS completes the chain today; see the limitation below. The BM-SC ingests either and the application plays either |
| `LIVE_SOURCE_MEDIA` | `~/MWC_TV_RADIO/TV_1.mp4` | Looped forever by the encoder. Missing is fine: a test pattern is generated instead |
| `MEDIA_PORT` | `3005` | Not 3004, so this origin and `rt-mbs-examples`' own media server can both be up on one host |
| `DEMO_C_TEID` | `52428` (`0xCCCC`) | Not free: `enb_baseline.conf` assigns `0xbbbb` to PMCH0 (MCCH and the SACH) and `0xCCCC` to PMCH1 (content). Changing it without changing `pmch1.session_teids` puts the session on no PMCH at all |
| `DEMO_TMGI_SERVICE_ID` | `21` | Free on this rig: 16 is the BM-SC's own built-in content session, 17 and 20 belong to the two demo-content templates, 0 is the SACH |
| `DEMO_FEC_ENABLED` | `true` | Required for continuous playback here, see below. The scheme itself is the BM-SC's choice (`xmb.content_fec_scheme`, set to `raptor`); this is only the per-session toggle of TS 26.348 Table 5.4-1 |

`04-provision-live-service.sh` also writes the session it provisions to
`run/state/local-live.json`, in the same template format the portal's own "Load template..."
button and `demo-content/load-demo.js` accept. So the exact session this demo creates can be
loaded by hand from the UI, and a variant can be built by editing `env.sh` and re-running the
script rather than hand-editing JSON.

## Troubleshooting

- **Activation rejected as "already active"**: a session from a previous run is still registered
  at the eNB/MBMS-GW while the BM-SC's own bookkeeping is behind. Retrying activate does not fix
  it. `./stop-all.sh` then `./start-all.sh` does, which is why `start-all.sh` restarts the
  transmit side rather than reusing it. The full explanation is in the tutorial's
  [`DEMO_RUNBOOK.md`](../tmux/mbms-broadcast-tutorial/DEMO_RUNBOOK.md).
- **The modem's TUN device never appears**: this host's bandwidth-blind cell search is slow, 50
  to 90 seconds is normal. `receive-netns.sh` waits; if it gives up, check
  `~/.local/state/mbms-broadcast-tutorial/Modem.log` and that `zmqrx` is installed.
- **Nothing plays but `bler` is 0**: the radio is fine and the problem is above it. Check the
  origin directly (`curl http://127.0.0.1:3005/tv_1_live/manifest.mpd`), then
  `run/logs/BM-SC.log` for ingest, then the client's log for objects arriving.
- **`Address already in use` on the S1-MME socket**: a leftover root `srsepc`. `transmit.sh`
  clears one on start, and `sudo pkill -x srsepc` clears it by hand.
- **The modem stops decoding MCCH and its log shows `SYNC_OFFSET_DIAG SLOWCALL`**: the software
  radio is not getting the CPU it needs, not a radio fault. The encode defaults here are cheap for
  this reason, but the whole box is shared: another demo's encoders (rt-mbs-examples runs its own)
  or a parallel build will do it. Check with `uptime` and `ps -eo pid,pcpu,comm --sort=-pcpu`.
- **Restarting the encoder while a session is active** breaks that session's ingest: the BM-SC
  re-polls the manifest and gets a half-written file (`Start tag expected, '<' not found` in
  `run/logs/BM-SC.log`), and content stops. Re-run `./start-all.sh` rather than restarting the
  encoder alone.

## Why FEC is on by default

Broadcast has no retransmission, so one lost packet destroys the whole object it belonged to. A
~250 KB HLS segment spans roughly 180 packets and needs every one of them.

Measured on this rig with FEC off: a packet loss small enough to leave `mch_status` reporting
BLER 0 still cost **6.7% of segments** (360 of 5382 over six hours). That is worse than it sounds,
because rt-mbms-client publishes only the contiguous run of segments it actually holds, stopping at
the first hole (`HlsMediaPlaylistRewriter.cpp`, so the player is never offered a segment the client
cannot serve). One hole every ten-or-so segments truncated the player's playlist to **two entries**,
about 8 seconds of media, and playback stalled repeatedly.

With `xmb.content_fec_scheme = raptor` and the session's `fecEnabled` set, measured over the same
chain: **0 of 107 segments lost**, playlist depth steady at 9-10 segments, every segment offered to
the player returning 200, while the radio's own BLER was non-zero (~0.002) the whole time, i.e. the
repair symbols were doing work rather than the link having quietly got better.

Turn FEC off with `DEMO_FEC_ENABLED=false` to see the unprotected behaviour; it costs bearer
capacity, which is the trade being made.

## Known limitation: DASH is announced but not playable

With `LIVE_FORMAT=dash` the whole chain runs and the content genuinely reaches the receiver, but no
playable service appears. The two ends disagree about how a DASH presentation is announced:

- rt-mbms-bmsc, for DASH, references the MPD through one `r9:mediaPresentationDescription` and
  deliberately omits `r12:appService` (its `service_manager.cc` cites TS 26.346 Annex L.2.5 for
  this, and emits `r12:appService` for HLS instead).
- rt-mbms-client builds a content stream only from `r12:appService`
  (`ServiceAnnouncement.cpp`), so for a DASH service it registers the service and stops there.

Confirmed live: with DASH the SA bundle arrives and lists the service and its MPD, the modem
decodes the content PMCH with BLER 0, and `client-api/services` stays empty. Which side is wrong
is a specification question that needs TS 26.346 Annex L.2.5 in front of you; it has not been
settled here, so nothing has been changed on either side and this demo defaults to HLS.

## What this does not cover

- Multicast (MBMS broadcast only) and unicast fallback.
- Time Interleaving and CAS muting scenarios. Those are the tutorial's
  [`DEMO_RUNBOOK.md`](../tmux/mbms-broadcast-tutorial/DEMO_RUNBOOK.md), which changes two config
  files between runs and has a known ordering constraint (never activate a fresh session with TI
  already on).
- Real SDR hardware. Set the eNB's `device_name`/`device_args` and drop the modem's `zmqrx`
  `device_args` for that; no bridge is then needed.
