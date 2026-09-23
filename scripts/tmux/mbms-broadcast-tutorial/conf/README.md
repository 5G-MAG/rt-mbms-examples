# conf/ — configuration for mbms-broadcast-tutorial.sh

This directory ships a **complete, working software-radio (ZeroMQ) config set**
for the whole stack, so the tutorial runs end-to-end with no SDR. All paths are
generic (logs go to `stdout`; the HSS DB and TLS certs are referenced
relative to this directory), so it is portable — no machine-specific paths.

The transmit/receive components run with this directory as their working
directory, so relative references (the eNB `[enb_files]` SIB/RR/RB includes,
`db_file`, the bmsc cert paths) resolve here.

## Files

| File | Component | Notes |
| --- | --- | --- |
| `epc.conf` | srsepc (EPC/MME) | `db_file = user_db.csv` (relative); log → `stdout` |
| `user_db.csv` | srsepc (HSS) | subscriber DB |
| `enb_baseline.conf` | srsenb (eNB) | ZeroMQ device; log → `stdout`; includes `sib.conf.mbsfn`, `rr.conf`, `rb.conf` |
| `sib.conf.mbsfn` | srsenb | SIB1/2/3 + SIB13 (MBSFN) |
| `rr.conf` | srsenb | radio resources |
| `rb.conf` | srsenb | SRB/DRB |
| `mbms-gw.conf` | mbms-gw | log → `stdout` |
| `bmsc.conf` | bmsc (BM-SC) | mTLS; cert paths relative to `conf/certs/` (see below); log → `stdout` |
| `modem_zmqtest.conf` | modem | ZeroMQ RX (`rx_port=tcp://127.0.0.1:2000`) |
| `client_recv.conf` | client | MBMS user-plane receiver |

The Application (`node app.js`) and Portal (`node --env-file=.env server.js`)
read their own `.env` from their repo roots. The **portal requires `AUTH_TOKEN`**
in `rt-mbms-application-provider/.env`.

## Generate the bmsc mTLS certificates (one-time)

`bmsc.conf` serves the xMB-C API over HTTPS with mutual TLS. Certificates are
not shipped; generate a self-signed set into `conf/certs/`:

```bash
mkdir -p conf/certs && cd conf/certs
# server (bmsc) key + cert
openssl req -x509 -newkey rsa:2048 -nodes -keyout server_key.pem \
  -out server_cert.pem -days 3650 -subj "/CN=localhost"
# client (portal / xMB client) key + cert
openssl req -x509 -newkey rsa:2048 -nodes -keyout client_key.pem \
  -out client_cert.pem -days 3650 -subj "/CN=xmb-client"
```

Then point the portal's xMB client at the same pair (in
`rt-mbms-application-provider/.env`): `XMB_CLIENT_CERT_PATH=.../client_cert.pem`,
`XMB_CLIENT_KEY_PATH=.../client_key.pem`, and either trust `server_cert.pem`
(`XMB_SERVER_CA_PATH`) or set `XMB_INSECURE_SKIP_SERVER_VERIFY=1` for the demo.

## Values that must stay consistent (if you edit the configs)

- **ZeroMQ I/Q link:** eNB `device_args` (`zmqtx`) and modem `rx_port` must use
  the same endpoint (`tcp://127.0.0.1:2000`). This is the virtual RF; no SDR.
- **PLMN (MCC/MNC):** identical in `epc.conf` and `enb_baseline.conf`.
- **S1AP / Sm addresses:** eNB → MME S1AP; `mbms-gw.conf` `mme_sm_peers` → MME Sm.
- **M1-U multicast:** `mbms-gw.conf` `m1u_multi_addr` (default `239.255.0.1`, GTP-U
  port `2153`) is the group the eNB joins.
- **Client interface:** `client_recv.conf` / the `-i` flag (`CLIENT_IFACE`) is the local
  interface IP carrying the recovered MBMS user plane.

For a real SDR instead of ZeroMQ, set the eNB `device_name`/`device_args` for
your radio and drop the modem `zmqrx` args; the launch script is unchanged.
