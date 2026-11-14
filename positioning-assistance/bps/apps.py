"""Command lines for the option 1 chain: `python -m bps.apps publish ...` on the broadcaster side, `python -m bps.apps ue ...` on the UE. [design]"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
import posixpath
import time
import urllib.request

import numpy as np

from . import geo, lab
from .assistance import AssistanceDocument
from .live import ModemSfn
from .modem_prs import ModemPrs, ModemRefused
from .publisher import Publisher
from .rtd_monitor import RtdMonitor
from .ue import AssistanceStore, NetworkClock, Positioner, StaleDocument
from .xmb import PortalClient, SessionSpec, XmbUClient


def _cell_cp(items) -> dict:
    """`--cell-cp 3=normal` (repeatable) as {3: "normal"}."""
    out = {}
    for it in items or []:
        cid, _, cp = it.partition("=")
        if not cid.isdigit() or cp not in ("normal", "extended"):
            raise SystemExit(f"--cell-cp wants CELL=normal or CELL=extended, not {it!r}")
        out[int(cid)] = cp
    return out


def _cell_cfo(items) -> dict:
    """`--cell-cfo 3=150` (repeatable) as {3: 150.0}, in Hz."""
    out = {}
    for it in items or []:
        cid, _, hz = it.partition("=")
        try:
            out[int(cid)] = float(hz)
        except ValueError:
            raise SystemExit(f"--cell-cfo wants CELL=HZ, e.g. 3=150, not {it!r}")
    return out


def _ecgi(text):
    """`--ref-ecgi 901-56-0x19B01` (mcc-mnc-cell identity) as ("901", "56", 0x19B01)."""
    if not text:
        return None
    try:
        mcc, mnc, ci = text.split("-")
        return mcc, mnc, int(ci, 0)
    except ValueError:
        raise SystemExit(f"--ref-ecgi wants MCC-MNC-CELLIDENTITY, e.g. 901-56-0x19B01, not {text!r}")


def _file_store(serve_dir: str, serve_url: str):
    """Stores the assistance file where `serve_url` points (a file of `serve_dir`, which a web server serves), replacing it atomically."""
    if not serve_dir or not serve_url:
        raise SystemExit("--deliver url needs --serve-dir and --serve-url")
    name = serve_url.rsplit("/", 1)[-1]
    os.makedirs(serve_dir, exist_ok=True)

    def store(body: bytes) -> None:
        tmp = os.path.join(serve_dir, f".{name}.tmp")
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, os.path.join(serve_dir, name))
    return store


def _sent_version(sent: bytes) -> int:
    """The version of a document the publisher has just sent, for its log line."""
    return AssistanceDocument.from_json(sent.decode()).version


def _fetch(url: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def cmd_publish(a) -> None:
    net = lab.network(_cell_cp(a.cell_cp), a.cp, _ecgi(a.ref_ecgi))
    if a.service_radius_m:
        net = replace(net, service_radius_m=a.service_radius_m)
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
    sfn_at = ModemSfn(a.sfn_from_modem) if a.sfn_from_modem else None
    pub = Publisher(net, monitor, lambda body: xmbu.push(session, a.path, body), validity_s=a.validity, sfn_at=sfn_at,
                    repeat_interval_s=a.interval, broadcast_area=a.broadcast_area, leap_seconds=a.leap_seconds,
                    file_url=a.serve_url if a.deliver == "url" else None, store_file=_file_store(a.serve_dir, a.serve_url) if a.deliver == "url" else None)
    end = time.time() + a.seconds if a.seconds else None
    while end is None or time.time() < end:
        now = lab.host_gps_ns(a.leap_seconds)
        monitor.add(world.monitor_epoch(now))
        sent = pub.publish_once(now)
        print(f"{time.strftime('%H:%M:%S')} " + ("pushed %d bytes, version %d" % (len(sent), _sent_version(sent)) if sent
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

    cell_cp = _cell_cp(a.cell_cp)
    cell_cfo = _cell_cfo(a.cell_cfo)
    mixer = Mixer([EmulatedCell(c, normal_cp=(cell_cp[c] == "normal") if c in cell_cp else None, cfo_hz=cell_cfo.get(c, 0.0)) for c in a.cells], delay, lambda: lab.host_gps_ns(a.leap_seconds), power_db=a.power_db, normal_cp=a.cp == "normal", snr_db=a.snr_db, real_cell_has_prs=not a.no_real_prs)
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
        if a.snr_file and n % 1000 == 0:
            try:
                text = open(a.snr_file).read().strip()
                snr = None if text in ("", "off") else float(text)
                if snr != mixer.snr_db:
                    mixer.snr_db = snr
                    print(f"{time.strftime('%H:%M:%S')} rig: noise {'off' if snr is None else f'{snr} dB Es/N0'}", flush=True)
            except (OSError, ValueError):
                pass
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
                   help="application-layer FEC on the session (the deck, slide 18 step 7). Off by default. It needs an rt-libflute that sends only as many "
                        "encoding symbols in a packet as fit (rt-libflute 'File: send only as many encoding symbols in a packet as fit in it'): without it the BM-SC "
                        "fails to send objects of 2 to 5 source symbols ('Not enough space in payload buffer for encoding symbol')")
    p.add_argument("--path", default="positioning/assistance.json")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--validity", type=int, default=90)
    p.add_argument("--leap-seconds", type=int, default=18)
    p.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = run until killed)")
    p.add_argument("--sfn-from-modem", metavar="URL", help="state the reference cell's SFN at the time the RTDs hold (OMA-LPPe systemFrameNumber), taken from the SFN boundary and time in the modem's sib_info at this URL (lab: same host as the transmitter)")
    p.add_argument("--deliver", choices=("file", "url"), default="file", help="send the assistance file itself over the air, or the URL where it is (the deck, slide 18, \"to alleviate NW load\"; the UE then fetches it)")
    p.add_argument("--serve-dir", help="--deliver url: the directory a web server serves, where the file is stored")
    p.add_argument("--serve-url", help="--deliver url: the URL of the file in that directory, which is what is sent")
    p.add_argument("--broadcast-area", help="the broadcastArea of the file's envelope (the deck's slide 14), when the broadcaster names one")
    p.add_argument("--cp", choices=("normal", "extended"), default="extended", help="the real cell's cyclic prefix, stated in the document (cpLength of the reference cell is mandatory)")
    p.add_argument("--ref-ecgi", metavar="MCC-MNC-CELLID", help="the reference cell's global identity (cellGlobalId, optional), e.g. 901-56-0x19B01")
    p.add_argument("--service-radius-m", type=float, help="how far from the centre of the cells a UE may be (default: the largest distance from the centre to a cell); sets the search window of expectedRSTD")
    p.add_argument("--cell-cp", action="append", metavar="CELL=normal|extended",
                   help="state this cell's cyclic prefix in the document (TS 37.355 cpLength); a neighbour that is not named has the reference cell's and states none (repeatable)")
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
    r.add_argument("--snr-db", type=float, default=None, help="add white noise to the stream: Es/N0 per resource element (a PSS or SSS element of the real cell is the reference) at the modem")
    r.add_argument("--snr-file", help="a file holding the Es/N0 in dB (or `off`), read once a second and followed while running; overrides --snr-db")
    r.add_argument("--no-real-prs", action="store_true", help="the real cell sends no PRS (the lock reference then holds its PSS and SSS only)")
    r.add_argument("--cell-cfo", action="append", metavar="CELL=HZ", help="an emulated cell's carrier frequency offset against the real cell's (separate oscillators), in Hz (repeatable)")
    r.add_argument("--cell-cp", action="append", metavar="CELL=normal|extended", help="an emulated cell whose cyclic prefix differs from the real cell's (repeatable)")
    r.add_argument("--freeze", action="store_true", help="evaluate the delays at the world epoch, so they do not drift (for checks against exact values)")
    r.add_argument("--leap-seconds", type=int, default=18)
    a = ap.parse_args()
    {"publish": cmd_publish, "ue": cmd_ue, "rig": cmd_rig}[a.cmd](a)


if __name__ == "__main__":
    main()
