// ul-feeder.cpp -- minimal uplink sample feeder for a downlink-only srsRAN
// ZeroMQ FeMBMS/broadcast eNB.
//
// Why this exists: srsenb's ZMQ radio drives its timing from rx_now(). With
// rx_port configured, every subframe the eNB's RX (a ZMQ_REQ socket) sends a
// one-byte request to :2001 and blocks until it gets a reply carrying uplink
// samples. In a real ue+enb ZMQ setup the srsUE's TX (a ZMQ_REP) answers those
// requests; in a receive-only broadcast setup (modem via the RX-only SoapySDR
// zmqrx bridge) nobody answers, so rf_zmq_rx_baseband blocks forever and the
// eNB never transmits. Removing rx_port avoids the block but also removes the
// per-subframe pacing usleep in rf_zmq_recv, so the eNB free-runs at many times
// real time and floods the modem's bridge ("RX buffer overflow").
//
// This process is the missing ZMQ_REP peer: it binds :2001 and answers every
// request with a buffer of zero samples. It does NOT pace -- the eNB's own
// rx_now() usleep provides the real-time clock; the ring buffer back-pressures
// this feeder, so replying as fast as requested is correct.
//
// Build:  g++ -std=c++17 ul-feeder.cpp -o ul-feeder $(pkg-config --cflags --libs libzmq)
// Run:    ./ul-feeder [bind_endpoint]      (default tcp://*:2001)

#include <zmq.h>

#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {
volatile sig_atomic_t g_run = 1;
void on_signal(int) { g_run = 0; }
} // namespace

int main(int argc, char** argv)
{
  const char* endpoint = (argc > 1) ? argv[1] : "tcp://*:2001";

  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);

  void* ctx = zmq_ctx_new();
  if (!ctx) {
    std::fprintf(stderr, "ul-feeder: zmq_ctx_new failed\n");
    return 1;
  }

  void* sock = zmq_socket(ctx, ZMQ_REP);
  if (!sock) {
    std::fprintf(stderr, "ul-feeder: zmq_socket failed: %s\n", zmq_strerror(zmq_errno()));
    return 1;
  }

  // Time-limited recv so we can notice SIGTERM between requests.
  int rcv_timeout_ms = 500;
  zmq_setsockopt(sock, ZMQ_RCVTIMEO, &rcv_timeout_ms, sizeof(rcv_timeout_ms));
  int linger = 0;
  zmq_setsockopt(sock, ZMQ_LINGER, &linger, sizeof(linger));

  if (zmq_bind(sock, endpoint) != 0) {
    std::fprintf(stderr, "ul-feeder: bind %s failed: %s\n", endpoint, zmq_strerror(zmq_errno()));
    return 1;
  }

  // One LTE subframe of samples at 15.36 Msps, as complex float32 (cf_t = 8 B).
  // srsran only cares that the byte count is a multiple of sizeof(cf_t); the
  // eNB buffers whatever it gets and reads what it needs, so an all-zero
  // uplink is fine for downlink-only broadcast.
  const size_t kSamples = 15360;
  const size_t kBytes   = kSamples * 2 * sizeof(float);
  std::vector<uint8_t> zeros(kBytes, 0);

  std::fprintf(stderr,
               "ul-feeder: REP bound on %s, answering rx requests with %zu zero-samples (%zu B)\n",
               endpoint,
               kSamples,
               kBytes);

  uint64_t replies = 0;
  while (g_run) {
    uint8_t request = 0;
    int     n       = zmq_recv(sock, &request, sizeof(request), 0);
    if (n < 0) {
      if (zmq_errno() == EAGAIN) {
        continue; // recv timeout: re-check g_run
      }
      if (zmq_errno() == EINTR) {
        continue;
      }
      std::fprintf(stderr, "ul-feeder: recv error: %s\n", zmq_strerror(zmq_errno()));
      break;
    }
    if (zmq_send(sock, zeros.data(), zeros.size(), 0) < 0) {
      if (zmq_errno() == EINTR) {
        continue;
      }
      std::fprintf(stderr, "ul-feeder: send error: %s\n", zmq_strerror(zmq_errno()));
      break;
    }
    ++replies;
  }

  std::fprintf(stderr, "ul-feeder: shutting down after %llu replies\n", (unsigned long long)replies);
  zmq_close(sock);
  zmq_ctx_destroy(ctx);
  return 0;
}
