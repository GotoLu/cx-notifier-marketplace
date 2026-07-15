"""Provider adapters and bounded notification delivery."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import platform
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from . import __version__
from .config import ResolvedChannel
from .events import NotificationEvent


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


def _validate_https_url(
    url: str | None,
    *,
    allowed_hosts: set[str] | None = None,
    host_error: str = "provider_host_not_allowed",
    allow_insecure_localhost: bool = False,
) -> str:
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
    if allowed_hosts and hostname not in allowed_hosts and not is_local_http:
        raise ProviderError(host_error)
    return urllib.parse.urlunsplit(parsed)


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


def _is_integer_zero(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


class Provider:
    """Provider adapter contract. New providers register one instance by channel type."""

    channel_type = ""
    reads_response = True

    def validate_url(self, channel: ResolvedChannel, *, allow_insecure_localhost: bool) -> str:
        return _validate_https_url(
            channel.webhook_url,
            allow_insecure_localhost=allow_insecure_localhost,
        )

    def payload(self, channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
        raise NotImplementedError

    def headers(
        self, channel: ResolvedChannel, event: NotificationEvent, body: bytes
    ) -> dict[str, str]:
        del channel, body
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": f"cx-plugin/{__version__}",
            "Idempotency-Key": event.notification_id,
        }

    def prepare(
        self,
        channel: ResolvedChannel,
        event: NotificationEvent,
        *,
        allow_insecure_localhost: bool,
    ) -> PreparedRequest:
        url = self.validate_url(
            channel,
            allow_insecure_localhost=allow_insecure_localhost,
        )
        body = json.dumps(
            self.payload(channel, event),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > 20_000:
            raise ProviderError("payload_too_large")
        return PreparedRequest(url=url, headers=self.headers(channel, event, body), body=body)

    def accepted(self, status: int, body: bytes) -> DeliveryResult:
        if not 200 <= status < 300:
            return DeliveryResult(
                False,
                status in {408, 429} or status >= 500,
                status,
                "http_error",
            )
        return DeliveryResult(True, False, status, "accepted")

    def send(
        self,
        channel: ResolvedChannel,
        event: NotificationEvent,
        *,
        timeout_seconds: float,
        allow_insecure_localhost: bool,
    ) -> DeliveryResult:
        try:
            prepared = self.prepare(
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
                    _read_response_with_deadline(response, deadline=deadline)
                    if self.reads_response
                    else b""
                )
            return self.accepted(status, body)
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


class FeishuProvider(Provider):
    channel_type = "feishu"

    def validate_url(self, channel: ResolvedChannel, *, allow_insecure_localhost: bool) -> str:
        return _validate_https_url(
            channel.webhook_url,
            allowed_hosts={"open.feishu.cn", "open.larksuite.com"},
            host_error="feishu_host_not_allowed",
            allow_insecure_localhost=allow_insecure_localhost,
        )

    def payload(self, channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
        text = event.render_text().replace("<", "＜").replace(">", "＞")
        if channel.mention_all:
            text = f"{text}\n{_FEISHU_MENTION_ALL}"
        payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
        if channel.secret:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = feishu_signature(channel.secret, timestamp)
        return payload

    def accepted(self, status: int, body: bytes) -> DeliveryResult:
        return _business_code_result(status, body, ("code", "StatusCode"))


class WeComProvider(Provider):
    channel_type = "wecom"

    def validate_url(self, channel: ResolvedChannel, *, allow_insecure_localhost: bool) -> str:
        return _validate_https_url(
            channel.webhook_url,
            allowed_hosts={"qyapi.weixin.qq.com"},
            host_error="wecom_host_not_allowed",
            allow_insecure_localhost=allow_insecure_localhost,
        )

    def payload(self, channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
        del channel
        return {"msgtype": "text", "text": {"content": event.render_text()}}

    def accepted(self, status: int, body: bytes) -> DeliveryResult:
        return _business_code_result(status, body, ("errcode",))


class WebhookProvider(Provider):
    channel_type = "webhook"
    reads_response = False

    def payload(self, channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
        del channel
        return event.payload()

    def headers(
        self, channel: ResolvedChannel, event: NotificationEvent, body: bytes
    ) -> dict[str, str]:
        headers = super().headers(channel, event, body)
        schema = event.payload()["schema"]
        headers["X-CX-Notification-Schema"] = schema
        if event.client == "codex":
            headers["X-Codex-Notification-Schema"] = schema
        if channel.bearer_token:
            headers["Authorization"] = f"Bearer {channel.bearer_token}"
        return headers


class HmacProvider(WebhookProvider):
    channel_type = "hmac"

    def headers(
        self, channel: ResolvedChannel, event: NotificationEvent, body: bytes
    ) -> dict[str, str]:
        headers = super().headers(channel, event, body)
        if not channel.secret:
            raise ProviderError("hmac_secret_missing")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            channel.secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        headers[channel.timestamp_header] = timestamp
        headers[channel.signature_header] = f"sha256={signature}"
        return headers


class DingTalkProvider(Provider):
    channel_type = "dingtalk"

    def validate_url(self, channel: ResolvedChannel, *, allow_insecure_localhost: bool) -> str:
        url = _validate_https_url(
            channel.webhook_url,
            allowed_hosts={"oapi.dingtalk.com"},
            host_error="dingtalk_host_not_allowed",
            allow_insecure_localhost=allow_insecure_localhost,
        )
        if not channel.secret:
            return url
        timestamp = int(time.time() * 1000)
        string_to_sign = f"{timestamp}\n{channel.secret}".encode("utf-8")
        signature = base64.b64encode(
            hmac.new(channel.secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
        ).decode("ascii")
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.extend((("timestamp", str(timestamp)), ("sign", signature)))
        return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))

    def payload(self, channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
        del channel
        return {"msgtype": "text", "text": {"content": event.render_text()}}

    def accepted(self, status: int, body: bytes) -> DeliveryResult:
        return _business_code_result(status, body, ("errcode",))


class DesktopProvider(Provider):
    channel_type = "desktop"
    reads_response = False

    def payload(self, channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
        del channel
        return {"title": event.title, "text": event.render_text()}

    def prepare(
        self,
        channel: ResolvedChannel,
        event: NotificationEvent,
        *,
        allow_insecure_localhost: bool,
    ) -> PreparedRequest:
        del channel, event, allow_insecure_localhost
        raise ProviderError("desktop_has_no_http_request")

    def send(
        self,
        channel: ResolvedChannel,
        event: NotificationEvent,
        *,
        timeout_seconds: float,
        allow_insecure_localhost: bool,
    ) -> DeliveryResult:
        del channel, allow_insecure_localhost
        system = platform.system()
        title = event.title[:120]
        text = event.render_text()[:1000]
        if system == "Darwin" and shutil.which("osascript"):
            script = "on run argv\n display notification item 2 of argv with title item 1 of argv\nend run"
            command = ["osascript", "-e", script, title, text]
        elif system == "Linux" and shutil.which("notify-send"):
            command = ["notify-send", title, text]
        else:
            return DeliveryResult(False, False, None, "desktop_not_supported")
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return DeliveryResult(False, True, None, "timeout")
        except OSError:
            return DeliveryResult(False, False, None, "desktop_unavailable")
        return DeliveryResult(
            completed.returncode == 0,
            False,
            completed.returncode,
            "accepted" if completed.returncode == 0 else "desktop_rejected",
        )


def _business_code_result(
    status: int, body: bytes, success_keys: tuple[str, ...]
) -> DeliveryResult:
    if not 200 <= status < 300:
        return DeliveryResult(False, status in {408, 429} or status >= 500, status, "http_error")
    response = _parse_json_response(body)
    if response is None:
        return DeliveryResult(False, False, status, "provider_response_invalid")
    accepted = any(_is_integer_zero(response.get(key)) for key in success_keys)
    return DeliveryResult(
        accepted,
        False,
        status,
        "accepted" if accepted else "provider_rejected",
    )


_PROVIDERS: dict[str, Provider] = {
    provider.channel_type: provider
    for provider in (
        DesktopProvider(),
        DingTalkProvider(),
        FeishuProvider(),
        HmacProvider(),
        WeComProvider(),
        WebhookProvider(),
    )
}


def get_provider(channel_type: str) -> Provider:
    try:
        return _PROVIDERS[channel_type]
    except KeyError as exc:
        raise ProviderError("unsupported_channel_type") from exc


def validate_channel(
    channel: ResolvedChannel, *, allow_insecure_localhost: bool = False
) -> None:
    provider = get_provider(channel.type)
    if channel.type != "desktop":
        provider.validate_url(
            channel,
            allow_insecure_localhost=allow_insecure_localhost,
        )


def preview_channel(channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
    """Return a credential-free preview without executing a provider."""

    provider = get_provider(channel.type)
    preview: dict[str, Any] = {
        "channel": channel.name,
        "type": channel.type,
        "payload": provider.payload(channel, event),
    }
    if channel.type == "hmac":
        preview["signed_headers"] = [channel.timestamp_header, channel.signature_header]
    return preview


def validate_webhook_url(
    url: str, channel_type: str, *, allow_insecure_localhost: bool = False
) -> str:
    channel = ResolvedChannel(name="validation", type=channel_type, webhook_url=url)
    return get_provider(channel_type).validate_url(
        channel,
        allow_insecure_localhost=allow_insecure_localhost,
    )


def feishu_signature(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _provider_payload(channel: ResolvedChannel, event: NotificationEvent) -> dict[str, Any]:
    return get_provider(channel.type).payload(channel, event)


def prepare_request(
    channel: ResolvedChannel,
    event: NotificationEvent,
    *,
    allow_insecure_localhost: bool = False,
) -> PreparedRequest:
    return get_provider(channel.type).prepare(
        channel,
        event,
        allow_insecure_localhost=allow_insecure_localhost,
    )


def _provider_accepted(channel_type: str, status: int, body: bytes) -> DeliveryResult:
    return get_provider(channel_type).accepted(status, body)


def send_once(
    channel: ResolvedChannel,
    event: NotificationEvent,
    *,
    timeout_seconds: float,
    allow_insecure_localhost: bool = False,
) -> DeliveryResult:
    """Send once through the registered provider adapter."""

    try:
        provider = get_provider(channel.type)
    except ProviderError as exc:
        return DeliveryResult(False, False, None, str(exc))
    return provider.send(
        channel,
        event,
        timeout_seconds=timeout_seconds,
        allow_insecure_localhost=allow_insecure_localhost,
    )
