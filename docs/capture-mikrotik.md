# MikroTik Capture Guide

This guide captures only the phone running Pool Comfort and the heat pump. Use
your own addresses in place of the examples.

Example addresses:

- phone: `192.168.1.17`
- heat pump: `192.168.1.x`

## 1. Start a Filtered Capture

```routeros
/tool/sniffer/stop
/tool/sniffer/set file-name=poolcomfort-wide.pcap file-limit=8192KiB memory-limit=4096KiB only-headers=no filter-interface=all filter-ip-address=192.168.1.17,192.168.1.x
/tool/sniffer/start
```

## 2. Generate Useful Traffic

On the phone:

1. force-close the Pool Comfort app
2. open it again while connected to the same LAN
3. wait until the pump appears online
4. change one harmless setting, such as target temperature by 1 degree
5. optionally change mode and then change it back

Avoid changing critical timer, anti-freeze, or service parameters.

## 3. Stop and Download

```routeros
/tool/sniffer/stop
```

Download `poolcomfort-wide.pcap` from the MikroTik Files view.

## 4. Inspect Locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[pcap]'
python scripts/pcap_dump.py poolcomfort-wide.pcap --pump 192.168.1.x
python scripts/pcap_auth_pairs.py poolcomfort-wide.pcap --pump 192.168.1.x
```

## Sharing Safely

Before publishing or sending a capture, remove unrelated traffic and consider
redacting:

- public IP sessions
- device serial number
- real LAN addresses
- any cloud login traffic

For protocol work, small copied hex payloads are usually better than a full
capture.
