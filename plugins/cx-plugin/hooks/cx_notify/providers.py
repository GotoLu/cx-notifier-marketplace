"""Provider payloads and bounded HTTPS delivery."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from . import __version__
from .config import ResolvedChannel
from .events import NotificationEvent


_FEISHU_HOSTS = {"open.feishu.cn", "open.larksuite.com"}
_WECOM_HOSTS = {"qyapi.weixin.qq.com"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_FEISHU_MENTION_ALL = '<at user_id="all">所有人</at>'


class ProviderError(ValueError):
    """Raised when a provider cannot be configured safely."""


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    retryable: bool
    status: int | None
    diagnostic: str


@dataclass(frozen=True)
class PreparedRequest:
    url: str
    headers: Mapping[str, str]
    body: bytes


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def validate_webhook_url(
    url: str, channel_type: str, *, allow_insecure_localhost: bool = False
) -> str:
    """Validate schemes and pin built-in providers to their official API hosts."""

    if (
        not isinstance(url, str)
        or not url
        or url != url.strip()
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in url
        )
    ):
        raise ProviderError("webhook_url_invalid")
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
    except ValueError as exc:
        raise ProviderError("webhook_url_invalid") from exc
    hostname = (parsed.hostname or "").lower()
    is_local_http = (
        parsed.scheme == "http"
        and allow_insecure_localhost
        and hostname in _LOCAL_HOSTS
    )
    if parsed.scheme != "https" and not is_local_http:
        raise ProviderError("webhook_url_requires_https")
    if not hostname or parsed.username or parsed.password or parsed.fragment:
        raise ProviderError("webhook_url_invalid")
    if channel_type == "feishu" and hostname not in _FEISHU_HOSTS:
        if not is_local_http:
            raise ProviderError("feishu_host_not_allowed")
    if channel_type == "wecom" and hostname not in _WECOM_HOSTS:
        if not is_local_http:
            raise ProviderError("wecom_host_not_allowed")
    return urllib.parse.urlunsplit(parsed)


def feishu_signature(secret: str, timestamp: int) -> str:
    """Create the signature expected by Feishu/Lark custom bots."""

    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _provider_payload(channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
    if channel.type == "feishu":
        # Feishu text messages interpret <at ...> markup. Neutralize any dynamic
        # angle brackets before appending the one trusted mention requested by
        # channel configuration.
        text = event.render_text().replace("<", "＜").replace(">", "＞")
        if channel.mention_all:
            text = f"{text}\n{_FEISHU_MENTION_ALL}"
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if channel.secret:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = feishu_signature(channel.secret, timestamp)
        return payload
    if channel.type == "wecom":
        return {"msgtype": "text", "text": {"content": event.render_text()}}
    if channel.type == "webhook":
        return event.payload()
    raise ProviderError("unsupported_channel_type")


def prepare_request(
    channel: ResolvedChannel,
    event: NotificationEvent,
    *,
    allow_insecure_localhost: bool = False,
) -> PreparedRequest:
    url = validate_webhook_url(
        channel.webhook_url,
        channel.type,
        allow_insecure_localhost=allow_insecure_localhost,
    )
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": f"cx-plugin/{__version__}",
        "Idempotency-Key": event.notification_id,
    }
    if channel.type == "webhook":
        schema = event.payload()["schema"]
        headers["X-CX-Notification-Schema"] = schema
        if event.client == "codex":
            headers["X-Codex-Notification-Schema"] = schema
        if channel.bearer_token:
            headers["Authorization"] = f"Bearer {channel.bearer_token}"
    body = json.dumps(
        _provider_payload(channel, event),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > 20_000:
        raise ProviderError("payload_too_large")
    return PreparedRequest(url=url, headers=headers, body=body)


def _parse_json_response(body: bytes) -> Mapping[str, Any] | None:
    if len(body) > 65536:
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _read_response_with_deadline(
    response: Any,
    *,
    deadline: float,
    max_bytes: int = 65537,
) -> bytes:
    """Read a small provider response while shrinking the socket timeout to a deadline."""

    chunks: list[bytes] = []
    total = 0
    while total < max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        socket_object = getattr(
            getattr(getattr(response, "fp", None), "raw", None),
            "_sock",
            None,
        )
        if socket_object is not None:
            socket_object.settimeout(max(0.001, remaining))
        reader = getattr(response, "read1", response.read)
        chunk = reader(min(8192, max_bytes - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _provider_accepted(channel_type: str, status: int, body: bytes) -> DeliveryResult:
    if not 200 <= status < 300:
        return DeliveryResult(False, status in {408, 429} or status >= 500, status, "http_error")
    if channel_type == "webhook":
        return DeliveryResult(True, False, status, "accepted")
    response = _parse_json_response(body)
    if response is None:
        return DeliveryResult(False, False, status, "provider_response_invalid")

    def is_integer_zero(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value == 0

    if channel_type == "wecom":
        accepted = is_integer_zero(response.get("errcode"))
    else:
        accepted = is_integer_zero(response.get("code")) or is_integer_zero(
            response.get("StatusCode")
        )
    return DeliveryResult(
        accepted,
        False,
        status,
        "accepted" if accepted else "provider_rejected",
    )


def send_once(
    channel: ResolvedChannel,
    event: NotificationEvent,
    *,
    timeout_seconds: float,
    allow_insecure_localhost: bool = False,
) -> DeliveryResult:
    """Send one bounded request without following redirects."""

    try:
        prepared = prepare_request(
            channel,
            event,
            allow_insecure_localhost=allow_insecure_localhost,
        )
    except ProviderError as exc:
        return DeliveryResult(False, False, None, str(exc))
    request = urllib.request.Request(
        prepared.url,
        data=prepared.body,
        headers=dict(prepared.headers),
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    deadline = time.monotonic() + timeout_seconds
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            body = (
                b""
                if channel.type == "webhook"
                else _read_response_with_deadline(response, deadline=deadline)
            )
        return _provider_accepted(channel.type, status, body)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        return DeliveryResult(
            False,
            status in {408, 429} or status >= 500,
            status,
            "http_error",
        )
    except (TimeoutError, socket.timeout):
        return DeliveryResult(False, True, None, "timeout")
    except urllib.error.URLError:
        return DeliveryResult(False, True, None, "network_error")
    except OSError:
        return DeliveryResult(False, True, None, "io_error")
