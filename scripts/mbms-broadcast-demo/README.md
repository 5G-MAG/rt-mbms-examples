# Tutorial: the whole MBMS Broadcast stack, with a local live origin

Runs the full LTE-based 5G Terrestrial Broadcast (FeMBMS / MBMS) reference stack from a cold
start: EPC, eNB, MBMS-GW, BM-SC, the portal and the Cell Broadcast Centre on the transmit side; a local content origin with
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
| `./08-start-alerts.sh` | Starts the Cell Broadcast Centre. Separate from the broadcast demo; needs only the transmit side |
| `./07-send-alert.sh` | Sends an ETWS/CMAS alert and waits for the modem to report it |
| `./stop-alerts.sh` | Stops the Cell Broadcast Centre, leaving the broadcast demo running |

`start-all.sh` clears anything already running first, so it is safe to run twice. Each numbered
script can also be run on its own (`./03-start-media-server.sh` to restart just the origin, say).

## Prerequisites

These scripts **run** a deployment; they build nothing. Everything below has to exist before
`./demo up` will get anywhere, and `./demo doctor` checks what it can and names what is missing.

### 1. System packages

The union of what the seven components ask for. Each repository's own README is authoritative for
its own list; this one exists so the whole job can be seen at once.

```bash
sudo apt install git build-essential cmake ninja-build pkg-config \
  libfftw3-dev libmbedtls-dev libboost-all-dev libconfig++-dev libsctp-dev libzmq3-dev \
  libspdlog-dev libcpprest-dev libssl-dev libwebsocketpp-dev libusb-1.0-0-dev \
  libgmime-3.0-dev libtinyxml2-dev libmicrohttpd-dev libgnutls28-dev libcurl4-gnutls-dev \
  libglibmm-2.4-dev libxml++-5.0-dev libsrtp2-dev \
  libsoapysdr-dev soapysdr-tools \
  libgps-dev \
  clang-tidy \
  nodejs npm python3 ffmpeg tmux iproute2 net-tools curl
```

Where the less obvious ones come from:

- `libzmq3-dev` is the software radio. Both `srsenb` in **rt-mbms-tx** and the SoapySDR bridge the
  modem receives through are built against it, and without it there is no radio here at all: this
  demo ships no SDR hardware path.
- `libmicrohttpd-dev`, `libgnutls28-dev`, `libcurl4-gnutls-dev`, `libglibmm-2.4-dev`,
  `libxml++-5.0-dev` and `libsrtp2-dev` are the **BM-SC**'s, which asks for each by name through
  `pkg_check_modules` in `bmsc/CMakeLists.txt` and aborts at configure time without them. Note
  `glibmm-2.4`, not the newer 2.68 the archive also carries, and `libxml++-5.0`, not 2.6.
- `libgmime-3.0-dev` and `libtinyxml2-dev` are the **client**'s: it parses the multipart service
  announcement with GMime and its XML fragments with TinyXML2.
- `libcpprest-dev` is the REST API in both the **modem** and the **client**.
- `libgps-dev` is the **modem**'s: `src/MeasurementFileWriter.h` includes `<libgpsmm.h>` to stamp
  measurement records with a GPS position, so the modem does not compile without it. Found by
  `check-build-from-clean.sh`; development machines had it installed already.
- `libsoapysdr-dev` and `soapysdr-tools` are needed to build and then verify the `zmqrx` bridge in
  step 3; `SoapySDRUtil` comes from the tools package.
- `clang-tidy` is not optional tooling for the **client**: its `CMakeLists.txt` sets
  `CMAKE_CXX_CLANG_TIDY` unconditionally, so CMake invokes it on every translation unit and the
  build stops with `Error running 'clang-tidy': no such file or directory` without it. The
  **modem** guards the same setting behind `find_program`, so it degrades quietly instead. Found by
  `check-build-from-clean.sh` on a stock image, having built fine on a development machine that
  happened to have it installed.

### 2. The components

Clone and build each of these. They are independent repositories with their own READMEs; the build
command is repeated here only so you can see the whole job at once. All eight are on 5G-MAG.

