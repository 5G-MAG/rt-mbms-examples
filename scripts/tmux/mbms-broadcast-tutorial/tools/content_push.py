#!/usr/bin/env python3
"""Push a continuous stream of real content into a BM-SC MBMS session for
end-to-end testing, bypassing xMB/FLUTE entirely.

BM-SC never listens on a content session's own mcast_addr:port -- that
address is purely the *downstream* destination baked into the packet header
(see ~/rt-mbms-bmsc/bmsc/main.cc's own comment: "content itself is broadcast
by a separate FLUTE sender, not srsbmsc"). What MBMS-GW actually does is
listen on its [sgi_mb_tunnel] socket (bind_port, matches bmsc.conf's own
tunnel_port) for already-IP/UDP-encapsulated packets, and dispatch each one
purely by the encapsulated packet's destination IP address
(mbms-gw.cc:forward_sgi_mb_pdu_to_m1u -> get_c_teid_by_dist_addr(), matched
against whichever content session's mcast_addr the eNB has an Active bearer
for). No checksum or deep validation is required downstream -- only
N_bytes>=20 and IP version==4 (confirmed by reading mbms-gw.cc directly) --
but this builds a real, correctly-checksummed IPv4+UDP header anyway,
byte-for-byte matching ~/rt-mbms-bmsc/bmsc/xmb/raw_udp_relay.cc's own
build_ip_udp_packet(), so the test exercises the exact wire format a real
FLUTE/xMB sender would produce.

This is a *plain* UDP socket, not a raw socket -- no root/CAP_NET_RAW
needed. The "IP packet" is just this script's own UDP payload; MBMS-GW's
own application code is what interprets those payload bytes as an
encapsulated IP/UDP packet, not the OS/kernel network stack.

Usage:
    ./content_push.py [--host 127.0.0.1] [--port 47000] \\
        [--dest 239.255.1.1] [--dport 6000] [--interval 0.2] [--count N]

Defaults match this tutorial's bmsc.conf [content_session] entry
(tunnel_addr/tunnel_port, mcast_addr, port) -- override if testing a
different session.
"""
import argparse
import socket
import struct
import sys
import time


def checksum(data: bytes) -> int:
    """RFC 1071 internet checksum -- same algorithm as raw_udp_relay.cc's
    calculate_sum() / libflute's Transmitter.cpp::calculate_sum()."""
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def build_ip_udp_packet(src_ip: str, dst_ip: str, dst_port: int, payload: bytes) -> bytes:
    src = socket.inet_aton(src_ip)
    dst = socket.inet_aton(dst_ip)

    # UDP header (source port == dest port, matching raw_udp_relay.cc's own
    # convention for this relay's tunnelled output).
    udp_len = 8 + len(payload)
    pseudo_hdr = src + dst + b"\x00" + bytes([socket.IPPROTO_UDP]) + struct.pack("!H", udp_len)
    udp_hdr_zero_sum = struct.pack("!HHHH", dst_port, dst_port, udp_len, 0)
    udp_sum = checksum(pseudo_hdr + udp_hdr_zero_sum + payload)
    udp_hdr = struct.pack("!HHHH", dst_port, dst_port, udp_len, udp_sum)

    # IPv4 header (id=0, no fragmentation, ttl=63, protocol=UDP -- matching
    # raw_udp_relay.cc field-for-field).
    total_len = 20 + udp_len
    ip_hdr_zero_check = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total_len, 0, 0, 63, socket.IPPROTO_UDP, 0, src, dst,
    )
    ip_check = checksum(ip_hdr_zero_check)
    ip_hdr = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total_len, 0, 0, 63, socket.IPPROTO_UDP, ip_check, src, dst,
    )
    return ip_hdr + udp_hdr + payload


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="MBMS-GW sgi_mb_tunnel host")
    ap.add_argument("--port", type=int, default=47000, help="MBMS-GW sgi_mb_tunnel bind_port")
    ap.add_argument("--dest", default="239.255.1.1", help="content session mcast_addr")
    ap.add_argument("--dport", type=int, default=6000, help="content session port")
    ap.add_argument("--src", default="10.0.0.1", help="fake source IP for the encapsulated packet")
    ap.add_argument("--interval", type=float, default=0.2, help="seconds between packets")
    ap.add_argument("--count", type=int, default=0, help="number of packets to send (0 = run until Ctrl-C)")
    ap.add_argument("--size", type=int, default=512, help="payload size in bytes")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tunnel = (args.host, args.port)

    print(f"Sending to MBMS-GW tunnel {tunnel}, encapsulated dest {args.dest}:{args.dport}, "
          f"every {args.interval}s, payload={args.size}B. Ctrl-C to stop.")

    seq = 0
    try:
        while args.count == 0 or seq < args.count:
            marker = f"content_push seq={seq} t={time.time():.3f} ".encode()
            payload = marker + bytes((seq + i) % 256 for i in range(max(0, args.size - len(marker))))
            packet = build_ip_udp_packet(args.src, args.dest, args.dport, payload)
            sock.sendto(packet, tunnel)
            if seq % 10 == 0:
                print(f"  sent seq={seq} ({len(packet)} bytes)")
            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {seq} packets.")
        sys.exit(0)


if __name__ == "__main__":
    main()
