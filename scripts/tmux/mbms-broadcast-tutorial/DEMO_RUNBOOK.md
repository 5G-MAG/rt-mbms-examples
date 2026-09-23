# Demo runbook: 8 MHz dedicated-mode FeMBMS, TI, CAS muting + alerts

Three scenarios, meant to be run one after another. Each one only changes a
couple of lines in `conf/enb_baseline.conf` / `conf/modem_zmqtest.conf` on top
of the same base setup, so you tear down, edit, and relaunch between them.

Portal AUTH_TOKEN (Basic auth, user `admin`) for the API calls below:
`AUTH_TOKEN from rt-mbms-application-provider/.env (not tracked)` (from `rt-mbms-application-provider/.env`).

---

## 0. Prerequisites (once, before any scenario)

- `bmsc.conf`'s `[content_session]` must stay `enable = false` (it was disabled
  because its hardcoded TMGI service-id 16 collides with the demo template's
  own session -- leave it off for all three scenarios).
- The HLS proxy is needed for scenarios 1 and 2 only (RTVE's CDN needs TLS1.3/
  HTTP2 that this BM-SC's libcurl can't always negotiate directly):
  ```bash
  cd /home/fivegmag/Repos/rt-mbms/rt-mbms-examples/scripts/tmux/mbms-broadcast-tutorial/demo-content
  node hls-http-proxy.js &
  disown
  ```
  Check it's up: `curl -I http://127.0.0.1:8888/rtvesec/24h/24h_main_dvr.m3u8`

---

## Scenario 1 -- 8 MHz dedicated mode, video, no TI

### Config (already the current state of the two files)

`conf/enb_baseline.conf` `[embms]`:
```
mbms_dedicated = true
pmch_bandwidth = 40
time_interleaving_n = 0      # PMCH0 (SACH) -- untouched
nof_pmch = 2
session_teids = 0xbbbb
pmch1.session_teids = 0xCCCC
```
`conf/modem_zmqtest.conf` `phy:` block:
```
expect_mixed_cell = false
mbsfn_prb_test_override = 40
```
`demo-content/rtve-24h.json`: `tmgiServiceId: 17`, `cTeid: 52428` (already set).

### Launch

```bash
cd /home/fivegmag/Repos/rt-mbms/rt-mbms-examples/scripts/tmux/mbms-broadcast-tutorial
./transmit.sh                       # EPC, eNB, MBMS-GW, BM-SC, Portal
sudo ./receive-netns.sh start       # Modem, Client, Application (WUI)
```
Wait for `mbms_modem_tun up, ...` in the output (can take ~30-60s on this
host -- bandwidth-blind cell search). If it warns "never appeared", the modem
may still be mid-sync; wait another 30s then check
`sudo ip netns exec mbms-rx ip link show mbms_modem_tun`.

### Load + activate the video (via the portal UI)

1. Open **http://127.0.0.1:8080** (Basic auth: `admin` / the token above).
2. **xMB tab** -> **Load template...** -> pick
   `demo-content/rtve-24h.json`.
3. Review the created service/session, then hit **Activate**.
   - If you get `"mbms-gw rejected START: ... session already active"`, the
     session's own state got stuck (usually from a previous run): toggle it
     to **Idle** then back to **Active** once more -- this forces a fresh
     manifest re-fetch/re-announce, which is also needed if the proxy wasn't
     up yet on the first activation attempt.

### Visualize

- **Player UI**: http://10.80.0.2:3000 -- should start playing the RTVE feed.
- **Modem API** sanity checks:
  ```bash
  curl -s http://10.80.0.2:3010/modem-api/sib_info | python3 -m json.tool
  curl -s http://10.80.0.2:3010/modem-api/mch_status/1   # BLER should be 0
  ```
  Look for `mib.mbms_dedicated: true`, `sib13.areas[0].pmch_bandwidth: 40`,
  `subcarrier_spacing_khz: 1.25`.

---

## Scenario 2 -- same, with Time Interleaving

**Do NOT bake TI into the static config and restart the whole stack with it
already active.** Confirmed live 2026-08-05: activating a *fresh* xMB session
while TI is already on delivers exactly one file (the manifest) and then
stalls forever -- real BLER stays healthy the whole time, so this only shows
up by actually checking segment delivery, not by checking `mch_status`. The
session state gets fully wedged; the fix is a full stack restart, not a
retry.

**The procedure that reliably works instead: run Scenario 1 first (no TI),
confirm the video is actually playing, THEN turn TI on live via the portal
on top of the already-flowing session.** Confirmed live and sustained (22+
segments over 70+ seconds, real BLER ~0-3%) that content already in flight
survives a live TI toggle cleanly -- this bug is specifically about a *fresh*
activation starting under TI, not about TI itself breaking delivery.

### Steps

