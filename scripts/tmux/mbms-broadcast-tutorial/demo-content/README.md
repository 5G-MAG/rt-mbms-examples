# demo-content — loadable xMB demo templates

Templates that broadcast a video source over MBMS, loaded into the running
portal via `load-demo.js`. Each template creates an xMB **service** and one or
more **sessions**; the BM-SC ingests the source, FLUTE-delivers it, and (once
you Activate) the MBMS-GW sets up the bearer so it goes over the air.

`rtve-24h.json` is a ready example: a live **HLS** stream via an **Application**
session in **Pull** mode (the BM-SC fetches the manifest + segments and re-polls
for new live segments).

## HTTPS / CDN sources need the local proxy

The BM-SC pulls with **libcurl + GnuTLS + nghttp2**. Modern CDNs (RTVE is on
Fastly) force **TLS 1.3 + HTTP/2**, and the BM-SC's GnuTLS handshake fails there
(`received handshake message out of context`, retried in a loop). So point the
template at the bundled **`hls-http-proxy.js`**, which does the HTTPS to the
origin itself and re-serves it to the BM-SC over plain HTTP:

```bash
node hls-http-proxy.js                 # default: rtvelivestream.rtve.es -> http://127.0.0.1:8888
# node hls-http-proxy.js <origin-host> <port>   # for another HTTPS origin
```

`rtve-24h.json` already uses `http://127.0.0.1:8888/rtvesec/24h/24h_main_dvr.m3u8`.
Start the proxy **before** you Activate. A source that is already plain HTTP (or
a TLS 1.2 / HTTP-1.1 origin) can be pulled directly and needs no proxy.

## Load a template

**From the portal (easiest):** open the **xMB tab** → **Load template…** → pick a
`.json` file here (e.g. `rtve-24h.json`). It creates the service and its
sessions and opens the service so you can review it.

**From the CLI (headless):**

```bash
cd demo-content
node load-demo.js rtve-24h.json
```

This reads the portal host/port and Basic-auth credentials from
`~/rt-mbms-application-provider/.env`.

Either way it **only creates** the service/session — it does **not** start
broadcasting. Open the xMB tab, review, and hit **Activate** to go live.

Prerequisites: the stack is up (`./transmit.sh`), the portal is reachable on
`:8080`, the BM-SC's xMB indicator is green (`:8543`), and — for an HTTPS/CDN
source like `rtve-24h.json` — `hls-http-proxy.js` is running (see above).

## What the fields mean

| Field | Meaning |
| --- | --- |
| `service.*` | The content service (name, class, announcement mode `SACH`). |
| `sessions[].sessionType` | `Application` for DASH/HLS. (`Files`, `Streaming`, `Transport-Mode` also exist.) |
| `application.ingestMode` | `Pull` (BM-SC fetches the URL) or `Push` (you push to a BM-SC-provided URL). |
| `application.applicationEntryPointURL` | The DASH/HLS manifest to broadcast. |
| `mbmsBearer.*` | The broadcast bearer: TMGI (`901`/`56` + service id), `mcastAddr` (M1-U, `239.255.0.1`), TSI, service-area-code, C-TEID, and `mmeSmPeers`. These must line up with your running MBMS-GW. |

## Make your own

Copy `rtve-24h.json`, then change `application.applicationEntryPointURL` to your
source and give it distinct `service.serviceNames` and
`mbmsBearer.tmgiServiceId`/`tsi` if you run several services at once.

## Note on content rights

`rtve-24h.json` points at a third-party (RTVE) stream — fine for a closed lab/RF
loopback test, but broadcasting third-party content beyond that needs rights
clearance. Swap in your own source for anything non-lab.
