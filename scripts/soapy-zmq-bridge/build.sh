#!/bin/bash
# Builds the SoapySDR zmqrx module the modem needs when the "radio" is srsenb's ZeroMQ
# transmitter rather than real hardware. Not needed with a real SDR.
#
#   ./build.sh            build libzmqrxSupport.so next to this script
#   ./build.sh --install  also copy it into SoapySDR's own module path (needs sudo)
#
# Point the demo at the result with SOAPY_ZMQ_DIR=<this directory>, or install it and let
# SoapySDR find it on its own.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

for p in SoapySDR libzmq; do
    pkg-config --exists "$p" || {
        echo "missing $p development files (apt install libsoapysdr-dev libzmq3-dev)" >&2
        exit 1
    }
done

g++ -std=c++17 -shared -fPIC -O2 SoapyZmqBridge.cpp -o libzmqrxSupport.so \
    $(pkg-config --cflags --libs SoapySDR) $(pkg-config --cflags --libs libzmq) -lpthread
echo "built $(pwd)/libzmqrxSupport.so"

if [[ "${1:-}" == "--install" ]]; then
    dest="$(pkg-config --variable=libdir SoapySDR)/SoapySDR/modules0.8"
    sudo install -D -m 0755 libzmqrxSupport.so "$dest/libzmqrxSupport.so"
    echo "installed into $dest"
    # The module only counts as working once SoapySDR itself lists the driver: a module that
    # loads but fails to register is indistinguishable from a missing one at modem start.
    SoapySDRUtil --info 2>/dev/null | grep -q zmqrx \
        && echo "SoapySDR lists the zmqrx factory" \
        || { echo "WARNING: SoapySDR does not list zmqrx -- the modem will not find a radio" >&2; exit 1; }
fi
