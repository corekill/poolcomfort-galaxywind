# Contributing

Thanks for helping make this useful beyond one pool heat pump.

## Good Contributions

- PCAP captures from additional Pool Comfort compatible heat pumps
- parsed attributes with sample payloads and expected values
- challenge-response test vectors from login captures
- small protocol tests that prove a new finding
- Home Assistant integration scaffolding after local login works

## Privacy

Do not commit:

- router credentials
- app account credentials
- full home-network captures with unrelated devices
- serial numbers unless you intentionally want them public
- APKs, decompiled APK folders, or native library dumps

Prefer documenting findings as small hex payload examples in tests or docs.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[pcap,test]'
pytest
```

## Capture Review Checklist

Before sharing a capture:

- the capture is filtered to the phone and heat pump only
- the Pool Comfort app was opened fresh so the login sequence is present
- at least one harmless state change was made, such as mode or target temp
- any cloud traffic is removed unless it is directly relevant
- IP addresses and serial numbers are anonymized when possible

## Reverse Engineering Notes

When adding a protocol finding, include:

- device model or app version when known
- packet direction
- raw payload hex
- decoded meaning
- a test or script output that can reproduce the decode
