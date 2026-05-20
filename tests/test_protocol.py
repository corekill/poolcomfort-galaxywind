import struct

from poolcomfort_local.client import build_auth_response
from poolcomfort_local.protocol import Packet, Mode, build_set_payload, parse_pool_diagnostics, parse_pool_state


def test_packet_roundtrip():
    raw = bytes.fromhex("32001400000000098000225500f40010090100000002000d0016000400210000")
    packet = Packet.parse(raw)
    assert packet.sequence == 0x14
    assert packet.session == bytes.fromhex("0980002255")
    assert packet.message_type == 0x00F4
    assert packet.build() == raw


def test_build_set_temp_payload_matches_capture():
    assert build_set_payload(0x0016, 33).hex() == "090100000002000d0016000400210000"


def test_build_set_mode_payload_matches_capture():
    # From poolcomfort-wide.pcap seq=001d/0020/001a: mode uses first-byte layout.
    assert build_set_payload(0x0017, 2, byteorder="little").hex() == "090100000002000d0017000402000000"
    assert build_set_payload(0x0017, 1, byteorder="little").hex() == "090100000002000d0017000401000000"
    assert build_set_payload(0x0017, 0, byteorder="little").hex() == "090100000002000d0017000400000000"


def test_build_set_power_payload_matches_capture():
    # From poolcomfort-wide.pcap seq=0013: power off uses first-byte layout.
    assert build_set_payload(0x0018, 0, byteorder="little").hex() == "090100000002000d0018000400000000"
    assert build_set_payload(0x0018, 1, byteorder="little").hex() == "090100000002000d0018000401000000"


def test_build_auth_response_matches_captures():
    assert (
        build_auth_response(bytes.fromhex("970e140a"), bytes.fromhex("833a8845"), "123456").hex()
        == "0cb002df95f4b8df536693f2c1fef352"
    )
    assert (
        build_auth_response(bytes.fromhex("55ac180a"), bytes.fromhex("efc1fb01"), "123456").hex()
        == "832a8318587ac6c28c7d8a31f811a191"
    )


def test_parse_pool_state_from_capture():
    payload = (
        bytes.fromhex("080d0000")
        + bytes.fromhex("0002000d00070024")
        + bytes.fromhex("00000001")
        + b"123456789012"
        + (b"\x00" * 20)
        + bytes.fromhex("0002000d00160004001f0000")
        + bytes.fromhex("0002000d0017000402000000")
        + bytes.fromhex("0002000d0018000401000000")
        + bytes.fromhex("0002000d002000080022001f00280000")
    )
    state = parse_pool_state(payload)
    assert state.serial == "123456789012"
    assert state.target_temp == 31
    assert state.mode == Mode.HEATING
    assert state.power is True
    assert state.out_water_temp == 34
    assert state.in_water_temp == 31


def test_parse_pool_state_temps_from_state_block():
    import struct
    state_block = struct.pack(">34h", 0, 230, 225, *([0] * 31))
    payload = (
        bytes.fromhex("080d0000")
        + bytes.fromhex("0002000d00070024")
        + bytes.fromhex("00000001")
        + b"123456789012"
        + (b"\x00" * 20)
        + bytes.fromhex("0002000d00150044")
        + state_block
        + bytes.fromhex("0002000d00160004001f0000")
        + bytes.fromhex("0002000d0018000400000000")
    )
    state = parse_pool_state(payload)
    assert state.in_water_temp == 23.0
    assert state.out_water_temp == 22.5
    assert state.power is False


def test_parse_pool_diagnostics_exposes_attributes():
    payload = (
        bytes.fromhex("080d0000")
        + bytes.fromhex("0002000d00070024")
        + bytes.fromhex("00000001")
        + b"123456789012"
        + (b"\x00" * 20)
        + bytes.fromhex("0002000d00150044")
        + struct.pack(">34h", 0, 250, 260, 190, *([0] * 30))
        + bytes.fromhex("0002000d0016000400200000")
        + bytes.fromhex("0002000d0017000402490020")
        + bytes.fromhex("0002000d0018000401490020")
    )
    diagnostics = parse_pool_diagnostics(payload)
    assert diagnostics.state.serial == "123456789012"
    assert diagnostics.state.target_temp == 32
    assert diagnostics.attributes["0x0015"]["name"] == "state_block"
    assert diagnostics.attributes["0x0015"]["decoded"]["water_in_temperature_c"] == 25.0
    assert diagnostics.attributes["0x0015"]["decoded"]["water_out_temperature_c"] == 26.0
    assert diagnostics.attributes["0x0015"]["decoded"]["ambient_temperature_c"] == 19.0
    assert diagnostics.attributes["0x0017"]["decoded"]["mode_name"] == "heating"
    assert diagnostics.attributes["0x0018"]["decoded"]["power"] is True


def test_parse_pool_working_details_from_state_block():
    words = [0] * 34
    words[1] = 250
    words[2] = 260
    words[3] = 190
    words[6] = 1 << 1
    words[22] = (1 << 0) | (1 << 7) | (1 << 9) | (1 << 10) | (1 << 11)
    words[24] = (1 << 2) | (1 << 3)
    payload = (
        bytes.fromhex("080d0000")
        + bytes.fromhex("0002000d00150044")
        + struct.pack(">34h", *words)
    )
    diagnostics = parse_pool_diagnostics(payload)
    decoded = diagnostics.attributes["0x0015"]["decoded"]
    details = decoded["working_details"]
    assert decoded["run_state_name"] == "heating"
    assert details["compressor"] is True
    assert details["high_fan_speed"] is True
    assert details["low_fan_speed"] is False
    assert details["circulation_pump"] is True
    assert details["four_way_valve"] is True
    assert details["waterflow_switch"] is True
    assert details["high_pressure_switch"] is True
