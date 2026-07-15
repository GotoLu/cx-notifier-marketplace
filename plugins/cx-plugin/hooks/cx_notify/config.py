"""Configuration loading and validation for notification channels."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Mapping

from .limits import MAX_DELIVERY_BUDGET_SECONDS


CONFIG_VERSION = 1
DEFAULT_CONFIG_PATH = Path("~/.config/cx-plugin/config.json").expanduser()
_CHANNEL_TYPES = {"desktop", "dingtalk", "feishu", "hmac", "wecom", "webhook"}
_TOP_LEVEL_KEYS = {"version", "channels", "rules", "privacy", "delivery"}
_COMMON_CHANNEL_KEYS = {
    "name",
    "type",
    "enabled",
    "webhook_url",
    "webhook_env",
}
_TYPE_CHANNEL_KEYS = {
    "feishu": {"secret", "secret_env", "mention_all"},
    "wecom": set(),
    "webhook": {"bearer_token", "bearer_token_env"},
    "dingtalk": {"secret", "secret_env"},
    "hmac": {"secret", "secret_env", "signature_header", "timestamp_header"},
    "desktop": set(),
}
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ConfigError(ValueError):
    """Raised for invalid or unsafe configuration."""


def parse_config_json(text: str) -> Any:
    """Decode configuration JSON while rejecting ambiguous duplicate keys."""

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate configuration key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=reject_duplicate_keys)


@dataclass(frozen=True)
class PrivacyConfig:
    project_name: str = "basename"
    include_permission_description: bool = False


@dataclass(frozen=True)
class DeliveryConfig:
    timeout_seconds: float = 1.5
    max_attempts: int = 2
    backoff_seconds: float = 0.2
    dedupe_ttl_seconds: int = 604800
    allow_insecure_localhost: bool = False


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    type: str
    enabled: bool
    webhook_url: str | None
    webhook_env: str | None
    secret: str | None = None
    secret_env: str | None = None
    mention_all: bool = False
    bearer_token: str | None = None
    bearer_token_env: str | None = None
    signature_header: str = "X-CX-Signature"
    timestamp_header: str = "X-CX-Timestamp"

    @property
    def contains_inline_secret(self) -> bool:
        return any((self.webhook_url, self.secret, self.bearer_token))


@dataclass(frozen=True)
class ResolvedChannel:
    name: str
    type: str
    webhook_url: str | None
    secret: str | None = None
    mention_all: bool = False
    bearer_token: str | None = None
    signature_header: str = "X-CX-Signature"
    timestamp_header: str = "X-CX-Timestamp"


@dataclass(frozen=True)
class RoutingRule:
    name: str
    events: tuple[str, ...] = ("*",)
    projects: tuple[str, ...] = ("*",)
    clients: tuple[str, ...] = ("*",)
    channels: tuple[str, ...] = ()
    enabled: bool = True

    def matches(self, *, event: str, project: str, project_id: str, client: str) -> bool:
        return (
            any(fnmatchcase(event, pattern) for pattern in self.events)
            and any(
                fnmatchcase(project, pattern) or fnmatchcase(project_id, pattern)
                for pattern in self.projects
            )
            and any(fnmatchcase(client, pattern) for pattern in self.clients)
        )


@dataclass(frozen=True)
class PluginConfig:
    path: Path
    channels: tuple[ChannelConfig, ...]
    privacy: PrivacyConfig
    delivery: DeliveryConfig
    rules: tuple[RoutingRule, ...] = ()

    def resolve_channels(
        self, environ: Mapping[str, str] | None = None
    ) -> tuple[tuple[ResolvedChannel, ...], tuple[str, ...]]:
        environment = os.environ if environ is None else environ
        resolved: list[ResolvedChannel] = []
        diagnostics: list[str] = []
        for channel in self.channels:
            if not channel.enabled:
                continue
            webhook_url = channel.webhook_url
            if channel.webhook_env:
                webhook_url = environment.get(channel.webhook_env)
            if channel.type != "desktop" and not webhook_url:
                diagnostics.append(f"channel_unconfigured:{channel.name}")
                continue
            secret = channel.secret
            if channel.secret_env:
                secret = environment.get(channel.secret_env)
                if not secret:
                    diagnostics.append(f"channel_unconfigured:{channel.name}")
                    continue
            bearer_token = channel.bearer_token
            if channel.bearer_token_env:
                bearer_token = environment.get(channel.bearer_token_env)
                if not bearer_token:
                    diagnostics.append(f"channel_unconfigured:{channel.name}")
                    continue
            resolved.append(
                ResolvedChannel(
                    name=channel.name,
                    type=channel.type,
                    webhook_url=webhook_url,
                    secret=secret,
                    mention_all=channel.mention_all,
                    bearer_token=bearer_token,
                    signature_header=channel.signature_header,
                    timestamp_header=channel.timestamp_header,
                )
            )
        return tuple(resolved), tuple(diagnostics)

    def route_channels(
        self,
        channels: tuple[ResolvedChannel, ...],
        *,
        event: str,
        project: str,
        project_id: str,
        client: str,
    ) -> tuple[ResolvedChannel, ...]:
        """Return the union of channels selected by every matching enabled rule."""

        if not self.rules:
            return channels
        selected: set[str] = set()
        for rule in self.rules:
            if rule.enabled and rule.matches(
                event=event,
                project=project,
                project_id=project_id,
                client=client,
            ):
                selected.update(rule.channels)
        return tuple(channel for channel in channels if channel.name in selected)


def default_config() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "channels": [],
        "rules": [],
        "privacy": {
            "project_name": "basename",
            "include_permission_description": False,
        },
        "delivery": {
            "timeout_seconds": 1.5,
            "max_attempts": 2,
            "backoff_seconds": 0.2,
            "dedupe_ttl_seconds": 604800,
            "allow_insecure_localhost": False,
        },
    }


def resolve_config_path(environ: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environ is None else environ
    override = environment.get("CX_NOTIFY_CONFIG")
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def resolve_data_dir(
    environ: Mapping[str, str] | None = None, *, config_path: Path | None = None
) -> Path:
    environment = os.environ if environ is None else environ
    override = (
        environment.get("CX_NOTIFY_DATA")
        or environment.get("CLAUDE_PLUGIN_DATA")
        or environment.get("PLUGIN_DATA")
    )
    if override:
        return Path(override).expanduser()
    path = config_path or resolve_config_path(environment)
    return path.parent / "data"


def _expect_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field} must be an object")
    return value


def _exclusive_pair(item: Mapping[str, Any], direct: str, env_name: str, label: str) -> None:
    if item.get(direct) and item.get(env_name):
        raise ConfigError(f"{label} must use either {direct} or {env_name}, not both")


def _parse_channel(raw: Any, seen_names: set[str]) -> ChannelConfig:
    item = _expect_mapping(raw, "channel")
    channel_type = item.get("type")
    if channel_type not in _CHANNEL_TYPES:
        raise ConfigError(f"unsupported channel type: {channel_type!r}")
    allowed = _COMMON_CHANNEL_KEYS | _TYPE_CHANNEL_KEYS[channel_type]
    unknown = set(item) - allowed
    if unknown:
        raise ConfigError(f"unknown keys for {channel_type}: {sorted(unknown)}")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise ConfigError("channel name must be a non-empty string up to 64 characters")
    name = name.strip()
    if name in seen_names:
        raise ConfigError(f"duplicate channel name: {name}")
    seen_names.add(name)
    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"enabled must be boolean for channel {name}")
    _exclusive_pair(item, "webhook_url", "webhook_env", name)
    if channel_type != "desktop" and not item.get("webhook_url") and not item.get("webhook_env"):
        raise ConfigError(f"channel {name} requires webhook_url or webhook_env")
    if channel_type == "desktop" and (item.get("webhook_url") or item.get("webhook_env")):
        raise ConfigError(f"desktop channel {name} must not configure a webhook")
    if channel_type in {"feishu", "dingtalk", "hmac"}:
        _exclusive_pair(item, "secret", "secret_env", name)
    if channel_type == "hmac" and not item.get("secret") and not item.get("secret_env"):
        raise ConfigError(f"hmac channel {name} requires secret or secret_env")
    if channel_type == "feishu":
        mention_all = item.get("mention_all", False)
        if not isinstance(mention_all, bool):
            raise ConfigError(f"mention_all must be boolean for channel {name}")
    else:
        mention_all = False
    if channel_type == "webhook":
        _exclusive_pair(item, "bearer_token", "bearer_token_env", name)
    string_fields = (
        "webhook_url",
        "webhook_env",
        "secret",
        "secret_env",
        "bearer_token",
        "bearer_token_env",
        "signature_header",
        "timestamp_header",
    )
    for field in string_fields:
        if field in item and (not isinstance(item[field], str) or not item[field].strip()):
            raise ConfigError(f"{field} must be a non-empty string for channel {name}")
    for field in ("signature_header", "timestamp_header"):
        value = item.get(field)
        if value is not None and (
            not isinstance(value, str) or not _HTTP_HEADER_NAME.fullmatch(value)
        ):
            raise ConfigError(f"{field} must be a valid HTTP header name for channel {name}")
    return ChannelConfig(
        name=name,
        type=channel_type,
        enabled=enabled,
        webhook_url=item.get("webhook_url"),
        webhook_env=item.get("webhook_env"),
        secret=item.get("secret"),
        secret_env=item.get("secret_env"),
        mention_all=mention_all,
        bearer_token=item.get("bearer_token"),
        bearer_token_env=item.get("bearer_token_env"),
        signature_header=item.get("signature_header", "X-CX-Signature"),
        timestamp_header=item.get("timestamp_header", "X-CX-Timestamp"),
    )


def _string_patterns(raw: Any, label: str) -> tuple[str, ...]:
    if raw is None:
        return ("*",)
    if not isinstance(raw, list) or not raw or len(raw) > 32:
        raise ConfigError(f"{label} must be a non-empty array with at most 32 items")
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ConfigError(f"{label} entries must be non-empty strings up to 128 characters")
        values.append(value.strip())
    return tuple(values)


def _parse_rule(raw: Any, seen_names: set[str], channel_names: set[str]) -> RoutingRule:
    item = _expect_mapping(raw, "rule")
    unknown = set(item) - {"name", "enabled", "events", "projects", "clients", "channels"}
    if unknown:
        raise ConfigError(f"unknown rule keys: {sorted(unknown)}")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise ConfigError("rule name must be a non-empty string up to 64 characters")
    name = name.strip()
    if name in seen_names:
        raise ConfigError(f"duplicate rule name: {name}")
    seen_names.add(name)
    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"enabled must be boolean for rule {name}")
    raw_channels = item.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels or len(raw_channels) > 16:
        raise ConfigError(f"channels for rule {name} must be a non-empty array")
    channels: list[str] = []
    for channel in raw_channels:
        if not isinstance(channel, str) or channel not in channel_names:
            raise ConfigError(f"rule {name} references unknown channel: {channel!r}")
        if channel not in channels:
            channels.append(channel)
    return RoutingRule(
        name=name,
        enabled=enabled,
        events=_string_patterns(item.get("events"), f"events for rule {name}"),
        projects=_string_patterns(item.get("projects"), f"projects for rule {name}"),
        clients=_string_patterns(item.get("clients"), f"clients for rule {name}"),
        channels=tuple(channels),
    )


def _parse_privacy(raw: Any) -> PrivacyConfig:
    item = _expect_mapping(raw, "privacy")
    unknown = set(item) - {"project_name", "include_permission_description"}
    if unknown:
        raise ConfigError(f"unknown privacy keys: {sorted(unknown)}")
    project_name = item.get("project_name", "basename")
    if project_name not in {"basename", "hash", "hidden"}:
        raise ConfigError("privacy.project_name must be basename, hash, or hidden")
    include_description = item.get("include_permission_description", False)
    if not isinstance(include_description, bool):
        raise ConfigError("privacy.include_permission_description must be boolean")
    return PrivacyConfig(project_name, include_description)


def _parse_delivery(raw: Any) -> DeliveryConfig:
    item = _expect_mapping(raw, "delivery")
    allowed = {
        "timeout_seconds",
        "max_attempts",
        "backoff_seconds",
        "dedupe_ttl_seconds",
        "allow_insecure_localhost",
    }
    unknown = set(item) - allowed
    if unknown:
        raise ConfigError(f"unknown delivery keys: {sorted(unknown)}")
    raw_timeout = item.get("timeout_seconds", 1.5)
    raw_attempts = item.get("max_attempts", 2)
    raw_backoff = item.get("backoff_seconds", 0.2)
    raw_ttl = item.get("dedupe_ttl_seconds", 604800)
    if (
        isinstance(raw_timeout, bool)
        or not isinstance(raw_timeout, (int, float))
        or isinstance(raw_attempts, bool)
        or not isinstance(raw_attempts, int)
        or isinstance(raw_backoff, bool)
        or not isinstance(raw_backoff, (int, float))
        or isinstance(raw_ttl, bool)
        or not isinstance(raw_ttl, int)
    ):
        raise ConfigError("delivery values have invalid types")
    timeout = float(raw_timeout)
    attempts = raw_attempts
    backoff = float(raw_backoff)
    ttl = raw_ttl
    if not 0.1 <= timeout <= 2.0:
        raise ConfigError("delivery.timeout_seconds must be between 0.1 and 2.0")
    if attempts not in {1, 2}:
        raise ConfigError("delivery.max_attempts must be 1 or 2")
    if not 0 <= backoff <= 0.5:
        raise ConfigError("delivery.backoff_seconds must be between 0 and 0.5")
    if not 60 <= ttl <= 2592000:
        raise ConfigError("delivery.dedupe_ttl_seconds must be between 60 and 2592000")
    budget = timeout * attempts + backoff * (attempts - 1)
    if budget > MAX_DELIVERY_BUDGET_SECONDS:
        raise ConfigError(
            f"delivery network budget must not exceed {MAX_DELIVERY_BUDGET_SECONDS:.1f} seconds"
        )
    allow_localhost = item.get("allow_insecure_localhost", False)
    if not isinstance(allow_localhost, bool):
        raise ConfigError("delivery.allow_insecure_localhost must be boolean")
    return DeliveryConfig(timeout, attempts, backoff, ttl, allow_localhost)


def _permissions_are_private(path: Path) -> bool:
    if os.name == "nt":
        return True
    return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def load_config(
    path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> PluginConfig:
    config_path = path or resolve_config_path(environ)
    try:
        data = parse_config_json(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError("configuration file not found") from exc
    except (OSError, ValueError) as exc:
        raise ConfigError("configuration file is not readable JSON") from exc
    root = _expect_mapping(data, "configuration")
    unknown = set(root) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level keys: {sorted(unknown)}")
    if root.get("version") != CONFIG_VERSION:
        raise ConfigError(f"configuration version must be {CONFIG_VERSION}")
    raw_channels = root.get("channels", [])
    if not isinstance(raw_channels, list) or len(raw_channels) > 16:
        raise ConfigError("channels must be an array with at most 16 items")
    seen_names: set[str] = set()
    channels = tuple(_parse_channel(item, seen_names) for item in raw_channels)
    raw_rules = root.get("rules", [])
    if not isinstance(raw_rules, list) or len(raw_rules) > 64:
        raise ConfigError("rules must be an array with at most 64 items")
    seen_rules: set[str] = set()
    channel_names = {channel.name for channel in channels}
    rules = tuple(_parse_rule(item, seen_rules, channel_names) for item in raw_rules)
    if any(channel.contains_inline_secret for channel in channels) and not _permissions_are_private(
        config_path
    ):
        raise ConfigError("configuration containing inline secrets must have mode 0600")
    return PluginConfig(
        path=config_path,
        channels=channels,
        privacy=_parse_privacy(root.get("privacy", {})),
        delivery=_parse_delivery(root.get("delivery", {})),
        rules=rules,
    )


def write_config(data: Mapping[str, Any], path: Path) -> None:
    """Atomically write configuration with owner-only permissions."""

    path = path.expanduser()
    parent_existed = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and not parent_existed:
        os.chmod(path.parent, 0o700)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
