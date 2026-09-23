# mbms-broadcast-tutorial

Launches a complete **LTE-based 5G Terrestrial Broadcast (FeMBMS / MBMS)**
reference deployment in one `tmux` session, one window per function, with
per-window logging. It is the LTE-broadcast analogue of
`5G-MAG/rt-mbs-examples`' `mbs-function-tutorial.sh`.

By default it runs the **software-radio (ZeroMQ)** end-to-end setup, so no SDR
hardware is required.

**New to this lab?** [`TUTORIAL.html`](TUTORIAL.html) in this same directory is a
full from-scratch walkthrough -- all ten repos, build order, the `zmqrx` bridge
source in full, every config step, and every gotcha actually hit standing this up.
Open it in a browser. This README below assumes the stack already builds and
covers day-to-day usage of the launcher scripts themselves.

## What it starts (in dependency order)

```
Transmit:  srsepc (EPC/MME) ─▶ srsenb (eNB) ─▶ mbms-gw ─▶ bmsc (BM-SC)
                                   │ ZMQ I/Q (tcp://127.0.0.1:2000)
Receive:   modem  ◀──────────────┘   ─▶ client (mw) ─▶ application (web UI, :3000)
Control:   application-provider (portal, :8080)
```

| Window | Binary | Default port(s) |
| --- | --- | --- |
| EPC | `rt-mbms-tx/build/srsepc/src/srsepc` | S1AP 36412, Sm 2123, SBc bridge 2102 |
| eNB | `rt-mbms-tx/build/srsenb/src/srsenb` | control 2100 |
| MBMS-GW | `rt-mbms-gw/build/mbms-gw/mbms-gw` | control 2101, M1-U 2153 |
| BM-SC | `rt-mbms-bmsc/build/bmsc/bmsc` | xMB-C 8543 |
| Modem | `rt-mbms-modem/build/modem` | REST 3010 |
| Client | `rt-mbms-client/build/client` | REST 3020 |
| Application | `rt-mbms-application` (`node app.js`) | 3000 |
| Portal | `rt-mbms-application-provider` (`node --env-file=.env server.js`) | 8080 |

## Prerequisites

