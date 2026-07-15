#!/usr/bin/env python3
"""Locate an installed cx-plugin and enable a zero-secret desktop channel."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_ID = "cx-plugin@cx-notifier"
CHANNEL_NAME = "desktop-main"
DEFAULT_CONFIG = Path("~/.config/cx-plugin/config.json").expanduser()


def _configure_at(root: Path) -> Path | None:
    candidate = root.expanduser() / "scripts" / "configure.py"
    return candidate if candidate.is_file() else None


def _run_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _from_claude() -> Path | None:
    executable = shutil.which("claude")
    if not executable:
        return None
    output = _run_output([executable, "plugin", "list", "--json"])
    if not output:
        return None
    try:
        entries = json.loads(output)
    except ValueError:
        return None
    if not isinstance(entries, list):
        return None
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID
    ]
    matches.sort(key=lambda entry: str(entry.get("installedAt") or ""), reverse=True)
    for entry in matches:
        install_path = entry.get("installPath")
        if isinstance(install_path, str):
            configure = _configure_at(Path(install_path))
            if configure:
                return configure
    return None


def _from_codex() -> Path | None:
    common = _configure_at(Path.home() / "plugins" / "cx-plugin")
    if common:
        return common
    executable = shutil.which("codex")
    if not executable:
        return None
    output = _run_output([executable, "plugin", "list"])
    if not output:
        return None
    for line in output.splitlines():
        if not re.match(r"^cx-plugin@(?:cx-notifier|personal)\s", line.strip()):
            continue
        columns = re.split(r"\s{2,}", line.strip())
        if columns:
            configure = _configure_at(Path(columns[-1]))
            if configure:
                return configure
    return None


def locate_configure() -> Path | None:
    override = os.environ.get("CX_NOTIFY_PLUGIN_ROOT")
    if override:
        configure = _configure_at(Path(override))
        if configure:
            return configure

    try:
        repository_root = Path(__file__).resolve().parents[1]
    except (NameError, OSError):
        repository_root = Path.cwd()
    local = _configure_at(repository_root / "plugins" / "cx-plugin")
    if local:
        return local
    return _from_claude() or _from_codex()


def _run_configure(configure: Path, config: Path, *arguments: str, capture: bool = False):
    return subprocess.run(
        [sys.executable, "-B", str(configure), "--config", str(config), *arguments],
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
        text=True,
    )


def _existing_channels(configure: Path, config: Path) -> list[dict[str, Any]] | None:
    if not config.exists():
        return []
    completed = _run_configure(configure, config, "list", capture=True)
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        return None
    try:
        data = json.loads(completed.stdout)
    except ValueError:
        print("无法读取现有配置，请先运行 configure.py validate。", file=sys.stderr)
        return None
    channels = data.get("channels", []) if isinstance(data, dict) else []
    return [channel for channel in channels if isinstance(channel, dict)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--replace",
        action="store_true",
        help=f"替换已有的 {CHANNEL_NAME} 渠道",
    )
    parser.add_argument(
        "--no-test",
        action="store_true",
        help="保存配置后不触发桌面测试通知",
    )
    parser.add_argument("--locate-only", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.config = args.config.expanduser()
    configure = locate_configure()
    if configure is None:
        print(
            "没有找到已安装的 cx-plugin。请先通过 Codex 或 Claude Code marketplace 安装插件。",
            file=sys.stderr,
        )
        return 2
    if args.locate_only:
        print(configure)
        return 0

    channels = _existing_channels(configure, args.config)
    if channels is None:
        return 2
    exists = any(channel.get("name") == CHANNEL_NAME for channel in channels)
    if exists and not args.replace:
        print(
            f"已存在 {CHANNEL_NAME}。若要重新配置，请在命令末尾增加 --replace。",
            file=sys.stderr,
        )
        return 2

    if not args.config.exists():
        if _run_configure(configure, args.config, "init").returncode != 0:
            return 2
    if exists:
        if _run_configure(configure, args.config, "remove", CHANNEL_NAME).returncode != 0:
            return 2

    if (
        _run_configure(
            configure,
            args.config,
            "add",
            "--type",
            "desktop",
            "--name",
            CHANNEL_NAME,
        ).returncode
        != 0
    ):
        return 2
    if _run_configure(configure, args.config, "validate").returncode != 0:
        return 2
    if not args.no_test:
        print("正在触发桌面测试通知……")
        if (
            _run_configure(
                configure,
                args.config,
                "test",
                "--channel",
                CHANNEL_NAME,
            ).returncode
            != 0
        ):
            print(
                "桌面渠道已保存，但测试通知失败。macOS 需要 osascript；Linux 需要 notify-send。",
                file=sys.stderr,
            )
            return 1
    print("桌面通知配置完成。请重新加载插件或启动新会话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
