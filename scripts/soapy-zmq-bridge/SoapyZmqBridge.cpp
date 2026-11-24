// Minimal SoapySDR device plugin that bridges to srsenb's ZMQ RF driver
// (srsRAN's native "zmq" device, tx_type=pub -- a ZMQ_PUB socket bound at
// tx_port that free-runs, broadcasting raw interleaved complex-float32 I/Q
// bursts to any connected subscriber; see rf_zmq_imp_tx.c/_rf_zmq_tx_baseband
// and rf_zmq_imp_rx.c/rf_zmq_async_rx_thread in rt-mbms-modem's vendored
// srsRAN tree, which this bridge mirrors on the RX side). This lets
// rt-mbms-modem run in its normal live-SDR mode (no -f file, no restart
// loop) while the "radio" underneath is actually srsenb's live ZMQ TX
// output. No real hardware involved.
//
// PUB has no request/response step -- a previous version of this file used
// a ZMQ_REQ/REP request-burst pattern, which cannot connect to a PUB socket
// at all (ZMQ enforces compatible socket-type pairs). This version connects
// a ZMQ_SUB socket and drains it continuously from a background thread into
// a ring buffer, exactly like srsRAN's own rf_zmq_imp_rx.c does.
//
// device_args:
//   driver=zmqrx
//   rx_port=tcp://127.0.0.1:2000   (srsenb's tx_port; "endpoint" also accepted)
//   base_srate / native_srate: accepted and ignored (informational only --
//     no resampling/decimation is implemented; keep the eNB and modem at the
//     same n_prb / sample rate, as rt-mbms-modem's modem_zmqtest.conf notes).

#include <SoapySDR/Device.hpp>
#include <SoapySDR/Registry.hpp>
#include <SoapySDR/Logger.hpp>
#include <zmq.h>
#include <atomic>
#include <chrono>
#include <complex>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

namespace {
// 10 subframes at 20 MHz, matching ZMQ_MAX_BUFFER_SIZE in srsRAN's own
// rf_zmq_imp_trx.h -- generous headroom against readStream's ~1ms poll
// cadence falling behind a burst.
constexpr size_t kRingCapacitySamples = 3072000;
}

class SoapyZmqBridge : public SoapySDR::Device
{
public:
    explicit SoapyZmqBridge(const SoapySDR::Kwargs &args)
        : _ring(kRingCapacitySamples)
    {
        std::string endpoint = "tcp://127.0.0.1:2000";
        auto it = args.find("rx_port");
        if (it == args.end()) it = args.find("endpoint");
        if (it != args.end()) endpoint = it->second;

        _ctx = zmq_ctx_new();
        _sock = zmq_socket(_ctx, ZMQ_SUB);
        zmq_setsockopt(_sock, ZMQ_SUBSCRIBE, "", 0);
        int timeout_ms = 100;
        zmq_setsockopt(_sock, ZMQ_RCVTIMEO, &timeout_ms, sizeof(timeout_ms));
        int rc = zmq_connect(_sock, endpoint.c_str());
        if (rc != 0) {
            SoapySDR::logf(SOAPY_SDR_ERROR, "SoapyZmqBridge: failed to connect to %s", endpoint.c_str());
        } else {
            SoapySDR::logf(SOAPY_SDR_INFO, "SoapyZmqBridge: subscribed to %s", endpoint.c_str());
        }

        _running = true;
        _rxThread = std::thread(&SoapyZmqBridge::rxThreadBody, this);
    }

    ~SoapyZmqBridge() override
    {
        _running = false;
        if (_rxThread.joinable()) _rxThread.join();
        if (_sock) zmq_close(_sock);
        if (_ctx) zmq_ctx_destroy(_ctx);
    }

    std::string getDriverKey(void) const override { return "zmqrx"; }
    std::string getHardwareKey(void) const override { return "ZmqRx"; }

    // --- channels ---
    size_t getNumChannels(const int direction) const override
    {
        return direction == SOAPY_SDR_RX ? 1 : 0;
    }

    // --- antenna / gain / frequency / bandwidth / sample rate: accept anything ---
    std::vector<std::string> listAntennas(const int, const size_t) const override { return {"RX"}; }
    void setAntenna(const int, const size_t, const std::string &) override {}
    std::string getAntenna(const int, const size_t) const override { return "RX"; }

    bool hasGainMode(const int, const size_t) const override { return true; }
    void setGainMode(const int, const size_t, const bool) override {}
    void setGain(const int, const size_t, const double) override {}
    void setGain(const int, const size_t, const std::string &, const double) override {}

    void setFrequency(const int, const size_t, const double freq, const SoapySDR::Kwargs &) override { _freq = freq; }
    double getFrequency(const int, const size_t) const override { return _freq; }

    void setBandwidth(const int, const size_t, const double bw) override { _bw = bw; }
    double getBandwidth(const int, const size_t) const override { return _bw; }

    void setSampleRate(const int, const size_t, const double rate) override { _rate = rate; }
    double getSampleRate(const int, const size_t) const override { return _rate; }
    std::vector<double> listSampleRates(const int, const size_t) const override { return {_rate}; }

