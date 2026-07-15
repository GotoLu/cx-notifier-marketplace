#!/usr/bin/env python3
"""Pause, resume, or inspect cx-plugin notifications without changing channels."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

from cx_notify.config import (  # noqa: E402
    ConfigError,
    load_config,
    parse_config_json,
    resolve_config_path,
    write_config,
)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = parse_config_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError("configuration file not found") from exc
    except (OSError, ValueError) as exc:
        raise ConfigError("configuration file is not readable JSON") from exc
    if not isinstance(value, dict):
        raise ConfigError("configuration root must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=lambda value: Path(value).expanduser(),
        default=resolve_config_path(),
        help="Configuration path (default: ~/.config/cx-plugin/config.json)",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--resume", action="store_true", help="Resume notifications")
    action.add_argument("--status", action="store_true", help="Print paused or running")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.status:
            print("paused" if config.paused else "running")
            return 0
        target = not args.resume
        if config.paused == target:
            print("Notifications already paused." if target else "Notifications already running.")
            return 0
        data = _read(args.config)
        data["paused"] = target
        write_config(data, args.config)
        verified = load_config(args.config)
        if verified.paused != target:
            raise ConfigError("pause state verification failed")
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"I/O error: {exc}", file=sys.stderr)
        return 2

    print("Notifications paused." if target else "Notifications resumed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
