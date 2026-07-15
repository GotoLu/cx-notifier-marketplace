"""Privacy-preserving helpers for outbound notification data."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth(?:orization)?|password|passwd|secret|token)"
        r"\b\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
)
_POSIX_HOME_PATH = re.compile(r"(?<![\w.-])/(?:Users|home)/[^\s]+")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![:/\w.-])/(?:[^/\s]+/)*[^/\s]+")
_WINDOWS_HOME_PATH = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\s]+")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s#]+")


def sha256_short(value: str, length: int = 16) -> str:
    """Return a non-reversible, short identifier for a potentially sensitive value."""

    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:length]


def canonical_hash(value: Any) -> str:
    """Hash a JSON-compatible value without retaining its original representation."""

    import json

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def sanitize_text(value: Any, max_length: int = 200) -> str:
    """Redact common secret/path shapes and constrain text sent to third parties."""

    text = _CONTROL_CHARS.sub(" ", str(value or ""))
    text = " ".join(text.split())
    text = _POSIX_HOME_PATH.sub("<path>", text)
    text = _POSIX_ABSOLUTE_PATH.sub("<path>", text)
    text = _WINDOWS_HOME_PATH.sub("<path>", text)
    text = _URL_QUERY.sub(r"\1?<redacted>", text)
    text = _SECRET_PATTERNS[0].sub(r"\1<redacted>", text)
    text = _SECRET_PATTERNS[1].sub(r"\1=<redacted>", text)
    for pattern in _SECRET_PATTERNS[2:]:
        text = pattern.sub("<redacted>", text)
    if len(text) > max_length:
        text = text[: max(0, max_length - 1)].rstrip() + "…"
    return text


def project_identity(cwd: str, mode: str = "basename") -> tuple[str, str]:
    """Return a display-safe project name and a stable hashed project identifier."""

    normalized = str(Path(cwd or ".").expanduser().resolve(strict=False))
    project_id = f"sha256:{sha256_short(normalized, 20)}"
    if mode == "hidden":
        return "hidden", project_id
    if mode == "hash":
        return f"project-{sha256_short(normalized, 8)}", project_id
    name = Path(normalized).name or "project"
    return sanitize_text(name, 80), project_id
