# SoapySDR `zmqrx` bridge

The demo runs without an SDR: the eNB transmits I/Q over ZeroMQ (srsRAN's native `zmq` RF
driver, `tx_type=pub`) and the modem receives through a SoapySDR module registering a `zmqrx`
driver, which subscribes to that socket and presents the samples as a receive device.

That module is not part of SoapySDR and not part of any of the components, which is why it
lives here.

```bash
./build.sh              # -> libzmqrxSupport.so in this directory
./build.sh --install    # also install into SoapySDR's module path, and check it registers
```

Needs `libsoapysdr-dev` and `libzmq3-dev`, both in the demo's package list. The demo finds the
result through `SOAPY_ZMQ_DIR`, which defaults to this directory; `soapysdr-tools` provides the
`SoapySDRUtil` the `--install` check uses.

`SoapyZmqBridge.cpp`'s own header explains what it talks to and why it is a `ZMQ_SUB` socket
drained by a background thread rather than a request/response pair.