| Component | Repository | Build |
|---|---|---|
| EPC + eNB | [rt-mbms-tx](https://github.com/5G-MAG/rt-mbms-tx) | `cmake -S . -B build && cmake --build build -j$(nproc)` |
| MBMS-GW | [rt-mbms-gw](https://github.com/5G-MAG/rt-mbms-gw) | `git submodule update --init --recursive && cmake -S . -B build && cmake --build build -j$(nproc)` |
| BM-SC | [rt-mbms-bmsc](https://github.com/5G-MAG/rt-mbms-bmsc) | `git submodule update --init --recursive && cmake -S . -B build && cmake --build build -j$(nproc)` |
| Modem | [rt-mbms-modem](https://github.com/5G-MAG/rt-mbms-modem) | `git submodule update --init --recursive && cmake -S . -B build && cmake --build build -j$(nproc)` |
| Client | [rt-mbms-client](https://github.com/5G-MAG/rt-mbms-client) | `git submodule update --init --recursive && cmake -S . -B build && cmake --build build -j$(nproc)` |
| Application (player UI) | [rt-mbms-application](https://github.com/5G-MAG/rt-mbms-application) | `npm install` |
| Application Provider (portal) | [rt-mbms-application-provider](https://github.com/5G-MAG/rt-mbms-application-provider) | `npm install` |
| Cell Broadcast Centre (alerts) | [rt-pws-cbc](https://github.com/5G-MAG/rt-pws-cbc) | `npm install` |

Clone each with its submodules, or run `git submodule update --init --recursive` afterwards:
the BM-SC carries rt-libflute, libmpdpp and rt-mbms-tx, the client carries rt-libflute,
rt-common-shared and gzip-hpp, the MBMS-GW carries rt-mbms-tx, and the modem carries
rt-common-shared. A checkout without them fails at configure time complaining about a missing
subdirectory rather than about the submodule.

The modem's srsRAN is not a submodule: it is tracked in the repository, under `lib/srsran`. The
receiver's PHY changes (wideband PMCH, the 0.37 kHz numerology, the Rel-19 time-interleaving work)
live in those files, so they are versioned with the modem that depends on them rather than pinned
from elsewhere.

**Building on a machine with little memory:** the BM-SC and the client each compile large
translation units. `-j$(nproc)` on a 14 GB machine already running another demo was killed by the
kernel's OOM killer more than once during this work; `-j2` or `-j1` is slower and finishes.

### 3. The ZeroMQ software radio bridge

The eNB transmits I/Q over ZeroMQ and the modem receives through a SoapySDR module registering a
`zmqrx` driver. That module is not part of SoapySDR and not part of any component, so it ships
here and is built once:

```bash
../soapy-zmq-bridge/build.sh                         # -> libzmqrxSupport.so, found automatically
../soapy-zmq-bridge/build.sh --install               # or install it into SoapySDR's module path
SoapySDRUtil --info | grep "Available factories"     # must list zmqrx
```

`build.sh` alone is enough: the launchers look in `scripts/soapy-zmq-bridge` for the built module
before anywhere else. `--install` puts it on SoapySDR's own path and then checks that SoapySDR
really lists the factory, because a module that loads without registering looks exactly like a
missing one when the modem starts. `SOAPY_SDR_PLUGIN_PATH` overrides the lookup. None of this is
needed with a real SDR. What the bridge does and why it subscribes rather than requests is in
[its own README](../soapy-zmq-bridge/README.md) and the source header.

Without it the modem starts, finds no radio and never syncs, which presents as a receive chain that
comes up and delivers nothing.

Using real SDR hardware instead: set the eNB's `device_name`/`device_args` in
`conf/enb_baseline.conf`, drop the modem's `zmqrx` `device_args` in `conf/modem_zmqtest.conf`, and
no bridge is needed.

### 4. Configuration the scripts do not generate

- **The Cell Broadcast Centre's `.env`**, with `AUTH_TOKEN` set, if you want to send emergency
  alerts. `rt-pws-cbc` refuses to start without one, for the same reason the portal does. It is
  cloned next to `rt-mbms/` rather than inside it, because Public Warning System alerts are not an
  MBMS function: they reach handsets over the cell's own system information, with no MBMS bearer
  and no content session involved. `CBC_DIR` defaults to `$REPOS_ROOT/rt-pws-cbc`; point it
  elsewhere if you keep it somewhere else. Skip it and the broadcast demo is unaffected: nothing
  in `./start-all.sh` touches the alert path.
- **The portal's `.env`**, with `AUTH_TOKEN` set. `rt-mbms-application-provider` refuses to start
  without it. `start-all.sh` and `status.sh` read it and print the login, so the value belongs
  there and nowhere else; do not copy it into a tracked file.
- **The BM-SC's mTLS certificates**, one `openssl` block, described in
  [`conf/README.md`](../tmux/mbms-broadcast-tutorial/conf/README.md).
- **Passwordless or cached `sudo`**, for the EPC's TUN device and routing and for the receive
  side's network namespace. The scripts authenticate once up front.

### 5. Content

Optional. The encoder plays `~/MWC_TV_RADIO/TV_1.mp4` if it is there (`LIVE_SOURCE_MEDIA` in
`env.sh`, and `channels.json` for the line-up), and generates a test pattern with a tone if it is
not, so a fresh checkout runs with no content at all.

If the repositories are not under `$HOME/Repos`, set `REPOS_ROOT` at the top of `env.sh`;
every other path is derived from it.

### 6. Testing work that has not merged yet

Everything above describes the **stable** layout: the default branch of each repository. That is
deliberate, so these instructions stay correct once outstanding work merges. It also means that
while a change is still under review, the branch these instructions name is not the branch under
test, and the two will disagree.

To test unmerged work, check the relevant branch out in **every** repository that has it, not just
the one whose change you are interested in. The components are built against each other, and the
transmit and receive sides have to agree about the PMCH layout, so a mixture of branches produces
failures that read as defects in whichever component happens to break first.

Taking `feature/mbms-broadcast-demo` as the example:

```bash
for d in rt-mbms/rt-mbms-tx rt-mbms/rt-mbms-gw rt-mbms/rt-mbms-bmsc rt-mbms/rt-mbms-modem \
         rt-mbms/rt-mbms-client rt-mbms/rt-mbms-application rt-mbms/rt-mbms-application-provider \
         rt-mbms/rt-mbms-examples rt-pws-cbc; do
    git -C ~/Repos/"$d" checkout feature/mbms-broadcast-demo 2>/dev/null \
        && git -C ~/Repos/"$d" pull --recurse-submodules \
        && git -C ~/Repos/"$d" submodule update --init --recursive
done
```

The branch does not exist in every repository, which is why the checkout is allowed to fail: a
repository without it is used at its default branch. `check-build-from-clean.sh` resolves branches
the same way and prints which one it used for each component, so its output is the quickest way to
see what a given branch actually covers.

### Checking the build the way someone else will see it

Rebuilding on the machine that already runs the demo cannot answer "can anyone else build this":
every dependency is already installed there, so an incomplete package list or a stale instruction
is invisible. `scripts/check-build-from-clean.sh` answers it properly: fresh clones of all seven
components, a stock container image, and only the packages this README's `apt` line names, which it
reads out of this file rather than restating.

```bash
../check-build-from-clean.sh              # everything
../check-build-from-clean.sh --quick      # skip the two srsRAN-derived builds
```

It needs `docker` and returns non-zero on any new failure. It does not check the `zmqrx` bridge or
a running chain: both need a radio, `sudo` and a network namespace, which do not belong in a build
check.

## Running it

```bash
cd rt-mbms-examples/scripts/mbms-broadcast-demo
./start-all.sh
```

This runs, in order:

| Script | What it does |
|---|---|
| `01-start-transmit.sh` | EPC, eNB, MBMS-GW, BM-SC and the portal, via the tutorial's `transmit.sh`, then waits for each control port |
| `03-start-media-server.sh` | The local origin (`media-server.js`) and the looping encoder (`live-encoder.sh`), then waits until the presentation is genuinely playable |
| `05-start-client-and-app.sh` | Modem, client and application inside netns `mbms-rx`, via the tutorial's `receive-netns.sh`, then waits for both REST APIs |
| `06-provision-live-service.sh` | Creates the xMB service and its Application/Pull session and activates it, which is what puts the content on air |
| `08-start-alerts.sh` | Not part of `start-all.sh`: starts the Cell Broadcast Centre, the only thing the alert path adds |
| `07-send-alert.sh` | Not part of `start-all.sh`: sends an ETWS/CMAS alert and waits for the modem to report it |

The numbering is rt-mbs-examples', so the same stage carries the same number and the same name in
both demos and neither has to be learned twice. The gaps are the stages that demo has and this one
does not: `00-setup-netns.sh` (here the namespace is created by `receive-netns.sh`, which also
starts the receive side), `02` (that demo's MBSF/MBSTF, which MBMS has no counterpart to) and `04`
(its separate RAN stage, where here the eNB comes up with the rest of the transmit side).

The media-server step waiting is not politeness: the BM-SC's Pull ingest resolves the segment list from the
manifest once, at activation. A session activated against a manifest the encoder has not
populated yet comes up healthy and delivers nothing.

When it finishes:

- **player UI**: <http://10.80.0.2:3000/application> (the application, inside the namespace). The
  default player, hls.js, matches the default `LIVE_FORMAT=hls`.
- **cell broadcast page**: <http://10.80.0.2:3000/cellbroadcast>
- **Cell Broadcast Centre**: <http://127.0.0.1:8081>, the alert console. Behind Basic auth like
  the portal, and `start-all.sh`/`status.sh` print its login the same way.
- **portal**: <http://127.0.0.1:8080> (xMB and RAN tabs; alerts moved to the CBC above). It is behind Basic
  auth, and `start-all.sh` and `status.sh` both print the login, read from the portal's own
  `.env` so it cannot go stale. `SHOW_PORTAL_CREDENTIALS=0` prints where the secret lives
  instead of the secret itself, for a terminal that is being projected or recorded. The player
  UI has no login unless `WUI_AUTH_TOKEN` is set in `rt-mbms-application/.env`, and the same two
  scripts say which of the two is the case.
- **origin**: <http://127.0.0.1:3005/tv_1_live/manifest.mpd>, which a browser can play directly
  over unicast to separate an origin problem from a broadcast problem
- **modem API**: <http://10.80.0.2:3010/modem-api/>, **client API**: <http://10.80.0.2:3020/client-api/>

### What "it worked" looks like

**Seeing the video takes one manual step.** Open the player UI, paste the client's own
presentation URL into its **Manifest URL** box and press **Load**. That box starts empty and
`Load` does nothing until it has a URL, so a first run looks like a dead player when nothing is
actually wrong. `start-all.sh` prints the exact URL at the end of its output; it is the client
republishing what it received over the air:

```
http://10.80.0.2:3020/xmb-app-manifest-<session-id>/stream.m3u8
```

With it playing, the player's own metrics say whether the picture came over the air: **segment
source `5G-BC`** rather than unicast, a resolution of 640x360 for this demo's encode, and a
current time that advances in real time.

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

`MODEM_DIAG=1 ./05-start-client-and-app.sh` restarts just the receive side with the modem's
diagnostics on; the two config levels need a transmit-side restart (`./start-all.sh`).

## Emergency alerts

**They are a separate demo, with their own entry point.** A Public Warning System alert reaches
handsets over the cell's own system information (SIB10/11/12), signalled from the MME over SBc-AP.
It never touches an MBMS bearer, an xMB session, the content origin or the encoders. So the two
paths start independently, and either runs without the other:

```bash
# broadcast only -- no Cell Broadcast Centre is started
./start-all.sh

# alerts only -- transmit side, then the alert path. No origin, no encoders, no session.
./01-start-transmit.sh
./08-start-alerts.sh
./05-start-client-and-app.sh      # only if you want the modem to confirm arrival
./07-send-alert.sh etws_test

# both: the broadcast demo, then add the alert path on top at any time
./start-all.sh
./08-start-alerts.sh
./07-send-alert.sh etws_test
./stop-alerts.sh                  # take the alert path down again, demo keeps running
```

`./07-send-alert.sh` tells you to run `./08-start-alerts.sh` if the CBC is not up, rather than
failing on the request. `./status.sh` shows the alert path as `[--] not started` when it is
absent, because for a broadcast-only run that is the correct state and not a fault.

The modem only matters for *confirming* an alert: `./07-send-alert.sh` waits for it to report the
warning. Without it the alert is still transmitted, and the script says that nothing observed it.


Alerts are issued by `rt-pws-cbc`, a Cell Broadcast Centre, and not by the content portal: warning
origination and media provisioning are separate jobs, done by separate organisations. They do not
travel over the MBMS bearer either. They go over the cell's own warning signalling, so
they work whether or not a content session is active.

```bash
./07-send-alert.sh --list                       # the alert types the CBC accepts
./07-send-alert.sh etws_test                    # send one, then wait for the modem to report it
./07-send-alert.sh cmas_severe "Flood warning" "Move to higher ground."
./07-send-alert.sh --cancel                     # Stop Warning for the active alert
```

The script only reports success once the receiving modem actually lists the alert
(`/modem-api/etws_primary_alerts`, `etws_secondary_alerts`, `pws_alerts`), so a Cell Broadcast
Centre that accepted an alert that never reached the air is reported as a failure, not a success. The same
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

`06-provision-live-service.sh` also writes the session it provisions to
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
