from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any
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

ATTRIBUTE_NAMES = {
    0x0006: "device_status",
    0x0007: "serial",
    ATTR_STATE_BLOCK: "state_block",
    ATTR_TARGET_TEMP: "target_temperature",
    ATTR_MODE: "mode",
    ATTR_POWER: "power",
    0x0019: "temperature_limits",
    0x001A: "runtime_flags_a",
    0x001B: "runtime_limits",
    0x001C: "timer_or_schedule_state",
    0x001D: "reserved_block",
    0x001E: "temperature_pair",
    0x001F: "runtime_flags_b",
    ATTR_RUNNING_TEMPS: "running_temperatures",
}

POOL_RUN_STATES = [
    "automatic",
    "heating",
    "cooling",
    "return_water",
    "defrosting",
    "heat_preserving",
    "refrigerant_recycle",
    "heating",
    "hot_water",
]

POOL_WORK_DETAIL_LABELS = {
    "compressor": "Compressor",
    "four_way_valve": "Four-way valve",
    "high_fan_speed": "High fan speed",
    "low_fan_speed": "Low fan speed",
    "circulation_pump": "Circulation pump",
    "electric_heating": "Electric heating",
    "bottom_heater": "Bottom heater",
    "low_pressure_switch": "Low pressure switch",
    "high_pressure_switch": "High pressure switch",
    "emergency_switch": "Emergency switch",
    "waterflow_switch": "Waterflow switch",
    "phase_protection": "Phase protection",
}

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


@dataclass(frozen=True)
class PoolDiagnostics:
    state: PoolState
    attributes: dict[str, dict[str, Any]]


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


def parse_pool_diagnostics(payload: bytes) -> PoolDiagnostics:
    attributes: dict[str, dict[str, Any]] = {}
    for attr in iter_attributes(payload):
        if attr.device_type != DEVICE_TYPE_POOL_HEATPUMP:
            continue
        key = f"0x{attr.attr:04x}"
        attributes[key] = describe_attribute(attr)
    return PoolDiagnostics(state=parse_pool_state(payload), attributes=attributes)


def describe_attribute(attr: Attribute) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": ATTRIBUTE_NAMES.get(attr.attr, f"unknown_0x{attr.attr:04x}"),
        "scope": f"0x{attr.scope:04x}",
        "device_type": f"0x{attr.device_type:04x}",
        "attr": f"0x{attr.attr:04x}",
        "length": len(attr.value),
        "raw": attr.value.hex(),
        "bytes": list(attr.value),
    }
    if len(attr.value) >= 2 and len(attr.value) % 2 == 0:
        data["uint16_be"] = _words(attr.value, signed=False, byteorder="big")
        data["int16_be"] = _words(attr.value, signed=True, byteorder="big")
        data["uint16_le"] = _words(attr.value, signed=False, byteorder="little")
    decoded = _decode_attribute(attr)
    if decoded:
        data["decoded"] = decoded
    return data


def _words(value: bytes, signed: bool, byteorder: str) -> list[int]:
    return [
        int.from_bytes(value[pos : pos + 2], byteorder, signed=signed)
        for pos in range(0, len(value), 2)
    ]


def _bit(value: int, bit: int) -> bool:
    return bool(value & (1 << bit))


def _first_set_bit(value: int) -> int | None:
    for bit in range(16):
        if _bit(value, bit):
            return bit
    return None


def _decode_pool_state_block(words: list[int]) -> dict[str, Any]:
    decoded: dict[str, Any] = {
        "water_box_temperature_c": words[0] / 10,
        "water_in_temperature_c": words[1] / 10,
        "water_out_temperature_c": words[2] / 10,
        "ambient_temperature_c": words[3] / 10,
        "back_water_temperature_c": words[4] / 10,
        "support_mode_bits": words[5],
        "slave_status_bits": words[6],
        "words": words,
    }
    run_state = _first_set_bit(words[6])
    if run_state is not None:
        decoded["run_state"] = run_state
        if run_state < len(POOL_RUN_STATES):
            decoded["run_state_name"] = POOL_RUN_STATES[run_state]

    # Pool Comfort's Android app builds "Working details" from TbCommercialStat.
    # On this firmware the live 0x0015 block lines up with pump_info at word 22
    # and fault2/switch status at word 24. Keep the raw words exposed so new
    # devices can confirm or correct these positions without losing data.
    if len(words) > 24:
        pump_info = words[22] & 0xFFFF
        fault2 = words[24] & 0xFFFF
        decoded["pump_info_bits"] = pump_info
        decoded["fault2_bits"] = fault2
        decoded["working_details"] = {
            "compressor": _bit(pump_info, 0),
            "four_way_valve": _bit(pump_info, 11),
            "high_fan_speed": _bit(pump_info, 9) and _bit(pump_info, 10),
            "low_fan_speed": (not _bit(pump_info, 9)) and _bit(pump_info, 10),
            "circulation_pump": _bit(pump_info, 7),
            "electric_heating": _bit(pump_info, 4),
            "bottom_heater": _bit(pump_info, 5),
            "low_pressure_switch": _bit(fault2, 7),
            "high_pressure_switch": _bit(fault2, 3),
            "emergency_switch": _bit(fault2, 0),
            "waterflow_switch": _bit(fault2, 2),
            "phase_protection": _bit(fault2, 1),
        }
    return decoded


def _decode_attribute(attr: Attribute) -> dict[str, Any]:
    if attr.attr == 0x0007:
        raw_serial = attr.value[4:] if len(attr.value) > 16 else attr.value
        serial = raw_serial.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        return {"serial": serial} if serial else {}
    if attr.attr == ATTR_TARGET_TEMP and len(attr.value) >= 2:
        return {"target_temperature_c": attr.uint16_be()}
    if attr.attr == ATTR_MODE and attr.value:
        raw_mode = attr.value[0] if len(attr.value) >= 4 else attr.uint16_be()
        decoded: dict[str, Any] = {"mode": raw_mode}
        if raw_mode in set(item.value for item in Mode):
            decoded["mode_name"] = Mode(raw_mode).name.lower()
        return decoded
    if attr.attr == ATTR_POWER and attr.value:
        return {"power": bool(attr.value[0] if len(attr.value) >= 4 else attr.uint16_be())}
    if attr.attr == ATTR_STATE_BLOCK and len(attr.value) >= 8:
        words = _words(attr.value, signed=True, byteorder="big")
        return _decode_pool_state_block(words)
    if attr.attr == ATTR_RUNNING_TEMPS and len(attr.value) >= 8:
        out_water_temp, in_water_temp, third, fourth = struct.unpack(">hhhh", attr.value[:8])
        return {
            "water_out_temperature_c": out_water_temp,
            "water_in_temperature_c": in_water_temp,
            "raw_temperature_3": third,
            "raw_temperature_4": fourth,
        }
    return {}


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
