# Home Assistant Plan

The first Home Assistant integration should stay small and local-only.

## Entity Model

Expose one `climate` entity:

- current water temperature from attribute `0x0020`
- target temperature from attribute `0x0016`
- HVAC mode from attribute `0x0017`
- on/off state from attribute `0x0018`

Optional diagnostic sensors:

- serial number
- raw mode value
- raw temperature block
- last successful update timestamp

## Configuration

Suggested config flow fields:

- host
- serial number, optional but useful for validation
- password, stored for future auth support
- polling interval

Discovery can use UDP `8818`, but manual host entry should remain available.

## Current Blocker

The integration should not be published as working until the second UDP `1194`
challenge-response is implemented. Packet parsing and command building are
ready to be reused, but live sessions are not reliable yet.

## Suggested Package Shape

```text
custom_components/poolcomfort/
  __init__.py
  climate.py
  config_flow.py
  const.py
  manifest.json
```

The Home Assistant layer should call the Python client rather than duplicating
protocol parsing.
