# Pool Comfort / Galaxywind — local Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Validate](https://img.shields.io/github/actions/workflow/status/corekill/poolcomfort-galaxywind/validate.yml?style=for-the-badge&label=HACS%20%2F%20hassfest)](https://github.com/corekill/poolcomfort-galaxywind/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg?style=for-the-badge)](https://www.python.org/)

Local UDP client and Home Assistant integration for **Pool Comfort** /
**Galaxywind** / **GWCD** pool heat pumps. Reverse-engineered from the
`com.gwcd.htc_en_oem` Android app — works fully offline against the heat pump
on UDP `1194`. **No cloud, no vendor account.**

Tested on a Pool Comfort unit; captures from other Galaxywind / GWCD pumps that
speak the same protocol are welcome.

---

## Install

### Via HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=corekill&repository=poolcomfort-galaxywind&category=integration)

1. Click the button above (or in HACS go to *⋮ → Custom repositories*, paste
   `https://github.com/corekill/poolcomfort-galaxywind`, category
   *Integration*).
2. Find **Pool Comfort / Galaxywind heat pump** in HACS and *Download*.
3. Restart Home Assistant.
4. Add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=poolcomfort)

You'll be asked for the heat pump IP and the device password (default
`123456`). The serial number is detected automatically.

### Manual install

1. Copy `custom_components/poolcomfort/` into your Home Assistant
   `config/custom_components/`.
2. Restart Home Assistant.
3. Use the *Add integration* button above.

---

## What you get

- **Climate entity** — target temperature, HVAC mode (off / auto / heat /
  cool), current water temperature.
- **Sensors** — water inlet temperature, water outlet temperature, serial
  number (diagnostic).
- **Local-only** — all traffic stays on your LAN over UDP `1194`. No cloud
  account, no internet dependency.
- **Polling** — 30 s by default. The integration uses a fresh UDP session per
  poll, which avoids the firmware's short idle-session timeout.

---

## Supported hardware

Any heat pump that pairs with the Pool Comfort / Galaxywind / GWCD
`com.gwcd.htc_en_oem` Android app *should* work — these include OEM-rebranded
units (Pool Comfort, Galaxywind, GWCD, several Chinese pool heat pump brands).
Confirmed:

- Pool Comfort (one unit, FW shipped 2023-ish)

If yours works (or doesn't), please open an issue with a small PCAP and the
model number — see [`docs/capture-mikrotik.md`](docs/capture-mikrotik.md).

---

## Repo layout

| Path | What it is |
|---|---|
| [`poolcomfort_local/`](poolcomfort_local/) | Pure-Python library: discovery, login, query, set commands |
| [`custom_components/poolcomfort/`](custom_components/poolcomfort/) | Home Assistant custom component |
| [`scripts/`](scripts/) | PCAP analysis: dump frames, extract auth challenge-response pairs |
| [`docs/`](docs/) | Reverse-engineering notes ([protocol](docs/protocol.md), [capture HOWTO](docs/capture-mikrotik.md)) |
| [`tests/`](tests/) | `pytest` test vectors backed by real captures |

---

## Library / CLI

The Home Assistant component is built on a standalone library you can use
directly:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

# Discover by serial, query state
python -m poolcomfort_local.cli --serial 123456789012 --password 123456 status

# Or by IP
python -m poolcomfort_local.cli --host 192.168.1.x --password 123456 status

# Control
python -m poolcomfort_local.cli --host 192.168.1.x --password 123456 power on
python -m poolcomfort_local.cli --host 192.168.1.x --password 123456 set-temp 28
python -m poolcomfort_local.cli --host 192.168.1.x --password 123456 set-mode 2
```

Set-temp and set-mode require the pump to be powered on; the CLI checks first
unless `--assume-on` is passed.

PCAP analysis (needs `scapy`):

```bash
pip install -e '.[pcap]'
python scripts/pcap_dump.py poolcomfort-wide.pcap --pump 192.168.1.x
python scripts/pcap_auth_pairs.py poolcomfort-wide.pcap --pump 192.168.1.x
```

---

## Protocol — what's known

- UDP `8818` — LAN discovery
- UDP `1194` — control session
- Device type `0x000d` — pool heat pump
- Login: `MD5(nonce + challenge + MD5(password))`, two-step handshake
- Per-attribute byte order matters: target temp is **BE u16**, mode and power
  are **first-byte / LE u16** — see [`docs/protocol.md`](docs/protocol.md).
- After every SET, the pump expects an immediate query and ACK on its `0b0a`
  notify, otherwise the session goes stale within ~2 s.

Full notes in [`docs/protocol.md`](docs/protocol.md).

---

## Status / known limits

- **Working details bitfield** (compressor, fans, pressure switches…) is
  partially decoded inside state block `0x0015` but the bit-to-flag mapping is
  not finalised. Working on it.
- **Long persistent sessions** — the firmware kills idle sessions in ~2 s
  unless the captured app's exact ping cadence is reproduced. The integration
  works around this by reconnecting per poll.
- One physical unit tested. More captures from other devices would help.

---

## Contributing

PCAPs from other heat pumps, parsed attributes, and protocol findings are very
welcome. **Please do not commit captures with router credentials, public IPs,
or unrelated LAN traffic** — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Credits

Built from reverse-engineering the `com.gwcd.htc_en_oem` Android app and
MikroTik LAN captures. Galaxywind / GWCD / Pool Comfort are trademarks of their
respective owners; this project is not affiliated with or endorsed by any of
them.

## License

MIT — see [`LICENSE`](LICENSE).