    // --- streaming ---
    SoapySDR::Stream *setupStream(const int direction, const std::string &format,
                                   const std::vector<size_t> &, const SoapySDR::Kwargs &) override
    {
        if (direction != SOAPY_SDR_RX) return nullptr;
        if (format != "CF32") {
            SoapySDR::logf(SOAPY_SDR_ERROR, "SoapyZmqBridge: only CF32 format supported, got %s", format.c_str());
            return nullptr;
        }
        return reinterpret_cast<SoapySDR::Stream *>(this);
    }

    void closeStream(SoapySDR::Stream *) override {}

    int activateStream(SoapySDR::Stream *, const int, const long long, const size_t) override
    {
        return 0;
    }

    int deactivateStream(SoapySDR::Stream *, const int, const long long) override
    {
        return 0;
    }

    // Pull up to numElems complex64 samples out of the ring buffer, waiting
    // up to timeoutUs for at least one sample. Mirrors
    // srsran_ringbuffer_read_timed's "return what's there, even if partial"
    // semantics -- the caller (rt-mbms-modem's SdrReader::read) polls this
    // roughly every 1ms and tolerates short reads / timeouts, so we never
    // need to block for a full numElems.
    int readStream(SoapySDR::Stream *, void *const *buffs, const size_t numElems,
                    int &flags, long long &timeNs, const long timeoutUs) override
    {
        flags = 0;
        timeNs = 0;
        auto *out = reinterpret_cast<std::complex<float> *>(buffs[0]);

        std::unique_lock<std::mutex> lock(_ring.mutex);
        if (_ring.count == 0) {
            _ring.cv.wait_for(lock, std::chrono::microseconds(timeoutUs > 0 ? timeoutUs : 100000),
                               [this] { return _ring.count > 0 || !_running; });
        }
        size_t take = std::min(_ring.count, numElems);
        for (size_t i = 0; i < take; ++i) {
            out[i] = _ring.buf[_ring.head];
            _ring.head = (_ring.head + 1) % _ring.buf.size();
        }
        _ring.count -= take;
        if (take == 0) return SOAPY_SDR_TIMEOUT;
        return static_cast<int>(take);
    }

private:
    struct Ring {
        explicit Ring(size_t capacity) : buf(capacity) {}
        std::vector<std::complex<float>> buf;
        size_t head = 0; // next sample to read
        size_t tail = 0; // next slot to write
        size_t count = 0;
        std::mutex mutex;
        std::condition_variable cv;
    };

    // Background thread: continuously zmq_recv() one PUB message (one raw
    // interleaved-CF32 burst, no framing/header -- see _rf_zmq_tx_baseband)
    // and push its samples into the ring buffer, dropping the oldest samples
    // on overflow rather than blocking the ZMQ socket (a blocked SUB reader
    // has no back-pressure mechanism to the PUB side anyway).
    void rxThreadBody()
    {
        std::vector<uint8_t> rxbuf(1 << 20); // 1 MiB per recv, grown on demand below
        while (_running) {
            int n = zmq_recv(_sock, rxbuf.data(), rxbuf.size(), 0);
            if (n < 0) {
                // EAGAIN on ZMQ_RCVTIMEO expiry is the normal idle case.
                continue;
            }
            if (static_cast<size_t>(n) == rxbuf.size()) {
                // Message may have been truncated; grow and let the next
                // iteration's zmq_recv (ZMQ still has the rest queued only
                // if using multipart -- PUB/SUB here is single-part, so a
                // truncated read means data loss). Grow preemptively for
                // next time and log once.
                rxbuf.resize(rxbuf.size() * 2);
                SoapySDR::logf(SOAPY_SDR_WARNING, "SoapyZmqBridge: grew recv buffer to %zu bytes", rxbuf.size());
            }

            size_t nsamples = static_cast<size_t>(n) / sizeof(std::complex<float>);
            if (nsamples == 0) continue;
            auto *samples = reinterpret_cast<std::complex<float> *>(rxbuf.data());

            std::lock_guard<std::mutex> lock(_ring.mutex);
            for (size_t i = 0; i < nsamples; ++i) {
                if (_ring.count == _ring.buf.size()) {
                    // Overflow: drop the oldest sample to make room, same
                    // trade-off srsran_ringbuffer_write_timed's timeout path
                    // implies -- keep the newest data, since a broadcast RX
                    // chain has no way to ask the PUB side to slow down.
                    _ring.head = (_ring.head + 1) % _ring.buf.size();
                    --_ring.count;
                }
                _ring.buf[_ring.tail] = samples[i];
                _ring.tail = (_ring.tail + 1) % _ring.buf.size();
                ++_ring.count;
            }
            _ring.cv.notify_one();
        }
    }

    void *_ctx = nullptr;
    void *_sock = nullptr;
    double _freq = 0;
    double _bw = 0;
    double _rate = 15360000.0;
    std::atomic<bool> _running{false};
    std::thread _rxThread;
    Ring _ring;
};

static SoapySDR::KwargsList findZmqBridge(const SoapySDR::Kwargs &)
{
    SoapySDR::Kwargs args;
    args["driver"] = "zmqrx";
    args["label"] = "ZMQ RX Bridge to srsenb";
    return {args};
}

static SoapySDR::Device *makeZmqBridge(const SoapySDR::Kwargs &args)
{
    return new SoapyZmqBridge(args);
}

static SoapySDR::Registry registerZmqBridge("zmqrx", &findZmqBridge, &makeZmqBridge, SOAPY_SDR_ABI_VERSION);
