"""Fail-open orchestration for Codex notification hooks."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ConfigError, PluginConfig, ResolvedChannel, load_config, resolve_config_path, resolve_data_dir
from .events import NotificationEvent, parse_hook_event
from .providers import DeliveryResult, send_once
from .security import canonical_hash, sha256_short
from .state import DeliveryState, SafeLogger


Transport = Callable[..., DeliveryResult]


class _NullLogger:
    def write(self, code: str, **fields: Any) -> None:
        return None


class _VolatileDeliveryState:
    """Continue notifications without deduplication when local state is unavailable."""

    channel_key = staticmethod(DeliveryState.channel_key)

    def claim(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def mark_sent(self, delivery_key: str) -> None:
        return None

    def mark_failed(self, delivery_key: str) -> None:
        return None

    def close(self) -> None:
        return None


def _safe_logger(data_dir: Path) -> SafeLogger | _NullLogger:
    try:
        return SafeLogger(data_dir)
    except Exception:
        return _NullLogger()


def _deliver_channel(
    channel: ResolvedChannel,
    event: NotificationEvent,
    config: PluginConfig,
    state: DeliveryState | _VolatileDeliveryState,
    logger: SafeLogger | _NullLogger,
    transport: Transport,
) -> tuple[str, bool, bool]:
    delivery_key = state.channel_key(event.dedupe_key, channel.name, channel.type)
    try:
        claimed = state.claim(
            delivery_key,
            event.notification_id,
            dedupe_ttl_seconds=config.delivery.dedupe_ttl_seconds,
        )
    except Exception:
        logger.write(
            "state_claim_failed",
            channel=channel.name,
            event=event.event,
            notification_id=event.notification_id,
        )
        return channel.name, False, False
    if not claimed:
        logger.write(
            "delivery_deduplicated",
            channel=channel.name,
            event=event.event,
            notification_id=event.notification_id,
        )
        return channel.name, True, True

    result = DeliveryResult(False, False, None, "not_attempted")
    for attempt in range(1, config.delivery.max_attempts + 1):
        try:
            result = transport(
                channel,
                event,
                timeout_seconds=config.delivery.timeout_seconds,
                allow_insecure_localhost=config.delivery.allow_insecure_localhost,
            )
        except Exception:
            result = DeliveryResult(False, True, None, "transport_exception")
        logger.write(
            result.diagnostic,
            channel=channel.name,
            event=event.event,
            status=result.status,
            attempt=attempt,
            notification_id=event.notification_id,
        )
        if result.success or not result.retryable or attempt >= config.delivery.max_attempts:
            break
        time.sleep(config.delivery.backoff_seconds)

    try:
        if result.success:
            state.mark_sent(delivery_key)
        else:
            state.mark_failed(delivery_key)
    except Exception:
        logger.write(
            "state_update_failed",
            channel=channel.name,
            event=event.event,
            notification_id=event.notification_id,
        )
    return channel.name, result.success, False


def deliver_event(
    event: NotificationEvent,
    config: PluginConfig,
    *,
    environ: Mapping[str, str] | None = None,
    transport: Transport = send_once,
    channel_filter: str | None = None,
) -> dict[str, int]:
    environment = os.environ if environ is None else environ
    data_dir = resolve_data_dir(environment, config_path=config.path)
    logger = _safe_logger(data_dir)
    channels, diagnostics = config.resolve_channels(environment)
    for diagnostic in diagnostics:
        channel_name = diagnostic.split(":", 1)[-1]
        logger.write(
            "channel_unconfigured",
            channel=channel_name,
            event=event.event,
            notification_id=event.notification_id,
        )
    if channel_filter:
        channels = tuple(channel for channel in channels if channel.name == channel_filter)
    summary = {"sent": 0, "failed": 0, "deduplicated": 0, "skipped": len(diagnostics)}
    if not channels:
        logger.write(
            "no_channels_available",
            event=event.event,
            notification_id=event.notification_id,
        )
        return summary
    try:
        state = DeliveryState(data_dir)
        state.purge(max(config.delivery.dedupe_ttl_seconds, 604800))
    except Exception:
        logger.write(
            "state_unavailable",
            event=event.event,
            notification_id=event.notification_id,
        )
        state = _VolatileDeliveryState()
    try:
        # Configuration permits at most 16 channels. Run all enabled channels in one
        # wave so the per-channel retry budget still fits the 5-second hook budget.
        with ThreadPoolExecutor(max_workers=len(channels)) as executor:
            futures = [
                executor.submit(
                    _deliver_channel,
                    channel,
                    event,
                    config,
                    state,
                    logger,
                    transport,
                )
                for channel in channels
            ]
            for future in as_completed(futures):
                try:
                    _, success, deduplicated = future.result()
                except Exception:
                    summary["failed"] += 1
                    continue
                if deduplicated:
                    summary["deduplicated"] += 1
                elif success:
                    summary["sent"] += 1
                else:
                    summary["failed"] += 1
    finally:
        state.close()
    return summary


def run_hook(
    data: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    transport: Transport = send_once,
    client: str = "codex",
) -> dict[str, int]:
    """Handle one hook input without ever making an approval decision."""

    environment = os.environ if environ is None else environ
    path = config_path or resolve_config_path(environment)
    data_dir = resolve_data_dir(environment, config_path=path)
    logger = _safe_logger(data_dir)
    try:
        config = load_config(path, environ=environment)
    except ConfigError:
        logger.write("config_error")
        return {"sent": 0, "failed": 0, "deduplicated": 0, "skipped": 1}
    result = parse_hook_event(
        data,
        project_name_mode=config.privacy.project_name,
        include_permission_description=config.privacy.include_permission_description,
        client=client,
    )
    if result.diagnostic:
        logger.write(result.diagnostic, event=str(data.get("hook_event_name") or "unknown"))
    if result.event is None:
        return {"sent": 0, "failed": 0, "deduplicated": 0, "skipped": 1}
    return deliver_event(
        result.event,
        config,
        environ=environment,
        transport=transport,
    )


def make_test_event(cwd: str | None = None) -> NotificationEvent:
    """Create an unmistakably synthetic event without leaking project context."""

    del cwd  # Kept for compatibility with early development callers.
    nonce = str(time.time_ns())
    dedupe_key = canonical_hash({"event": "test", "nonce": nonce})
    occurred_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return NotificationEvent(
        event="test",
        notification_id=f"cxn_{dedupe_key[:20]}",
        dedupe_key=dedupe_key,
        occurred_at=occurred_at,
        project_name="test",
        project_id=f"sha256:{sha256_short('cx-plugin-test-project', 20)}",
        session_id_hash=f"sha256:{sha256_short('cx-plugin-test-session', 20)}",
        turn_id_hash=f"sha256:{sha256_short(nonce, 20)}",
        title="Codex 通知配置测试",
    )
