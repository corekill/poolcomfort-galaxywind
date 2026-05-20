# Protocol Notes

These notes are based on `POOL COMFORT_1.12_APKPure.apk`, decompiled Android
classes, `libclib_jni.so`, and a MikroTik capture.

## Ports

- UDP `8818`: LAN discovery
- UDP `1194`: local session and control

Discovery can be sent directly to a known host or as a LAN broadcast. The
prototype can discover candidate IPs first, open a normal local session to each,
query state, and match the requested heat pump by serial number.

## Packet header

Observed UDP `1194` packet layout:

```text
00      marker: 0x32 app->pump, 0x30 pump->app
01..02  sequence, big-endian
03..06  zero/reserved
07..11  5-byte session token, assigned by pump after first handshake
12..13  message type, big-endian
14..15  payload length, big-endian
16..    payload
```

Known message types:

- `0x00f2`: handshake
- `0x00f3`: ping/keepalive
- `0x00f4`: data/query/set

## Pool Heat Pump Attributes

The heat pump uses device type `0x000d`.

Known attributes:

- `0x0006`: device status code, observed `0x0047`
- `0x0007`: serial, with a 4-byte prefix before ASCII serial
- `0x0015`: 68-byte state block. Observed big-endian signed shorts. The Android
  app's `HtchpPoolDev.getMeasureItems()` and `getWorkItems()` show the
  "Measured system value" and "Working details" model.
  - word 1 = water inlet temperature * 10
  - word 2 = water outlet temperature * 10
  - word 3 = ambient temperature * 10
  - word 6 = slave/run-state bitfield
  - word 22 = likely `pump_info`, used for compressor, four-way valve, high/low
    fan speed, circulation pump, electric heating, and bottom heater
  - word 24 = likely `fault2`/switch state, used for low/high pressure,
    emergency, waterflow, and phase switches
- `0x0016`: target temperature
- `0x0017`: mode, first byte: `0=auto`, `1=cooling`, `2=heating`, `3=warm`
- `0x0018`: power, first byte: `0=off`, `1=on`
- `0x0019`: temperature limits/config block, observed words include `40`,
  `-3`, `13`, `8`
- `0x001a`..`0x001f`: runtime/config blocks, exposed as diagnostics until the
  meaning is fully mapped
- `0x0020`: water temperature block, four big-endian signed shorts

The CLI `diagnostics` command exposes every pool heat-pump attribute as raw hex,
bytes, signed/unsigned big-endian words, little-endian words, and decoded values
where known. This keeps unknown component flags visible while the mapping is
being reverse engineered.

The currently decoded working details follow the Android app bit logic:

- `compressor`: `pump_info` bit 0
- `four_way_valve`: `pump_info` bit 11
- `high_fan_speed`: `pump_info` bits 9 and 10
- `low_fan_speed`: `pump_info` bit 10 set and bit 9 clear
- `circulation_pump`: `pump_info` bit 7
- `electric_heating`: `pump_info` bit 4
- `bottom_heater`: `pump_info` bit 5
- `low_pressure_switch`: `fault2` bit 7
- `high_pressure_switch`: `fault2` bit 3
- `emergency_switch`: `fault2` bit 0
- `waterflow_switch`: `fault2` bit 2
- `phase_protection`: `fault2` bit 1

## Set Commands

Observed set payload shape:

```text
0901 0000 0002 000d ATTR 0004 VALUE 0000
```

Examples:

```text
set target temp 33: 090100000002000d0016000400210000
set mode heating:   090100000002000d0017000402000000
set power off:      090100000002000d0018000400000000
```

Device behavior note: target temperature and mode writes are only accepted while
the heat pump is powered on. When the unit is off, the app/device rejects those
changes; switch power on first, then set target temperature or mode. Power-off
itself remains a valid standalone command.

## Login

The first handshake succeeds with the discovery token. The pump then returns a
4-byte challenge in the first `0x00f2` response. The second handshake response
is:

```text
MD5(first_handshake_nonce_4b + pump_challenge_4b + MD5(device_password))
```

The algorithm was verified against `libclib_jni.so` and two captured app login
sessions. The device password is the Pool Comfort device password, not the
router password.

Known local capture pairs:

```text
nonce=970e140a challenge=833a8845 response=0cb002df95f4b8df536693f2c1fef352
nonce=55ac180a challenge=efc1fb01 response=832a8318587ac6c28c7d8a31f811a191
```

Use `scripts/pcap_auth_pairs.py` to extract more triples from additional
captures.
