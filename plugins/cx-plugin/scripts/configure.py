#!/usr/bin/env python3
"""Create, validate, inspect, and test cx-plugin channel configuration."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

from cx_notify.config import (  # noqa: E402
    ConfigError,
    default_config,
    load_config,
    parse_config_json,
    resolve_config_path,
    write_config,
)
from cx_notify.limits import MAX_DELIVERY_BUDGET_SECONDS  # noqa: E402
from cx_notify.runtime import deliver_event, make_test_event  # noqa: E402
from cx_notify.providers import ProviderError, validate_webhook_url  # noqa: E402


def _load_raw(path: Path) -> dict[str, Any]:
    try:
        value = parse_config_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_config()
    except (OSError, ValueError) as exc:
        raise ConfigError("configuration file is not readable JSON") from exc
    if not isinstance(value, dict):
        raise ConfigError("configuration root must be an object")
    return value


def _validate_candidate(data: dict[str, Any], target: Path) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".cx-plugin-validate-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        write_config(data, temporary)
        load_config(temporary)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _save_validated(data: dict[str, Any], path: Path) -> None:
    _validate_candidate(data, path)
    write_config(data, path)


def _redacted_config(path: Path) -> dict[str, Any]:
    data = _load_raw(path)
    for channel in data.get("channels", []):
        if not isinstance(channel, dict):
            continue
        for key in ("webhook_url", "secret", "bearer_token"):
            if channel.get(key):
                channel[key] = "<stored-secret>"
    return data


def command_init(args: argparse.Namespace) -> int:
    path: Path = args.config
    if path.exists() and not args.force:
        print(f"Configuration already exists: {path}", file=sys.stderr)
        return 2
    _save_validated(default_config(), path)
    print(f"Created configuration: {path}")
    return 0


def command_add(args: argparse.Namespace) -> int:
    path: Path = args.config
    data = _load_raw(path)
    channel: dict[str, Any] = {
        "name": args.name,
        "type": args.type,
        "enabled": True,
    }
    if args.webhook_env:
        channel["webhook_env"] = args.webhook_env
    else:
        channel["webhook_url"] = getpass.getpass("Webhook URL: ")
    if args.type == "feishu":
        if args.mention_all:
            channel["mention_all"] = True
        if args.secret_env:
            channel["secret_env"] = args.secret_env
        elif args.secret_prompt:
            channel["secret"] = getpass.getpass("Feishu signing secret: ")
    elif args.secret_env or args.secret_prompt or args.mention_all:
        if args.mention_all:
            print("--mention-all is only valid for feishu", file=sys.stderr)
            return 2
        print("--secret-prompt/--secret-env are only valid for feishu", file=sys.stderr)
        return 2
    if args.type == "webhook":
        if args.bearer_token_env:
            channel["bearer_token_env"] = args.bearer_token_env
        elif args.bearer_token_prompt:
            channel["bearer_token"] = getpass.getpass("Bearer token: ")
    elif args.bearer_token_env or args.bearer_token_prompt:
        print("bearer token options are only valid for webhook", file=sys.stderr)
        return 2

    direct_url = channel.get("webhook_url")
    if isinstance(direct_url, str):
        raw_delivery = data.get("delivery", {})
        allow_localhost = (
            isinstance(raw_delivery, dict)
            and raw_delivery.get("allow_insecure_localhost") is True
        )
        try:
            validate_webhook_url(
                direct_url,
                args.type,
                allow_insecure_localhost=allow_localhost,
            )
        except ProviderError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 2
    channels = data.setdefault("channels", [])
    if not isinstance(channels, list):
        raise ConfigError("channels must be an array")
    channels.append(channel)
    _save_validated(data, path)
    print(f"Added channel {args.name!r} to {path}")
    return 0


def command_set_mention_all(args: argparse.Namespace) -> int:
    path: Path = args.config
    data = _load_raw(path)
    channels = data.get("channels", [])
    if not isinstance(channels, list):
        raise ConfigError("channels must be an array")
    target = next(
        (
            item
            for item in channels
            if isinstance(item, dict) and item.get("name") == args.name
        ),
        None,
    )
    if target is None:
        print(f"Channel not found: {args.name}", file=sys.stderr)
        return 2
    if target.get("type") != "feishu":
        print("mention_all is only valid for feishu channels", file=sys.stderr)
        return 2
    enabled = args.state == "on"
    target["mention_all"] = enabled
    _save_validated(data, path)
    print(f"Set mention_all={str(enabled).lower()} for channel {args.name!r}")
    return 0


def command_remove(args: argparse.Namespace) -> int:
    path: Path = args.config
    data = _load_raw(path)
    channels = data.get("channels", [])
    if not isinstance(channels, list):
        raise ConfigError("channels must be an array")
    remaining = [item for item in channels if not isinstance(item, dict) or item.get("name") != args.name]
    if len(remaining) == len(channels):
        print(f"Channel not found: {args.name}", file=sys.stderr)
        return 2
    data["channels"] = remaining
    _save_validated(data, path)
    print(f"Removed channel {args.name!r} from {path}")
    return 0


def command_list(args: argparse.Namespace) -> int:
    load_config(args.config)
    print(json.dumps(_redacted_config(args.config), ensure_ascii=False, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    channels, diagnostics = config.resolve_channels(os.environ)
    if diagnostics:
        for diagnostic in diagnostics:
            print(f"Configuration error: {diagnostic}", file=sys.stderr)
        return 2
    for channel in channels:
        try:
            validate_webhook_url(
                channel.webhook_url,
                channel.type,
                allow_insecure_localhost=config.delivery.allow_insecure_localhost,
            )
        except ProviderError as exc:
            print(
                f"Configuration error: channel {channel.name!r}: {exc}",
                file=sys.stderr,
            )
            return 2
    budget = (
        config.delivery.timeout_seconds * config.delivery.max_attempts
        + config.delivery.backoff_seconds * (config.delivery.max_attempts - 1)
    )
    if budget > MAX_DELIVERY_BUDGET_SECONDS:
        print(
            f"Configuration error: delivery budget exceeds {MAX_DELIVERY_BUDGET_SECONDS:.1f} seconds",
            file=sys.stderr,
        )
        return 2
    print(f"Configuration is valid: {config.path} ({len(config.channels)} channel(s))")
    return 0


def command_test(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    event = make_test_event()
    summary = deliver_event(event, config, channel_filter=args.channel)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["sent"] > 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=lambda value: Path(value).expanduser(),
        default=resolve_config_path(),
        help="Configuration path (default: ~/.config/cx-plugin/config.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create an empty secure configuration")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing configuration")
    init_parser.set_defaults(handler=command_init)

    add_parser = subparsers.add_parser("add", help="Add one notification channel")
    add_parser.add_argument("--type", choices=("feishu", "wecom", "webhook"), required=True)
    add_parser.add_argument("--name", required=True)
    webhook = add_parser.add_mutually_exclusive_group(required=True)
    webhook.add_argument("--webhook-env", help="Environment variable containing the webhook URL")
    webhook.add_argument(
        "--webhook-prompt",
        action="store_true",
        help="Read the webhook URL without echo and store it in the owner-only config file",
    )
    secret = add_parser.add_mutually_exclusive_group()
    secret.add_argument("--secret-env", help="Environment variable containing a Feishu signing secret")
    secret.add_argument(
        "--secret-prompt",
        action="store_true",
        help="Read a Feishu signing secret without echo",
    )
    add_parser.add_argument(
        "--mention-all",
        action="store_true",
        help="Append a Feishu @all mention to every notification on this channel",
    )
    bearer = add_parser.add_mutually_exclusive_group()
    bearer.add_argument("--bearer-token-env", help="Environment variable containing a generic bearer token")
    bearer.add_argument(
        "--bearer-token-prompt",
        action="store_true",
        help="Read a generic bearer token without echo",
    )
    add_parser.set_defaults(handler=command_add)

    remove_parser = subparsers.add_parser("remove", help="Remove a channel by name")
    remove_parser.add_argument("name")
    remove_parser.set_defaults(handler=command_remove)

    mention_parser = subparsers.add_parser(
        "set-mention-all",
        help="Enable or disable Feishu @all for an existing channel",
    )
    mention_parser.add_argument("name")
    mention_parser.add_argument("state", choices=("on", "off"))
    mention_parser.set_defaults(handler=command_set_mention_all)

    list_parser = subparsers.add_parser("list", help="Print configuration with secrets redacted")
    list_parser.set_defaults(handler=command_list)

    validate_parser = subparsers.add_parser("validate", help="Validate configuration and permissions")
    validate_parser.set_defaults(handler=command_validate)

    test_parser = subparsers.add_parser("test", help="Send a synthetic test notification")
    test_parser.add_argument("--channel", help="Only test the named channel")
    test_parser.set_defaults(handler=command_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"I/O error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