- `tmux`, `node`, and coreutils `stdbuf` on `PATH`.
- All seven components built (see each repo's README).
- For the ZeroMQ software-radio path, a SoapySDR `zmqrx` bridge that **you build
  yourself** — it is intentionally not shipped with this tutorial. See
  [ZeroMQ software radio](#zeromq-software-radio) below. Not needed for a real SDR.
- A complete, generic ZeroMQ config set already ships in `./conf/` — the only
  thing to create is the bmsc mTLS certs (one `openssl` block); see
  [`conf/README.md`](conf/README.md).
- `rt-mbms-application-provider/.env` with `AUTH_TOKEN` set (the portal refuses
  to start without it).

## Usage

Two ways to bring the stack up:

**A. Background launcher (simplest, no tmux) — transmit side only, good for a demo:**

```bash
./transmit.sh             # start EPC + eNB + MBMS-GW + BM-SC + Portal in the background
./transmit.sh --stop      # stop everything it started (incl. the root srsepc)
sudo ./receive-netns.sh start   # modem + client + application, in their own netns (see below)
```

Each component runs backgrounded with its own log under
`~/.local/state/mbms-broadcast-tutorial/<Name>.log`. It authenticates sudo once
(for the EPC), clears a leftover `srsepc`, and warns on already-bound ports.
`transmit.sh` only runs the transmit side — see
[Full chain on one host](#full-chain-on-one-host-receive-side-in-a-network-namespace)
below for the receive side, which always needs its own network namespace on a
single-host demo (UDP `:2153` would otherwise collide with the eNB's own
receiver).

**B. tmux tutorial (one visible window per function, whole chain in one namespace)** — needs `tmux`
(`sudo apt install tmux`):

```bash
./mbms-broadcast-tutorial.sh          # launch all functions in tmux and attach
./mbms-broadcast-tutorial.sh --kill   # tear the session down (incl. the root srsepc)
```

Both share the same `conf/` and defaults. On launch each runs two preflight guards so re-runs stay clean:
- It stops a **leftover `srsepc` (EPC/MME)** first. `srsepc` runs as root, so a
  `tmux kill-session` can't stop it, and a stale instance is the usual cause of
  `bind(): Address already in use` on the S1-MME socket. `--kill` stops it too.
- It **warns** if any stack port (2100/2101/3000/3010/3020/8080/8543) is already
  bound by a leftover component, before launching.

Inside tmux: `Ctrl-b n/p` next/prev window, `Ctrl-b w` window list,
`Ctrl-b d` detach, `tmux attach -t mbms-broadcast` to re-attach. Logs are under
`~/.local/state/mbms-broadcast-tutorial/<Window>.log`.

## Configuration

Every path and config filename is a variable at the top of the script; override
by editing the CONFIG block or via the environment, e.g.:

```bash
CONF=/path/to/my/conf MW_IFACE=192.168.1.50 ./mbms-broadcast-tutorial.sh
```

### Privileges (sudo)

`srsepc` needs root (its SP-GW brings up a TUN interface and edits routing), so
its window runs under `sudo`. Following the reference tutorial, the script
authenticates **once** up front (`sudo -v`) and keeps the credential warm while
the windows launch; it never stores your password. Depending on your sudoers
`tty_tickets` setting, the EPC pane may prompt once in its own window — enter the
same password there.

The other components run without root by default. Enable `sudo` for them only
when needed, via environment toggles:

```bash
SUDO_ENB=sudo SUDO_MODEM=sudo ./mbms-broadcast-tutorial.sh   # real SDR
SUDO_GW=sudo   ./mbms-broadcast-tutorial.sh                  # mbms-gw sgi_mb TUN enabled
```

Notes:
- Windows stay open if a component exits, showing the error and its log path.

## ZeroMQ software radio

The default config runs without an SDR: the eNB transmits I/Q over ZeroMQ
(srsRAN's native `zmq` RF driver) and the modem receives it. The modem receives
through a **SoapySDR module that registers a `zmqrx` driver** (see
`conf/modem_zmqtest.conf`: `device_args = "driver=zmqrx,rx_port=tcp://127.0.0.1:2000"`),
which connects to the eNB's ZeroMQ transmitter and presents the samples as an SDR
receive device.

This bridge is **not shipped with the tutorial** — build it yourself. The eNB's ZMQ TX
(`enb_baseline.conf`'s `tx_type=pub`) is a `ZMQ_PUB` socket that free-runs, broadcasting raw
interleaved complex-float32 I/Q bursts with no framing (see srsRAN's own
`rf_zmq_imp_tx.c`/`_rf_zmq_tx_baseband`). The bridge just needs to be the matching `ZMQ_SUB`
side, draining it into a ring buffer from a background thread the same way srsRAN's own
reference receiver (`rf_zmq_imp_rx.c`/`rf_zmq_async_rx_thread`) does — a `ZMQ_REQ`/`REP`
request-burst pattern will **not** work here; ZMQ enforces compatible socket-type pairs and a
`REQ` socket cannot connect to a `PUB` socket at all.

1. Install the dev packages: `sudo apt install libsoapysdr-dev libzmq3-dev`.
2. Save the following as `~/soapy-zmq-bridge/SoapyZmqBridge.cpp`:

   ```cpp
   // Minimal SoapySDR device plugin bridging srsenb's ZMQ_PUB TX output into
   // rt-mbms-modem's usual SoapySDR::Device::make() radio path.
   #include <SoapySDR/Device.hpp>
   #include <SoapySDR/Registry.hpp>
   #include <SoapySDR/Logger.hpp>
   #include <zmq.h>
   #include <atomic>
   #include <chrono>
   #include <complex>
   #include <condition_variable>
   #include <mutex>
   #include <thread>
   #include <vector>

   namespace { constexpr size_t kRingCapacitySamples = 3072000; } // 10 subframes @ 20 MHz

   class SoapyZmqBridge : public SoapySDR::Device {
   public:
       explicit SoapyZmqBridge(const SoapySDR::Kwargs &args) : _ring(kRingCapacitySamples) {
           std::string endpoint = "tcp://127.0.0.1:2000";
           auto it = args.find("rx_port");
           if (it == args.end()) it = args.find("endpoint");
           if (it != args.end()) endpoint = it->second;
           _ctx = zmq_ctx_new();
           _sock = zmq_socket(_ctx, ZMQ_SUB);
           zmq_setsockopt(_sock, ZMQ_SUBSCRIBE, "", 0);
           int timeout_ms = 100;
           zmq_setsockopt(_sock, ZMQ_RCVTIMEO, &timeout_ms, sizeof(timeout_ms));
           zmq_connect(_sock, endpoint.c_str());
           _running = true;
           _rxThread = std::thread(&SoapyZmqBridge::rxThreadBody, this);
       }
       ~SoapyZmqBridge() override {
           _running = false;
           if (_rxThread.joinable()) _rxThread.join();
           if (_sock) zmq_close(_sock);
           if (_ctx) zmq_ctx_destroy(_ctx);
       }
       std::string getDriverKey(void) const override { return "zmqrx"; }
       std::string getHardwareKey(void) const override { return "ZmqRx"; }
       size_t getNumChannels(const int d) const override { return d == SOAPY_SDR_RX ? 1 : 0; }
       std::vector<std::string> listAntennas(const int, const size_t) const override { return {"RX"}; }
       void setAntenna(const int, const size_t, const std::string &) override {}
       std::string getAntenna(const int, const size_t) const override { return "RX"; }
       bool hasGainMode(const int, const size_t) const override { return true; }
       void setGainMode(const int, const size_t, const bool) override {}
       void setGain(const int, const size_t, const double) override {}
       void setGain(const int, const size_t, const std::string &, const double) override {}
       void setFrequency(const int, const size_t, const double f, const SoapySDR::Kwargs &) override { _freq = f; }
       double getFrequency(const int, const size_t) const override { return _freq; }
       void setBandwidth(const int, const size_t, const double bw) override { _bw = bw; }
       double getBandwidth(const int, const size_t) const override { return _bw; }
       void setSampleRate(const int, const size_t, const double r) override { _rate = r; }
       double getSampleRate(const int, const size_t) const override { return _rate; }
       std::vector<double> listSampleRates(const int, const size_t) const override { return {_rate}; }
       SoapySDR::Stream *setupStream(const int d, const std::string &fmt,
                                     const std::vector<size_t> &, const SoapySDR::Kwargs &) override {
           if (d != SOAPY_SDR_RX || fmt != "CF32") return nullptr;
           return reinterpret_cast<SoapySDR::Stream *>(this);
       }
       void closeStream(SoapySDR::Stream *) override {}
       int activateStream(SoapySDR::Stream *, const int, const long long, const size_t) override { return 0; }
       int deactivateStream(SoapySDR::Stream *, const int, const long long) override { return 0; }
       int readStream(SoapySDR::Stream *, void *const *buffs, const size_t numElems,
                       int &flags, long long &timeNs, const long timeoutUs) override {
           flags = 0; timeNs = 0;
           auto *out = reinterpret_cast<std::complex<float> *>(buffs[0]);
           std::unique_lock<std::mutex> lock(_ring.mutex);
           if (_ring.count == 0)
               _ring.cv.wait_for(lock, std::chrono::microseconds(timeoutUs > 0 ? timeoutUs : 100000),
                                  [this] { return _ring.count > 0 || !_running; });
           size_t take = std::min(_ring.count, numElems);
           for (size_t i = 0; i < take; ++i) {
               out[i] = _ring.buf[_ring.head];
               _ring.head = (_ring.head + 1) % _ring.buf.size();
           }
           _ring.count -= take;
           return take == 0 ? SOAPY_SDR_TIMEOUT : static_cast<int>(take);
       }
   private:
       struct Ring {
           explicit Ring(size_t c) : buf(c) {}
           std::vector<std::complex<float>> buf;
           size_t head = 0, tail = 0, count = 0;
           std::mutex mutex; std::condition_variable cv;
       };
       void rxThreadBody() {
           std::vector<uint8_t> rxbuf(1 << 20);
           while (_running) {
               int n = zmq_recv(_sock, rxbuf.data(), rxbuf.size(), 0);
               if (n < 0) continue; // ZMQ_RCVTIMEO expiry -- normal idle case
               if (static_cast<size_t>(n) == rxbuf.size()) rxbuf.resize(rxbuf.size() * 2);
               size_t nsamples = static_cast<size_t>(n) / sizeof(std::complex<float>);
               if (nsamples == 0) continue;
               auto *samples = reinterpret_cast<std::complex<float> *>(rxbuf.data());
               std::lock_guard<std::mutex> lock(_ring.mutex);
               for (size_t i = 0; i < nsamples; ++i) {
                   if (_ring.count == _ring.buf.size()) { _ring.head = (_ring.head + 1) % _ring.buf.size(); --_ring.count; }
                   _ring.buf[_ring.tail] = samples[i];
                   _ring.tail = (_ring.tail + 1) % _ring.buf.size();
                   ++_ring.count;
               }
               _ring.cv.notify_one();
           }
       }
       void *_ctx = nullptr; void *_sock = nullptr;
       double _freq = 0, _bw = 0, _rate = 15360000.0;
       std::atomic<bool> _running{false};
       std::thread _rxThread;
       Ring _ring;
   };

   static SoapySDR::KwargsList findZmqBridge(const SoapySDR::Kwargs &) {
       SoapySDR::Kwargs args; args["driver"] = "zmqrx"; args["label"] = "ZMQ RX Bridge to srsenb";
       return {args};
   }
   static SoapySDR::Device *makeZmqBridge(const SoapySDR::Kwargs &args) { return new SoapyZmqBridge(args); }
   static SoapySDR::Registry registerZmqBridge("zmqrx", &findZmqBridge, &makeZmqBridge, SOAPY_SDR_ABI_VERSION);
   ```

3. Build and install it:
   ```bash
   cd ~/soapy-zmq-bridge
   g++ -std=c++17 -shared -fPIC -O2 SoapyZmqBridge.cpp -o libzmqrxSupport.so \
     $(pkg-config --cflags --libs SoapySDR) $(pkg-config --cflags --libs libzmq) -lpthread
   sudo cp libzmqrxSupport.so "$(pkg-config --variable=libdir SoapySDR)/SoapySDR/modules0.8/libzmqrxSupport.so"
   SoapySDRUtil --info | grep "Available factories"   # -> should list zmqrx
   ```
4. Point the launcher at the directory holding `libzmqrxSupport.so` (only needed if you didn't
   install it into SoapySDR's own module path above):
   ```bash
   SOAPY_ZMQ_DIR=/path/to/your/bridge ./mbms-broadcast-tutorial.sh
   # (or export SOAPY_SDR_PLUGIN_PATH; default is ~/soapy-zmq-bridge)
   ```
   The script exports `SOAPY_SDR_PLUGIN_PATH` for the Modem window; preflight
   warns if the module is missing.

Before wiring this into the full stack, it's worth confirming the bridge actually receives real
bytes in isolation: bind a throwaway `ZMQ_PUB` publisher (a few lines of Python with `pyzmq`,
sending periodic `complex64` bursts on `tcp://*:2000`) and call `SoapySDR::Device::make()` +
`readStream()` against `driver=zmqrx` directly, checking for non-zero sample counts with values
in the expected range. This isolates a broken bridge from a broken eNB/modem config before
they're stacked together.

**Prefer a real SDR?** Set the eNB `device_name`/`device_args` for your radio
(UHD / BladeRF / SoapySDR) in `conf/enb_baseline.conf`, drop the modem's `zmqrx`
`device_args` in `conf/modem_zmqtest.conf`, and you don't need this bridge at all.

## Full chain on one host (receive side in a network namespace)

On real hardware the transmitter and receiver are separate machines. On a single
host the eNB's M1-U receiver and the client's content receiver both want UDP
`:2153` and collide. To run the **whole chain on one box**, put the receive side
in its own network namespace with `receive-netns.sh` (needs sudo):

```bash
./transmit.sh                          # EPC + eNB + MBMS-GW + BM-SC + Portal (root netns)
sudo ./receive-netns.sh start          # modem + client + application in netns "mbms-rx"
```

The eNB transmits ZMQ on `tcp://*:2000`, and the namespace reaches it over a veth
(`10.80.0.1` root ⟷ `10.80.0.2` netns); the modem's ZMQ RX is pointed at
`10.80.0.1:2000` automatically. The receiver's multicast / `:2153` now live
inside `mbms-rx`, isolated from the eNB.

Watch the result from the host:

- **player UI: http://10.80.0.2:3000**  (the application, in the namespace)
- modem API `10.80.0.2:3010`, client API `10.80.0.2:3020`

Drive content as usual from the portal (`:8080`, transmit side): start
`demo-content/hls-http-proxy.js`, Load template, Activate. Tear down with:

```bash
sudo ./receive-netns.sh stop
./transmit.sh --stop
```

Note: netns + veth needs root and can't be exercised in every environment, so if
a component doesn't come up first try, check its log in
`~/.local/state/mbms-broadcast-tutorial/` and the veth/routing with
`sudo ip netns exec mbms-rx ip addr`.
