#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass

from scapy.all import IP, UDP, Raw, rdpcap

from poolcomfort_local.protocol import MSG_HANDSHAKE_1, CONTROL_PORT, Packet


@dataclass(frozen=True)
class AuthPair:
    nonce: bytes
    challenge: bytes
    response: bytes
    session: bytes
    app_ip: str
    pump_ip: str


def iter_auth_pairs(path: str, pump_ip: str) -> list[AuthPair]:
    pending_nonce: dict[tuple[str, str], bytes] = {}
    pending_challenge: dict[tuple[str, str, bytes], tuple[bytes, bytes]] = {}
    pairs: list[AuthPair] = []
    seen: set[tuple[str, str, bytes]] = set()

    for pkt in rdpcap(path):
        if IP not in pkt or UDP not in pkt or Raw not in pkt:
            continue
        if pkt[UDP].sport != CONTROL_PORT and pkt[UDP].dport != CONTROL_PORT:
            continue
        if pump_ip not in {pkt[IP].src, pkt[IP].dst}:
            continue

        packet = Packet.parse(bytes(pkt[Raw].load))
        dedup_key = (pkt[IP].src, pkt[IP].dst, bytes(pkt[Raw].load))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        if packet.message_type != MSG_HANDSHAKE_1:
            continue

        src = pkt[IP].src
        dst = pkt[IP].dst

        if dst == pump_ip and packet.payload.startswith(bytes.fromhex("01010200")) and len(packet.payload) >= 8:
            pending_nonce[(src, pump_ip)] = packet.payload[4:8]
        elif src == pump_ip and packet.payload.startswith(bytes.fromhex("03000000")) and len(packet.payload) >= 8:
            app_ip = dst
            nonce = pending_nonce.pop((app_ip, pump_ip), b"")
            pending_challenge[(app_ip, pump_ip, packet.session)] = (nonce, packet.payload[4:8])
        elif dst == pump_ip and packet.payload.startswith(bytes.fromhex("04000003")) and len(packet.payload) >= 20:
            app_ip = src
            auth = pending_challenge.pop((app_ip, pump_ip, packet.session), None)
            if auth is None:
                continue
            nonce, challenge = auth
            pairs.append(
                AuthPair(
                    nonce=nonce,
                    challenge=challenge,
                    response=packet.payload[4:20],
                    session=packet.session,
                    app_ip=app_ip,
                    pump_ip=pump_ip,
                )
            )

    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Pool Comfort auth challenge-response pairs from a PCAP")
    parser.add_argument("pcap")
    parser.add_argument("--pump", required=True, help="Heat pump IP address")
    args = parser.parse_args()

    pairs = iter_auth_pairs(args.pcap, args.pump)
    if not pairs:
        print("No auth challenge-response pairs found.")
        return

    for index, pair in enumerate(pairs, start=1):
        print(
            f"{index}: app={pair.app_ip} pump={pair.pump_ip} session={pair.session.hex()} "
            f"nonce={pair.nonce.hex()} challenge={pair.challenge.hex()} response={pair.response.hex()}"
        )


if __name__ == "__main__":
    main()
