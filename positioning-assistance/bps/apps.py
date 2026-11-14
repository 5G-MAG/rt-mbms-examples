"""Command lines for the option 1 chain: `python -m bps.apps publish ...` on the broadcaster side, `python -m bps.apps ue ...` on the UE. [design]"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import time
import urllib.request

import numpy as np

from . import geo, lab
from .modem_prs import ModemPrs, ModemRefused
from .publisher import Publisher
from .rtd_monitor import RtdMonitor
from .ue import AssistanceStore, NetworkClock, Positioner, StaleDocument
from .xmb import PortalClient, SessionSpec, XmbUClient


def _fetch(url: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def cmd_publish(a) -> None:
    net = lab.network()
    world = lab.World(epoch_gps_ns=lab.host_gps_ns(a.leap_seconds))
    monitor = RtdMonitor(geo.enu_to_lla(lab.ORIGIN, lab.MONITOR_ENU), net.lla(), reference_id=1, min_epochs=5,
                         timestamp_sigma_ns=world.noise_ns)
    print(f"world_epoch_gps_ns={world.epoch_gps_ns}", flush=True)
    xmbu = XmbUClient(a.xmbu_url, cert=a.cert, key=a.key, insecure=a.insecure)
    session = a.session
    if not session:
        portal = PortalClient(a.portal_url, a.portal_user, a.portal_password)
        spec = SessionSpec("Positioning assistance", tmgi_service_id=a.tmgi_service_id, tsi=a.tsi, mcast_addr=a.mcast, port=a.port,
                           c_teid=a.c_teid, fec_enabled=a.fec, display_base_url=posixpath.dirname(a.path))
        svc, session = portal.create_push_session(spec)
        portal.set_state(svc, session, "Active")
        print(f"session {session} on service {svc} active", flush=True)
    pub = Publisher(net, monitor, lambda body: xmbu.push(session, a.path, body), validity_s=a.validity)
    end = time.time() + a.seconds if a.seconds else None
    while end is None or time.time() < end:
        now = lab.host_gps_ns(a.leap_seconds)
        monitor.add(world.monitor_epoch(now))
        sent = pub.publish_once(now)
        print(f"{time.strftime('%H:%M:%S')} " + ("pushed %d bytes, version %d" % (len(sent), json.loads(sent)["version"]) if sent
                                                    else "collecting monitoring epochs"), flush=True)
        time.sleep(a.interval)


def cmd_ue(a) -> None:
    world = lab.World(epoch_gps_ns=a.world_epoch_gps_ns, seed=2)
    rng = np.random.default_rng(3)
    clock, store = NetworkClock(), AssistanceStore()
    positioner = Positioner(clock, store, height_m=lab.UE_ENU[2] + lab.ORIGIN[2])
    modem_prs = ModemPrs(a.modem_url.rsplit("/", 1)[0], ratio_min=a.prs_ratio_min) if a.measure == "modem" else None
    end = time.time() + a.seconds
    last_doc = None
    print(f"{'time':>8} {'doc':>11} {'net time - host (ms)':>26} {'error m':>8} {'claimed m':>10}", flush=True)
    while time.time() < end:
        try:
            raw = open(a.doc_file, "rb").read() if a.doc_file else _fetch(a.doc_url)
            if raw != last_doc:
                last_doc = raw
                verdict = store.ingest(raw)
                print(f"{time.strftime('%H:%M:%S')} document: {verdict}", flush=True)
        except (OSError, ValueError):
            pass
        info = json.loads(_fetch(a.modem_url))
        sib16 = info.get("sib16") or {}
        rx = info.get("rx_clock")
        if clock.update_from_modem(sib16) and rx:
            sample_clock = rx["sample_clock_ns"]  # now, on the receiver's sample clock
            t_net = clock.network_time_ns(sample_clock)
            try:
                if a.measure == "prs":
                    m = world.measurement_from_prs(t_net, sample_clock, rng, snr_db=0.0 if a.snr_db is None else a.snr_db)
                elif a.measure == "sync":
                    m = world.measurement_from_sync(t_net, sample_clock, rng, snr_db=10.0 if a.snr_db is None else a.snr_db)
                elif a.measure == "modem":
                    # slide 15: the PRS information of the document goes down to the modem's PHY, which measures; the RTD and the cell locations stay here
                    doc = store.current(t_net)
                    if modem_prs.ensure(doc):
                        print(f"{time.strftime('%H:%M:%S')} PRS information of document v{doc.version} pushed to the modem", flush=True)
                    m = modem_prs.next(doc)
                    if m is None:
                        raise ValueError("waiting for a PRS measurement" + (f" ({modem_prs.last_reject})" if modem_prs.last_reject else ""))
                else:
                    m = world.measurement(t_net, sample_clock)
                fix = positioner.solve(m)
            except (StaleDocument, ValueError, ModemRefused, OSError) as e:
                print(f"{time.strftime('%H:%M:%S')} no fix: {e}", flush=True)
            else:
                err = np.linalg.norm(geo.lla_to_enu(lab.ORIGIN, (fix.latitude_deg, fix.longitude_deg, lab.UE_ENU[2] + lab.ORIGIN[2]))[:2]
                                     - lab.UE_ENU[:2])
                host_ms = (t_net - lab.host_gps_ns(a.leap_seconds)) / 1e6
                print(f"{time.strftime('%H:%M:%S'):>8} v{fix.document_version:>10} {host_ms:>26.1f} {err:>8.1f} {fix.horizontal_sigma_m:>10.1f}", flush=True)
        time.sleep(a.interval)


def cmd_rig(a) -> None:
    """The mixer between the transmitter and the modem (bps.rig): the transmitter's IQ in on a ZMQ SUB socket, the same stream with the emulated cells added out on
    a PUB socket, which is where the modem expects the transmitter."""
    import zmq
    from .rig import BLOCK, EmulatedCell, Mixer

    world = lab.World(epoch_gps_ns=lab.host_gps_ns(a.leap_seconds))
    state = {"mtime": None}

    def refresh_epoch() -> None:
        # the publisher's world epoch, once it has started: the RTD of the emulated cells must drift the way the broadcaster's monitor sees it
        if not a.world_epoch_file:
            return
        try:
            m = os.stat(a.world_epoch_file).st_mtime
            if m != state["mtime"]:
                world.epoch_gps_ns = int(open(a.world_epoch_file).read().strip())
                state["mtime"] = m
                print(f"{time.strftime('%H:%M:%S')} world epoch {world.epoch_gps_ns}", flush=True)
        except (OSError, ValueError):
            pass

    def delay(cell_id: int, t_ns: int) -> float:
        refresh_epoch()
        return world.emulated_delay_s(cell_id, world.epoch_gps_ns if a.freeze else t_ns)

    mixer = Mixer([EmulatedCell(c) for c in a.cells], delay, lambda: lab.host_gps_ns(a.leap_seconds), power_db=a.power_db, normal_cp=a.cp == "normal")
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.setsockopt(zmq.RCVHWM, 10000)
    sub.connect(a.input)
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.SNDHWM, 10000)
    pub.bind(a.output)
    print(f"{time.strftime('%H:%M:%S')} rig: {a.input} -> {a.output}, cells {a.cells} at {a.power_db} dB, {a.cp} cyclic prefix", flush=True)
    print(f"world_epoch_gps_ns={world.epoch_gps_ns}{' (delays frozen at the epoch)' if a.freeze else ''}", flush=True)
    n = 0
    was_locked = False
    while True:
        msg = sub.recv()
        block = np.frombuffer(msg, dtype=np.complex64)
        out = mixer.process(block) if len(block) == BLOCK else block
        pub.send(np.ascontiguousarray(out, dtype=np.complex64).tobytes())
        n += 1
        if mixer.p0 is not None and not was_locked:
            was_locked = True
            print(f"{time.strftime('%H:%M:%S')} rig: locked onto the cell acquisition subframes (match {mixer.lock_quality:.0f} times the background, "
                  f"stream scale {mixer.scale:.4f})", flush=True)
        if n % 5000 == 0:
            print(f"{time.strftime('%H:%M:%S')} rig: {n} blocks, {mixer.injected} cell subframes added", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--xmbu-url", default="https://127.0.0.1:8543")
    p.add_argument("--cert", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--insecure", action="store_true", help="do not verify the BM-SC's certificate (lab)")
    p.add_argument("--session", help="an existing Files/Push session; otherwise one is created through the portal")
    p.add_argument("--portal-url", default="http://127.0.0.1:8080")
    p.add_argument("--portal-user", default="admin")
    p.add_argument("--portal-password", default="")
    p.add_argument("--tmgi-service-id", type=int, default=22)
    p.add_argument("--tsi", type=int, default=5)
    p.add_argument("--mcast", default="239.255.0.2")
    p.add_argument("--port", type=int, default=2155)
    p.add_argument("--c-teid", type=int, default=0xCCCD)
    p.add_argument("--fec", action="store_true",
                   help="application-layer FEC on the session. Off by default: with it on, the BM-SC fails to send objects of 2 or 3 "
                        "source symbols (1500 to 3000 bytes here, 'Not enough space in payload buffer for encoding symbol'); the "
                        "document is repeated every few seconds, which is the redundancy it needs")
    p.add_argument("--path", default="positioning/assistance.json")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--validity", type=int, default=90)
    p.add_argument("--leap-seconds", type=int, default=18)
    p.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = run until killed)")
    u = sub.add_parser("ue")
    g = u.add_mutually_exclusive_group(required=True)
    g.add_argument("--doc-file")
    g.add_argument("--doc-url")
    u.add_argument("--modem-url", default="http://10.80.0.2:3010/modem-api/sib_info")
    u.add_argument("--world-epoch-gps-ns", type=int, required=True, help="the publisher's world epoch, printed by `publish`")
    u.add_argument("--measure", choices=("formula", "prs", "sync", "modem"), default="formula",
                   help="where the time differences come from: computed from the geometry (default), estimated from simulated PRS waveforms, estimated "
                        "from the PSS and SSS of simulated cell acquisition subframes, or measured by the modem on the PRS it receives (the PRS information "
                        "of the assistance document is pushed down to it, slide 15 of the proposal)")
    u.add_argument("--prs-ratio-min", type=float, default=9.0,
                   help="with --measure modem: how far a cell's correlation peak must stand out of the background (peak over median, as the modem reports it) "
                        "for the cell to count as detected")
    u.add_argument("--snr-db", type=float, default=None,
                   help="with --measure prs: signal to noise ratio per sample of each cell (default 0); with --measure sync: per resource "
                        "element (default 10)")
    u.add_argument("--interval", type=float, default=2.0)
    u.add_argument("--seconds", type=float, default=60.0)
    u.add_argument("--leap-seconds", type=int, default=18)
    r = sub.add_parser("rig", help="mix emulated neighbouring cells into the transmitter's IQ stream")
    r.add_argument("--input", default="tcp://127.0.0.1:2002", help="the transmitter's ZMQ output (its tx_port)")
    r.add_argument("--output", default="tcp://*:2000", help="where the modem expects the transmitter")
    r.add_argument("--cells", type=int, nargs="+", default=[2, 3, 4], help="the emulated cells, by identity")
    r.add_argument("--power-db", type=float, default=-6.0, help="their power against the real cell's PSS and SSS elements (at -12 dB the modem barely tells them from noise, at -6 dB it measures them to about 10 ns)")
    r.add_argument("--world-epoch-file", help="the publisher's world epoch (positions the RTD of the emulated cells in time)")
    r.add_argument("--cp", choices=("extended", "normal"), default="extended", help="the real cell's cyclic prefix (the transmitter's extended_cp)")
    r.add_argument("--freeze", action="store_true", help="evaluate the delays at the world epoch, so they do not drift (for checks against exact values)")
    r.add_argument("--leap-seconds", type=int, default=18)
    a = ap.parse_args()
    {"publish": cmd_publish, "ue": cmd_ue, "rig": cmd_rig}[a.cmd](a)


if __name__ == "__main__":
    main()
