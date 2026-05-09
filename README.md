# poolcomfort-galaxywind

Local UDP client and Home Assistant integration for **Pool Comfort** /
**Galaxywind** / **GWCD** pool heat pumps. Reverse-engineered from the Android
app package `com.gwcd.htc_en_oem` (sometimes branded "Pool Comfort", "Smart
Life", or shipped by OEM as part of a Galaxywind/GWCD firmware).

Works fully offline against the heat pump on UDP `1194` — no cloud, no vendor
account. Tested on a real Pool Comfort unit; captures from other Galaxywind /
GWCD heat pumps that speak the same protocol are welcome.

The repo contains:

- `poolcomfort_local/` — Python library for discovery, login, query, and set
- `custom_components/poolcomfort/` — Home Assistant custom component (climate
  entity, water-temp sensors, config flow)
- `scripts/` — PCAP analysis tools
- `docs/` — protocol notes and capture instructions

## Status

This is reverse-engineering work in progress.

Working today:

- parsing UDP `1194` packet headers
- local discovery and login with the Pool Comfort device password
- parsing known pool heat pump state attributes
- building observed set commands for target temperature, mode, and power
- inspecting PCAP captures with repeatable tooling

Not working yet:

- Home Assistant custom integration

The live client has been tested with one Pool Comfort / Galaxywind heat pump.
Please treat writes as experimental until more devices are captured and tested.

## What is known

- Discovery uses UDP `8818`.
- Local control uses UDP `1194`.
- Pool heat pump data appears under device type `0x000d`.
- Observed attributes:
  - `0x0007`: serial number
  - `0x0016`: target temperature
  - `0x0017`: mode, `0=auto`, `1=cooling`, `2=heating`, `3=warm`
  - `0x0018`: power
  - `0x0020`: water temperature block

## Try the prototype

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m poolcomfort_local.cli --serial 123456789012 --password 123456 status
python -m poolcomfort_local.cli --host 192.168.1.x --password 123456 status
python -m poolcomfort_local.cli --serial 123456789012 --password 123456 power on
python -m poolcomfort_local.cli --serial 123456789012 --password 123456 set-temp 32
python -m poolcomfort_local.cli --serial 123456789012 --password 123456 set-mode 2
```

The live client is still experimental. Packet parsing and command construction
are covered by tests. Start with `status` before trying writes on a new device.

Target temperature and mode changes are only expected to work while the heat
pump is powered on. The CLI checks this before `set-temp` and `set-mode` unless
`--assume-on` is passed for protocol experiments.

## Analyze a capture

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[pcap]'
python scripts/pcap_dump.py poolcomfort-wide.pcap --pump 192.168.1.x
python scripts/pcap_auth_pairs.py poolcomfort-wide.pcap --pump 192.168.1.x
```

## Capturing More Devices

See [`docs/capture-mikrotik.md`](docs/capture-mikrotik.md) for a safer
step-by-step MikroTik workflow. Please do not publish captures that contain
private passwords, router credentials, public IP sessions, or unrelated LAN
traffic.

Short RouterOS example:

```routeros
/tool/sniffer/stop
/tool/sniffer/set file-name=poolcomfort-wide.pcap file-limit=8192KiB memory-limit=4096KiB only-headers=no filter-interface=all filter-ip-address=192.168.1.17,192.168.1.x
/tool/sniffer/start
```

Then open the Pool Comfort app and change one value. Stop and download the file:

```routeros
/tool/sniffer/stop
```

## Home Assistant integration

A custom component is included under
[`custom_components/poolcomfort/`](custom_components/poolcomfort). It exposes:

- one `climate` entity (target temperature, HVAC mode auto/heat/cool/off,
  current water temperature)
- diagnostic `sensor` entities (water inlet, water outlet, serial)

Install:

1. Copy `custom_components/poolcomfort/` into your Home Assistant
   `config/custom_components/` directory.
2. Make sure the `poolcomfort-local` Python package is importable inside the
   Home Assistant Python environment (e.g. `pip install -e .` from the repo
   root, or list it in `manifest.json` once published to PyPI).
3. Restart Home Assistant, then add the integration via
   *Settings → Devices & Services → Add Integration → "Pool Comfort"* and enter
   the heat pump IP and device password.

Polling defaults to 30 s; each poll opens a fresh UDP session, which avoids the
firmware's short idle-session timeout.

## Current reverse engineering status

Implemented:

- local discovery and MD5 password challenge-response login
- packet header parsing/building, notify ACK, pump-side keepalive handling
- pool heat pump state parser (target temp, mode, power, water in/out, serial)
- command builder for target temperature, mode, power, with the per-attribute
  byte order observed in captures (`set_target_temp` BE u16; `set_mode` /
  `set_power` first-byte / LE u16)
- PCAP dump and auth-pair extraction tools
- Home Assistant custom component (climate + sensors + config flow)

Still needed / known limitations:

- bit-level mapping of "Working details" flags (compressor, fans, pressure
  switches…) — partially decoded in `state block 0x0015`, needs more captures
- long idle session decay: persistent sessions get killed after ~2 s of silence
  unless the captured app's exact ping cadence is reproduced; the HA integration
  works around this by reconnecting per poll.
