#!/usr/bin/env python3
from __future__ import annotations

import argparse

from scapy.all import IP, UDP, Raw, rdpcap

from poolcomfort_local.protocol import Packet, parse_pool_state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pcap")
    parser.add_argument("--pump", default="192.168.1.x")
    args = parser.parse_args()

    last = None
    for pkt in rdpcap(args.pcap):
        if IP not in pkt or UDP not in pkt or Raw not in pkt:
            continue
        if args.pump not in {pkt[IP].src, pkt[IP].dst}:
            continue
        if pkt[UDP].sport != 1194 and pkt[UDP].dport != 1194:
            continue
        raw = bytes(pkt[Raw].load)
        key = (pkt[IP].src, pkt[IP].dst, raw)
        if key == last:
            continue
        last = key
        parsed = Packet.parse(raw)
        direction = "pump -> app" if pkt[IP].src == args.pump else "app  -> pump"
        print(
            f"{float(pkt.time):.3f} {direction} seq={parsed.sequence:04x} "
            f"type={parsed.message_type:04x} len={len(parsed.payload):03d} {parsed.payload.hex()}"
        )
        if parsed.payload.startswith(bytes.fromhex("080d")) or parsed.payload.startswith(bytes.fromhex("0b0a")):
            state = parse_pool_state(parsed.payload)
            if any(value is not None for value in state.__dict__.values()):
                print(f"  state={state}")


if __name__ == "__main__":
    main()