1. Do all of **Scenario 1** first, exactly as written, through "confirm it's
   playing" at http://10.80.0.2:3000. Don't skip the visual confirmation --
   you need content genuinely flowing before the next step.
2. Open the portal's **RAN tab** -> "Time Interleaving (PMCH1 / Content)"
   card (added 2026-08-05 -- it used to only expose the flat/PMCH0 fields,
   which are useless once `nof_pmch=2`: TS 36.300 SS15.3.3 forbids TI on the
   PMCH carrying MCCH, so the eNB silently zeroed those back out before this
   fix). Set N=2, M=4, hit **Apply**.
3. Confirm TI took effect on air:
   ```bash
   curl -s http://10.80.0.2:3010/modem-api/sib_info | python3 -c \
     "import json,sys; d=json.load(sys.stdin); print(d['mcch']['pmch_list'])"
   ```
4. Confirm the video is *still playing* (not just that TI is signalled) --
   this is the whole point of doing it in this order.

**Stick to N=2/M=4** -- see Troubleshooting for why other combinations are
currently unsafe on this rig (a separate, deeper PHY bug, not this one).
Look for `time_interleaving_n: 2, time_interleaving_m: 4` on PMCH1's entry
(not PMCH0's). This can take longer than usual to show up after activation
(MCCH's own modification-period cycle) -- give it ~10-15s.

---

## Scenario 3 -- CAS muting, no video, ETWS/CMAS alerts only

### Teardown scenario 2 first (same as above)

### Config change

In `conf/enb_baseline.conf`'s `[embms]` section, drop the
`pmch1.time_interleaving_*` lines again (or leave them -- harmless with no
PMCH1 content, but cleaner to remove) and add:
```
cas_muting = true
k_cas = 8
n_cas = 4
```
(This is the exact combo already validated together with a real PMCH1 session
in prior testing -- safe to trust.) Leave `mbms_dedicated=true` /
`pmch_bandwidth=40` as-is; muting is independent of PMCH width.

### Launch -- no video this time

```bash
cd /home/fivegmag/Repos/rt-mbms/rt-mbms-examples/scripts/tmux/mbms-broadcast-tutorial
./transmit.sh
sudo ./receive-netns.sh start
```
**Do not** load/activate `rtve-24h.json` this time -- skip straight to alerts.
(No proxy needed either, for the same reason.)

### Send an ETWS/CMAS alert (portal UI)

1. Open **http://127.0.0.1:8080**, go to the **Emergency Alerts** tab.
2. Pick an alert type (e.g. `ETWS: Earthquake`, or a CMAS one), fill in a
   headline/description, hit **Send Alert**.
3. **Cancel Active Alert** is available afterward to send a Stop-Warning if
   you want to demo that too.

Available alert types (`lib/cap.js`):
`etws_earthquake`, `etws_tsunami`, `etws_earthquake_tsunami`, `etws_test`,
`etws_other`, `cmas_presidential`, `cmas_extreme`, `cmas_severe`, `cmas_amber`.

### Visualize

- **Application WUI's Cell Broadcast page**: http://10.80.0.2:3000/cellbroadcast
  -- the received ETWS/CMAS message should appear here.
- Confirm CAS muting itself is live via the modem API:
  ```bash
  curl -s http://10.80.0.2:3010/modem-api/sib_info | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d['sib1'])"
  ```
  Look for `cas_muting_enabled: true, k_cas: 8, n_cas: 4`. Note: SIB1 fields
  propagate noticeably slower than MCCH ones after a config change -- prior
  testing saw ~25s from activation to confirmed decode, vs ~7-8s for MCCH.

---

## Quick troubleshooting reference

- **`mbms_modem_tun` never appears in time**: this host's CPU is slow for the
  bandwidth-blind cell search; just wait longer (up to ~90s) before assuming
  failure.
- **xMB activation rejected as "already active"**: try the Idle -> Active
  toggle first (works if BM-SC's own state actually reached Active before,
  e.g. after a proxy hiccup). **If the Idle -> Active toggle *also* fails
  with the same "already active" error**, that means the bearer genuinely
  started at the eNB/mbms-gw level on a previous attempt while BM-SC's own
  bookkeeping never got past "Announced" (`update_session()` only issues a
  real STOP to mbms-gw if BM-SC's *own* state was already `active` -- from
  `Announced`, an Idle PUT is a silent no-op, so mbms-gw is never told to
  stop and correctly refuses the next START). The only reliable fix: a full
  transmit-side restart (`./transmit.sh --stop` then `./transmit.sh`,
  followed by `sudo ./receive-netns.sh stop`/`start` since the eNB restart
  drops the modem's sync too), then load + activate **once** -- don't retry
  activate repeatedly against the same stuck session, each attempt just adds
  to the confusion.
- **Manifest never reaches the client (scenario 1/2) after a proxy restart**:
  same Idle -> Active toggle -- the announcement only refreshes on a state
  transition, not on every background repoll.
- **Full stack log locations**: `~/.local/state/mbms-broadcast-tutorial/*.log`
  (EPC, eNB, MBMS-GW, BM-SC, Portal, Modem, Client, Application).

## Time Interleaving: what's fixed, and the one remaining known limitation (2026-08-05)

Three real, distinct bugs found investigating "Apply does nothing" / "degrades
after several changes":

1. **Portal never exposed PMCH1's own TI fields.** The eNB's control socket
   has supported `embms.pmch1.time_interleaving_n/m` for a while, but
   `lib/fields.js`/`public/ran.js` only ever whitelisted the flat/PMCH0 keys --
   which are structurally useless once `nof_pmch=2` (TS 36.300 SS15.3.3: a
   TI'd MCH can't carry MCCH, so the eNB correctly zeroes PMCH0's TI back out
   whenever a second PMCH exists). **Fixed**: added the PMCH1 fields to both
   files, new "Time Interleaving (PMCH1 / Content)" RAN-tab card.
2. **Modem softbuffer staleness across a live TI change** (`rt-mbms-modem`,
   `MbsfnFrameProcessor.cpp`): a live N/M change redefines which slot a given
   subframe maps to, but the per-slot softbuffer state from the OLD mapping
   was never flushed -- confirmed live as real BLER-collapsing corruption, not
   just cosmetic staleness. **Fixed**: detect the change, reset all slots.
3. **`main.cpp`'s gap-detection dead for PMCH1** (`rt-mbms-modem`): the
   worker-pinning gap check that prevents `mb_idx` from spuriously rotating
   mid-block hardcoded `pmch_info_list[0]` (PMCH0) -- since PMCH0's own TI is
   always disabled (see #1), this check could never actually fire for PMCH1,
   silently unprotected for the one PMCH that needs it. **Fixed**: index by
   the subframe's real `pmch_idx`; also added an immediate `mb_idx` realignment
   on a detected live change instead of waiting for the new block boundary to
   naturally arrive.

**What these three fixes actually resolve**: `n=2/m=4` is now confirmed clean
and stable at the *radio* layer -- both from a cold start and as a single
live Apply from the RAN tab -- with real BLER 0 sustained over 30+ seconds.

**What's still open #1, found along the way but NOT fixed**: wider TI settings
(confirmed with `n=8/m=8`) show persistent BLER ~100% **even baked into the
static config from a cold start with zero live reconfiguration involved** --
proving it's a separate, standalone PHY-level decode bug, unrelated to
anything above. Traced deep enough to rule out a TX/RX parameter mismatch
(every rate-matching input -- TBS, e_min, n_cb_cap, codeblock sizes -- is
byte-for-byte identical between the eNB and modem's independent copies of
`pmch.c`, confirmed via matched runtime diagnostics) and to rule out a
TBS-rounding table mismatch (also identical). The remaining suspect is the
turbo-code's own hand-reverse-engineered tail-bit interleaving logic in
`rm_turbo.c`, which the code's own comments admit was only ever validated for
`rv_idx 0..3` (N=4) -- our failing case doubles that to `rv_idx 0..7`. Not
fixed; would need dedicated bit-exact trace capture (the codebase's existing
`PMCH_RE_DUMP` tooling could help) to resolve properly, not something to
attempt blind under demo time pressure. **Don't use N/M values other than
2/4 for now.**

**What's still open #2, found later the same day**: activating a **fresh**
xMB session while TI is *already* active (e.g. a full stack restart with TI
baked into the static config) delivers exactly one file -- the HLS
manifest -- and then silently stalls forever, with **real radio BLER staying
perfectly healthy the whole time** (this only shows up if you actually check
for `.ts` segments arriving, not by checking `mch_status`). Confirmed this is
specific to the *fresh-activation-under-TI* case, not TI itself: disabling
TI on the same stuck session immediately resumed continuous segment
delivery, and re-enabling TI on that now-flowing session did **not** break
it again (22+ segments sustained over 70+ seconds with TI back on). **The
safe procedure is in Scenario 2 above**: get video flowing with TI off first
(Scenario 1), then turn TI on live -- never start a session fresh with TI
already on. Root cause not identified (something in sustained multi-file
FLUTE delivery specifically breaks when TI is live from a session's first
activation; a single "FLUTE: source block number N out of range" client-side
error was observed once during this investigation but did not reproduce
consistently, so it may or may not be related).

One more thing worth knowing: `evm` in `mch_status` can freeze at a stale,
alarming-looking value (observed: ~0.9) even while `bler:0` is genuinely
correct and content is flowing fine -- a known, separate, already-documented
measurement artifact ("degenerate equalizer denominator" case). **Trust
`bler`, not `evm`**, when judging whether TI is actually working.
