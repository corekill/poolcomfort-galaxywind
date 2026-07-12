"""Retry behaviour of PoolComfortClient._send (issue #1).

No real sockets: ``client._sock`` is replaced with a stub that counts
transmissions, and replies are injected straight into the pending slot the
way the reader thread would.
"""

from __future__ import annotations

import threading

import pytest

from poolcomfort_local.client import SEND_ATTEMPTS, PoolComfortClient
from poolcomfort_local.protocol import MSG_DATA, MSG_HANDSHAKE_1, MSG_PING, Packet


class _StubSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendto(self, data: bytes, addr) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


def _make_client() -> tuple[PoolComfortClient, _StubSocket]:
    client = PoolComfortClient("192.0.2.1", timeout=0.05, keepalive=False)
    sock = _StubSocket()
    client._sock = sock
    return client, sock


def test_send_retransmits_on_timeout() -> None:
    client, sock = _make_client()
    packet = Packet(0x32, 5, b"\x00" * 5, MSG_DATA, b"\x01\x02")

    with pytest.raises(TimeoutError, match="after 3 attempts"):
        client._send(packet)

    assert len(sock.sent) == SEND_ATTEMPTS
    # Every retransmission must be byte-identical (same sequence number).
    assert len(set(sock.sent)) == 1


def test_send_returns_reply_that_arrives_during_retry() -> None:
    client, sock = _make_client()
    packet = Packet(0x32, 7, b"\x00" * 5, MSG_PING, b"\x01\x00\x00\x00")
    reply = Packet(0x32, 7, b"\x00" * 5, MSG_PING, b"\x02\x00\x00\x00")

    def deliver_late() -> None:
        # Wait until the second transmission went out, then answer it the
        # way the reader thread would.
        while len(sock.sent) < 2:
            pass
        key = (packet.sequence, packet.message_type)
        with client._pending_lock:
            slot = client._pending[key]
        slot.reply = reply
        slot.event.set()

    thread = threading.Thread(target=deliver_late, daemon=True)
    thread.start()
    result = client._send(packet)
    thread.join(timeout=1)

    assert result is reply
    assert len(sock.sent) >= 2


def test_send_does_not_retry_handshake() -> None:
    client, sock = _make_client()
    packet = Packet(0x32, 0, b"\x00" * 5, MSG_HANDSHAKE_1, b"\x01\x01\x02\x00")

    with pytest.raises(TimeoutError, match="after 1 attempts"):
        client._send(packet)

    # A duplicated login could allocate a second session slot on the pump,
    # so handshake packets must go out exactly once.
    assert len(sock.sent) == 1


def test_send_immediate_reply_sends_once() -> None:
    client, sock = _make_client()
    packet = Packet(0x32, 9, b"\x00" * 5, MSG_DATA, b"\xaa")
    reply = Packet(0x32, 9, b"\x00" * 5, MSG_DATA, b"\xbb")

    def deliver() -> None:
        while len(sock.sent) < 1:
            pass
        key = (packet.sequence, packet.message_type)
        with client._pending_lock:
            slot = client._pending[key]
        slot.reply = reply
        slot.event.set()

    thread = threading.Thread(target=deliver, daemon=True)
    thread.start()
    result = client._send(packet)
    thread.join(timeout=1)

    assert result is reply
    assert len(sock.sent) == 1
