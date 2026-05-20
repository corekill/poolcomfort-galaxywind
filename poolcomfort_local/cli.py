from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from enum import IntEnum

from .client import PoolComfortClient, discover_hosts


def main() -> None:
    parser = argparse.ArgumentParser(description="Pool Comfort local UDP prototype")
    parser.add_argument("--host", help="Heat pump IP address")
    parser.add_argument("--serial", help="Heat pump serial number; discovers the IP automatically when --host is omitted")
    parser.add_argument("--password", default="123456", help="Device password")
    parser.add_argument(
        "--assume-on",
        action="store_true",
        help="Skip the power-state guard before set-temp and set-mode",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("diagnostics")
    temp = sub.add_parser("set-temp")
    temp.add_argument("value", type=int)
    mode = sub.add_parser("set-mode")
    mode.add_argument("value", type=int, choices=[0, 1, 2, 3])
    power = sub.add_parser("power")
    power.add_argument("value", choices=["on", "off"])
    args = parser.parse_args()

    host = resolve_host(args.host, args.serial, args.password)
    client = PoolComfortClient(host, password=args.password)
    client.connect()
    try:
        if args.command == "status":
            state = client.query_state()
            print_json(state)
        elif args.command == "diagnostics":
            diagnostics = client.query_diagnostics()
            print_json(diagnostics)
        elif args.command == "set-temp":
            ensure_powered_for_control(client, args.assume_on)
            reply = client.set_target_temp(args.value)
            print(reply.payload.hex())
        elif args.command == "set-mode":
            ensure_powered_for_control(client, args.assume_on)
            reply = client.set_mode(args.value)
            print(reply.payload.hex())
        elif args.command == "power":
            reply = client.set_power(args.value == "on")
            print(reply.payload.hex())
    finally:
        client.close()


def print_json(value: object) -> None:
    print(json.dumps(value, default=json_default, indent=2, sort_keys=True))


def json_default(value: object) -> object:
    if isinstance(value, IntEnum):
        return int(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def resolve_host(host: str | None, serial: str | None, password: str) -> str:
    if host is not None:
        return host
    if serial is None:
        raise SystemExit("Pass --host or --serial.")

    for candidate in discover_hosts():
        client = PoolComfortClient(candidate, password=password)
        try:
            client.connect()
            state = client.query_state()
        except Exception:
            continue
        finally:
            client.close()
        if state.serial == serial:
            return candidate
    raise SystemExit(f"No Pool Comfort heat pump with serial {serial} was discovered.")


def ensure_powered_for_control(client: PoolComfortClient, assume_on: bool) -> None:
    if assume_on:
        return
    state = client.query_state()
    if state.power is False:
        raise SystemExit("Heat pump is off; power it on before changing temperature or mode.")


if __name__ == "__main__":
    main()
