#!/usr/bin/env python3
"""Fail when a public release contains local state or likely private data."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "cx-plugin"

FORBIDDEN_EXACT_NAMES = {
    ".env",
    "config.json",
    "id_ed25519",
    "id_rsa",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".key",
    ".log",
    ".pem",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}
FORBIDDEN_DIRS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "htmlcov",
    "venv",
}

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
)
TEST_FIXTURES = {
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "sk-" + "supersecretvalue",
    "xoxb-" + "1234567890-secret",
}
HOME_PATH = re.compile(r"/(?:Users|home)/[^/\s]+/")
URL = re.compile(r"https?://[^\s\"'<>]+")
EMAIL = re.compile(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def is_test_fixture(path: Path, value: str) -> bool:
    return relative(path).startswith("plugins/cx-plugin/tests/") and value in TEST_FIXTURES


def url_is_public_example(raw: str) -> bool:
    parsed = urlsplit(raw.rstrip(".,;:)]}"))
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost"}:
        return True
    if host == "example.com" or host.endswith(".example.com"):
        return True
    if host == "test" or host.endswith(".test"):
        return True
    if host == "github.com":
        path = parsed.path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return path in {
            "/GotoLu",
            "/GotoLu/cx-notifier-marketplace",
        } or path.startswith("/GotoLu/cx-notifier-marketplace/")
    if host == "raw.githubusercontent.com":
        return parsed.path == "/GotoLu/cx-notifier-marketplace/main/scripts/setup_feishu.py"
    if host == "open.feishu.cn":
        return parsed.path.endswith(("/example", "/private"))
    if host == "qyapi.weixin.qq.com":
        query = dict(parse_qsl(parsed.query))
        return not query or query.get("key") in {"example", "secret"}
    if host == "oapi.dingtalk.com":
        query = dict(parse_qsl(parsed.query))
        return parsed.path == "/robot/send" and query.get("access_token") == "example"
    return False


def scan_file(path: Path) -> list[str]:
    problems: list[str] = []
    raw = path.read_bytes()
    if b"\x00" in raw:
        return [f"binary file is not allowed: {relative(path)}"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [f"non-UTF-8 file is not allowed: {relative(path)}"]

    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if not is_test_fixture(path, match.group(0)):
                problems.append(f"likely secret in {relative(path)}")

    for match in HOME_PATH.finditer(text):
        if not (
            relative(path) == "plugins/cx-plugin/tests/test_events.py"
            and match.group(0) == "/Users/" + "alice/"
        ):
            problems.append(f"personal home path in {relative(path)}")

    for match in URL.finditer(text):
        raw_url = match.group(0)
        if not url_is_public_example(raw_url):
            problems.append(f"non-example URL in {relative(path)}")

    for match in EMAIL.finditer(text):
        if match.group(1).lower() != "example.com":
            problems.append(f"non-example email in {relative(path)}")

    return problems


def main() -> int:
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        parts = set(path.relative_to(ROOT).parts)
        if ".git" in parts:
            continue
        if path.is_symlink():
            problems.append(f"symbolic link is not allowed: {relative(path)}")
            continue
        if path.is_dir():
            if parts & FORBIDDEN_DIRS:
                problems.append(f"runtime/cache directory is not allowed: {relative(path)}")
            continue
        if path.name in FORBIDDEN_EXACT_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"private/runtime file is not allowed: {relative(path)}")
            continue
        if path.name.startswith(".env."):
            problems.append(f"environment file is not allowed: {relative(path)}")
            continue
        problems.extend(scan_file(path))

    codex_manifest_path = PLUGIN / ".codex-plugin" / "plugin.json"
    claude_manifest_path = PLUGIN / ".claude-plugin" / "plugin.json"
    codex_marketplace_path = ROOT / ".agents" / "plugins" / "marketplace.json"
    claude_marketplace_path = ROOT / ".claude-plugin" / "marketplace.json"
    try:
        codex_manifest = json.loads(codex_manifest_path.read_text(encoding="utf-8"))
        claude_manifest = json.loads(claude_manifest_path.read_text(encoding="utf-8"))
        codex_marketplace = json.loads(codex_marketplace_path.read_text(encoding="utf-8"))
        claude_marketplace = json.loads(claude_marketplace_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"invalid release metadata: {exc}")
    else:
        manifests = (codex_manifest, claude_manifest)
        if any(manifest.get("name") != "cx-plugin" for manifest in manifests):
            problems.append("both plugin manifest names must be cx-plugin")
        versions = {str(manifest.get("version", "")) for manifest in manifests}
        if len(versions) != 1:
            problems.append("Codex and Claude plugin versions must match")
        if "+codex." in str(codex_manifest.get("version", "")):
            problems.append("public manifest must not contain a local cachebuster")
        if any(
            manifest.get("author", {}).get("name") == "Local developer"
            for manifest in manifests
        ):
            problems.append("public manifest contains local publisher metadata")
        for platform, marketplace in (
            ("Codex", codex_marketplace),
            ("Claude", claude_marketplace),
        ):
            entries = marketplace.get("plugins", [])
            if len(entries) != 1 or entries[0].get("name") != "cx-plugin":
                problems.append(
                    f"{platform} marketplace must contain exactly the cx-plugin entry"
                )

    if problems:
        for problem in sorted(set(problems)):
            print(f"ERROR: {problem}")
        return 1
    print("Public release privacy check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
