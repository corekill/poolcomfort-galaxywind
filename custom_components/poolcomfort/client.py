from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import socket
import threading
import time

from .protocol import (
    ATTR_ALL,
    ATTR_MODE,
    ATTR_POWER,
    ATTR_STATE_BLOCK,
    ATTR_TARGET_TEMP,
    CONTROL_PORT,
    DISCOVERY_PORT,
    DEVICE_TYPE_POOL_HEATPUMP,
    MSG_DATA,
    MSG_HANDSHAKE_1,
    MSG_PING,
    OP_ACK_NOTIFY,
    OP_NOTIFY,
    Packet,
    PoolDiagnostics,
    PoolState,
    build_query_payload,
    build_set_payload,
    parse_pool_diagnostics,
    parse_pool_state,
)


APP_ID = b"com.codery.PoolComfort20160704"
DISCOVERY_PAYLOAD = bytes.fromhex("000000e8d0abbbd40000000000000000000100040000000001020078")
# Fallback when local time is unavailable; matches a captured session handshake.
APP_PROTO_TAG = bytes.fromhex("07ea050914032b02")


def build_proto_tag(now: time.struct_time | None = None) -> bytes:
    # Layout from captures: BE uint16 year, then byte month, day, hour, minute, second, then 0x02.
    # Two captured sessions (live ~20:03:43 and ~20:04:01 on 2026-05-09) both end with 0x02.
    t = now if now is not None else time.localtime()
    return (
        t.tm_year.to_bytes(2, "big")
        + bytes([t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, 0x02])
    )

# Pump-initiated keepalive probe — type=00f3, payload=07000000. Not seen in app captures
# but the device sends it when it stops hearing from us; respond with a normal ping echo.
PUMP_PROBE_OP = b"\x07"

KEEPALIVE_INTERVAL = 1.5


