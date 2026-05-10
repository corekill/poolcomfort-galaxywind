from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct


DISCOVERY_PORT = 8818
CONTROL_PORT = 1194
CLIENT_DISCOVERY_PORT = 8819

DEVICE_TYPE_POOL_HEATPUMP = 0x000D

ATTR_STATE_BLOCK = 0x0015
ATTR_TARGET_TEMP = 0x0016
ATTR_MODE = 0x0017
ATTR_POWER = 0x0018
ATTR_RUNNING_TEMPS = 0x0020
ATTR_ALL = 0xFFFF

MSG_HANDSHAKE_1 = 0x00F2
MSG_DATA = 0x00F4
MSG_PING = 0x00F3

OP_QUERY = 0x0801
OP_QUERY_BLOCK = 0x080D
OP_SET = 0x0901
OP_NOTIFY = 0x0B0A
OP_ACK_NOTIFY = 0x0B01


class Mode(IntEnum):
    AUTO = 0
    COOLING = 1
    HEATING = 2
    WARM = 3


@dataclass(frozen=True)
class Packet:
    marker: int
    sequence: int
    session: bytes
    message_type: int
    payload: bytes

    @classmethod
    def parse(cls, raw: bytes) -> "Packet":
        if len(raw) < 16:
            raise ValueError("packet too short")
        marker = raw[0]
        sequence = int.from_bytes(raw[1:3], "big")
        session = raw[7:12]
        message_type = int.from_bytes(raw[12:14], "big")
        payload_len = int.from_bytes(raw[14:16], "big")
        payload = raw[16:]
        if payload_len != len(payload):
            raise ValueError(f"payload length mismatch: declared {payload_len}, got {len(payload)}")
        return cls(marker, sequence, session, message_type, payload)

    def build(self) -> bytes:
        if len(self.session) != 5:
            raise ValueError("session must be exactly 5 bytes")
        header = bytearray(16)
        header[0] = self.marker
        header[1:3] = self.sequence.to_bytes(2, "big")
        header[7:12] = self.session
        header[12:14] = self.message_type.to_bytes(2, "big")
        header[14:16] = len(self.payload).to_bytes(2, "big")
        return bytes(header) + self.payload


@dataclass(frozen=True)
class Attribute:
    scope: int
    device_type: int
    attr: int
    value: bytes

    def uint16_le(self) -> int:
        if len(self.value) < 2:
            raise ValueError("attribute value is shorter than uint16")
        return int.from_bytes(self.value[:2], "little")

    def uint16_be(self) -> int:
        if len(self.value) < 2:
            raise ValueError("attribute value is shorter than uint16")
        return int.from_bytes(self.value[:2], "big")

    def uint32_le(self) -> int:
        if len(self.value) < 4:
            raise ValueError("attribute value is shorter than uint32")
        return int.from_bytes(self.value[:4], "little")


@dataclass(frozen=True)
class PoolState:
    serial: str | None = None
    target_temp: int | None = None
    mode: Mode | None = None
    power: bool | None = None
    in_water_temp: float | None = None
    out_water_temp: float | None = None


def build_query_payload(device_type: int = DEVICE_TYPE_POOL_HEATPUMP, attr: int = ATTR_ALL) -> bytes:
    return struct.pack(">HHHHH", OP_QUERY, 0, 2, device_type, attr) + b"\x00\x00"


def build_set_payload(
    attr: int,
    value: int,
    device_type: int = DEVICE_TYPE_POOL_HEATPUMP,
    byteorder: str = "big",
) -> bytes:
    return struct.pack(">HHHHHH", OP_SET, 0, 2, device_type, attr, 4) + int(value).to_bytes(2, byteorder) + b"\x00\x00"


def iter_attributes(payload: bytes) -> list[Attribute]:
    if len(payload) < 6:
        return []
    pos = 4
    attrs: list[Attribute] = []
    while pos + 8 <= len(payload):
        scope = int.from_bytes(payload[pos : pos + 2], "big")
        device_type = int.from_bytes(payload[pos + 2 : pos + 4], "big")
        attr = int.from_bytes(payload[pos + 4 : pos + 6], "big")
        value_len = int.from_bytes(payload[pos + 6 : pos + 8], "big")
        pos += 8
        if pos + value_len > len(payload):
            break
        value = payload[pos : pos + value_len]
        attrs.append(Attribute(scope, device_type, attr, value))
        pos += value_len
    return attrs


def parse_pool_state(payload: bytes) -> PoolState:
    serial = None
    target_temp = None
    mode = None
    power = None
    in_water_temp = None
    out_water_temp = None

    for attr in iter_attributes(payload):
        if attr.device_type != DEVICE_TYPE_POOL_HEATPUMP:
            continue
        if attr.attr == 0x0007:
            raw_serial = attr.value[4:] if len(attr.value) > 16 else attr.value
            serial = raw_serial.split(b"\x00", 1)[0].decode("ascii", errors="ignore") or None
        elif attr.attr == ATTR_TARGET_TEMP:
            target_temp = attr.uint16_be()
        elif attr.attr == ATTR_MODE:
            raw_mode = attr.value[0] if len(attr.value) >= 4 else attr.uint16_be()
            if raw_mode in set(item.value for item in Mode):
                mode = Mode(raw_mode)
        elif attr.attr == ATTR_POWER:
            power = bool(attr.value[0] if len(attr.value) >= 4 else attr.uint16_be())
        elif attr.attr == ATTR_STATE_BLOCK and len(attr.value) >= 6:
            words = struct.unpack_from(">3h", attr.value, 0)
            in_water_temp = words[1] / 10
            out_water_temp = words[2] / 10
        elif attr.attr == ATTR_RUNNING_TEMPS and len(attr.value) >= 8:
            out_water_temp, in_water_temp, *_ = struct.unpack(">hhhh", attr.value[:8])

    return PoolState(
        serial=serial,
        target_temp=target_temp,
        mode=mode,
        power=power,
        in_water_temp=in_water_temp,
        out_water_temp=out_water_temp,
    )