def discover_hosts(timeout: float = 2.0) -> list[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    hosts: list[str] = []
    try:
        sock.bind(("", 0))
        sock.sendto(DISCOVERY_PAYLOAD, ("255.255.255.255", DISCOVERY_PORT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                _raw, addr = sock.recvfrom(2048)
            except socket.timeout:
                break
            host = addr[0]
            if host not in hosts:
                hosts.append(host)
    finally:
        sock.close()
    return hosts


def build_auth_response(nonce: bytes, challenge: bytes, password: str) -> bytes:
    if len(nonce) != 4:
        raise ValueError("nonce must be 4 bytes")
    if len(challenge) != 4:
        raise ValueError("challenge must be 4 bytes")
    password_key = hashlib.md5(password.encode()).digest()
    return hashlib.md5(nonce + challenge + password_key).digest()


@dataclass
class _Pending:
    event: threading.Event
    reply: Packet | None = None


@dataclass
class PoolComfortClient:
    host: str
    password: str = "123456"
    local_port: int = 0
    timeout: float = 2.0

    def __post_init__(self) -> None:
        self._sequence = 0
        self._session = b"\x00\x00\x00\x00\x00"
        self._sock: socket.socket | None = None
        self._discovery_token = bytes.fromhex("000000bfe0301313")
        self._send_lock = threading.Lock()
        self._pending: dict[tuple[int, int], _Pending] = {}
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self._last_send = 0.0

    def discover(self) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        try:
            sock.bind(("", 0))
            sock.sendto(DISCOVERY_PAYLOAD, (self.host, DISCOVERY_PORT))
            raw, _addr = sock.recvfrom(2048)
        finally:
            sock.close()
        if len(raw) >= 8:
            self._discovery_token = raw[:8]
        return raw

    def connect(self) -> None:
        self.discover()
        self.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.bind(("", self.local_port))
        self._sock = sock
        self._stop.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, name="poolcomfort-reader", daemon=True)
        self._reader_thread.start()

        try:
            nonce = os.urandom(4)
            proto_tag = build_proto_tag()
            response = self._send(
                Packet(
                    marker=0x32,
                    sequence=0,
                    session=b"\x00\x00\x00\x00\x00",
                    message_type=MSG_HANDSHAKE_1,
                    payload=(
                        bytes.fromhex("01010200")
                        + nonce
                        + self._discovery_token
                        + bytes.fromhex("6ea8533f06364e95aa56caaac6358b06")
                        + proto_tag
                    ),
                )
            )
            self._session = response.session
            if not response.payload.startswith(bytes.fromhex("03000000")) or len(response.payload) < 8:
                raise RuntimeError("heat pump returned an unexpected login challenge")
            challenge = response.payload[4:8]
            auth_response = build_auth_response(nonce, challenge, self.password)
            payload = (
                bytes.fromhex("04000003")
                + auth_response
                + proto_tag
                + bytes.fromhex("00640004000000030065")
                + len(APP_ID).to_bytes(2, "big")
                + APP_ID
            )
            self._send(Packet(0x32, 0, self._session, MSG_HANDSHAKE_1, payload))
        except Exception:
            self.close()
            raise
        with self._send_lock:
            self._sequence = -1

        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, name="poolcomfort-keepalive", daemon=True)
        self._keepalive_thread.start()

    def close(self) -> None:
        self._stop.set()
        for attr in ("_keepalive_thread", "_reader_thread"):
            thread: threading.Thread | None = getattr(self, attr, None)
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)
            setattr(self, attr, None)
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        with self._pending_lock:
            for slot in self._pending.values():
                slot.event.set()
            self._pending.clear()
        self._stop.clear()

    def query_state(self) -> PoolState:
        packet = self.send_data(build_query_payload(DEVICE_TYPE_POOL_HEATPUMP, ATTR_ALL))
        return parse_pool_state(packet.payload)

    def query_diagnostics(self) -> PoolDiagnostics:
        packet = self.send_data(build_query_payload(DEVICE_TYPE_POOL_HEATPUMP, ATTR_ALL))
        return parse_pool_diagnostics(packet.payload)

    def set_target_temp(self, temp: int) -> Packet:
        reply = self.send_data(build_set_payload(ATTR_TARGET_TEMP, temp))
        self._post_set_query()
        return reply

    def set_mode(self, mode: int) -> Packet:
        reply = self.send_data(build_set_payload(ATTR_MODE, mode, byteorder="little"))
        self._post_set_query()
        return reply

    def set_power(self, on: bool) -> Packet:
        reply = self.send_data(build_set_payload(ATTR_POWER, 1 if on else 0, byteorder="little"))
        self._post_set_query()
        return reply

    def _post_set_query(self) -> None:
        # The captured app always follows a SET with `query ATTR_ALL` within a few ms; without it
        # the pump leaves the session in a state where pings come back as op 0x0700 and queries
        # stop being answered. Issuing the query here completes the SET → notify → query response
        # cycle the firmware expects. Wait briefly for any pending notify so the reader thread
        # can ACK it before we return control to the caller.
        try:
            self.send_data(build_query_payload(DEVICE_TYPE_POOL_HEATPUMP, ATTR_ALL))
        except TimeoutError:
            pass
        time.sleep(0.05)

    def ping(self) -> Packet:
        return self._send(Packet(0x32, self._next_sequence(), self._session, MSG_PING, b"\x01\x00\x00\x00"))

    def send_data(self, payload: bytes) -> Packet:
        return self._send(Packet(0x32, self._next_sequence(), self._session, MSG_DATA, payload))

    def _send(self, packet: Packet) -> Packet:
        if self._sock is None:
            raise RuntimeError("client is not connected")
        key = (packet.sequence, packet.message_type)
        slot = _Pending(event=threading.Event())
        with self._pending_lock:
            self._pending[key] = slot
        try:
            with self._send_lock:
                if self._sock is None:
                    raise RuntimeError("client is not connected")
                self._sock.sendto(packet.build(), (self.host, CONTROL_PORT))
                self._last_send = time.monotonic()
            if not slot.event.wait(self.timeout):
                raise TimeoutError("no response from heat pump")
            if slot.reply is None:
                raise RuntimeError("client closed during send")
            return slot.reply
        finally:
            with self._pending_lock:
                self._pending.pop(key, None)

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                raw, _addr = sock.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            try:
                reply = Packet.parse(raw)
            except ValueError:
                continue
            self._handle_reply(reply)

    def _handle_reply(self, reply: Packet) -> None:
        if self._is_notify(reply):
            self._ack_notify(reply)
            return
        # Reply to pump-initiated keepalive probes (payload starts with 0x07).
        # The pump sends these when it wants confirmation the client is alive;
        # not answering eventually causes the pump to mark the session dead.
        if (
            reply.message_type == MSG_PING
            and len(reply.payload) >= 1
            and reply.payload[0] == 0x07
        ):
            self._reply_to_pump_ping(reply)
            return
        key = (reply.sequence, reply.message_type)
        with self._pending_lock:
            slot = self._pending.get(key)
            if slot is None:
                return
            slot.reply = reply
            slot.event.set()

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(0.3):
            if self._sock is None:
                return
            if time.monotonic() - self._last_send < KEEPALIVE_INTERVAL:
                continue
            try:
                self._send_ping_no_wait()
            except OSError:
                return

    def _send_ping_no_wait(self) -> None:
        with self._send_lock:
            if self._sock is None:
                return
            seq = self._next_sequence_locked()
            packet = Packet(0x32, seq, self._session, MSG_PING, b"\x01\x00\x00\x00")
            self._sock.sendto(packet.build(), (self.host, CONTROL_PORT))
            self._last_send = time.monotonic()

    @staticmethod
    def _is_notify(packet: Packet) -> bool:
        return (
            packet.message_type == MSG_DATA
            and len(packet.payload) >= 2
            and int.from_bytes(packet.payload[:2], "big") == OP_NOTIFY
        )

    def _reply_to_pump_ping(self, ping: Packet) -> None:
        sock = self._sock
        if sock is None:
            return
        # Pump pings the client with op 0x0700; the standard pong is op 0x0200, mirroring how
        # the pump answers app-initiated 0x0100 pings.
        reply = Packet(0x32, ping.sequence, self._session, MSG_PING, b"\x02\x00\x00\x00")
        try:
            with self._send_lock:
                if self._sock is not None:
                    self._sock.sendto(reply.build(), (self.host, CONTROL_PORT))
                    self._last_send = time.monotonic()
        except OSError:
            pass

    def _ack_notify(self, notify: Packet) -> None:
        ack_payload = (
            OP_ACK_NOTIFY.to_bytes(2, "big")
            + b"\x00\x00"
            + (2).to_bytes(2, "big")
            + DEVICE_TYPE_POOL_HEATPUMP.to_bytes(2, "big")
            + ATTR_STATE_BLOCK.to_bytes(2, "big")
            + b"\x00\x00"
        )
        ack = Packet(0x32, notify.sequence, self._session, MSG_DATA, ack_payload)
        try:
            with self._send_lock:
                if self._sock is not None:
                    self._sock.sendto(ack.build(), (self.host, CONTROL_PORT))
                    self._last_send = time.monotonic()
        except OSError:
            pass

    def _next_sequence(self) -> int:
        with self._send_lock:
            return self._next_sequence_locked()

    def _next_sequence_locked(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFF
        return self._sequence
