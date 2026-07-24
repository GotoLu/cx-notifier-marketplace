#!/usr/bin/env python3
"""Install CX Notifier for Codex and/or Claude Code, then configure one channel."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


MARKETPLACE_SOURCE = "GotoLu/cx-notifier-marketplace"
MARKETPLACE_NAME = "cx-notifier"
PLUGIN_ID = "cx-plugin@cx-notifier"
SETUP_URLS = {
    "desktop": "https://raw.githubusercontent.com/GotoLu/cx-notifier-marketplace/main/scripts/setup_desktop.py",
    "feishu": "https://raw.githubusercontent.com/GotoLu/cx-notifier-marketplace/main/scripts/setup_feishu.py",
}


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    if not capture:
        print(f"+ {shlex.join(command)}")
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
            timeout=120,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))


def _json_contains(value: Any, *, keys: tuple[str, ...], expected: str) -> bool:
    if isinstance(value, dict):
        if any(value.get(key) == expected for key in keys):
            return True
        return any(
            _json_contains(child, keys=keys, expected=expected)
            for child in value.values()
        )
    if isinstance(value, list):
        return any(
            _json_contains(child, keys=keys, expected=expected) for child in value
        )
    return False


def _json_contains_active_plugin(value: Any) -> bool:
    if isinstance(value, dict):
        plugin_id = value.get("pluginId") or value.get("id")
        if plugin_id == PLUGIN_ID:
            return value.get("installed", True) is not False and value.get(
                "enabled", True
            ) is not False
        return any(_json_contains_active_plugin(child) for child in value.values())
    if isinstance(value, list):
        return any(_json_contains_active_plugin(child) for child in value)
    return False


def _plugin_installed(client: str, executable: str) -> bool:
    del client
    completed = _run([executable, "plugin", "list", "--json"], capture=True)
    if completed.returncode != 0:
        return False
    try:
        value = json.loads(completed.stdout)
    except ValueError:
        return PLUGIN_ID in completed.stdout
    return _json_contains_active_plugin(value)


def _marketplace_installed(executable: str) -> bool:
    completed = _run([executable, "plugin", "marketplace", "list"], capture=True)
    if completed.returncode != 0:
        return False
    try:
        value = json.loads(completed.stdout)
    except ValueError:
        return any(
            line.split(maxsplit=1)[0] == MARKETPLACE_NAME
            for line in completed.stdout.splitlines()
            if line.strip()
        )
    return _json_contains(
        value,
        keys=("name", "marketplaceName", "id"),
        expected=MARKETPLACE_NAME,
    )


def _install_for(client: str, executable: str) -> bool:
    if _plugin_installed(client, executable):
        print(f"{client}: {PLUGIN_ID} 已安装，跳过插件安装。")
        return True

    if not _marketplace_installed(executable):
        completed = _run(
            [
                executable,
                "plugin",
                "marketplace",
                "add",
                MARKETPLACE_SOURCE,
            ]
        )
        if completed.returncode != 0:
            print(f"{client}: 添加 marketplace 失败。", file=sys.stderr)
            return False

    action = "add" if client == "codex" else "install"
    completed = _run([executable, "plugin", action, PLUGIN_ID])
    if completed.returncode != 0:
        print(f"{client}: 安装 {PLUGIN_ID} 失败。", file=sys.stderr)
        return False
    return True


def _select_clients(requested: str) -> list[tuple[str, str]]:
    detected = {
        client: executable
        for client in ("codex", "claude")
        if (executable := shutil.which(client)) is not None
    }
    if requested == "auto":
        selected = [client for client in ("codex", "claude") if client in detected]
    elif requested == "both":
        selected = ["codex", "claude"]
    else:
        selected = [requested]

    missing = [client for client in selected if client not in detected]
    if missing:
        raise ValueError(f"未找到命令：{', '.join(missing)}")
    if not selected:
        raise ValueError("未找到 codex 或 claude 命令。")
    return [(client, detected[client]) for client in selected]


def _setup_arguments(args: argparse.Namespace) -> list[str]:
    arguments: list[str] = []
    if args.config:
        arguments.extend(["--config", str(args.config.expanduser())])
    if args.replace:
        arguments.append("--replace")
    if args.no_test:
        arguments.append("--no-test")
    if args.channel == "feishu":
        if args.no_signature:
            arguments.append("--no-signature")
        if args.mention_all:
            arguments.append("--mention-all")
    return arguments


def _local_setup_script(channel: str) -> Path | None:
    try:
        candidate = Path(__file__).resolve().with_name(f"setup_{channel}.py")
    except (NameError, OSError):
        return None
    return candidate if candidate.is_file() else None


def _download_setup_script(channel: str) -> bytes:
    url = SETUP_URLS[channel]
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "cx-notifier-installer"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _run_setup(channel: str, arguments: list[str]) -> int:
    local = _local_setup_script(channel)
    if local is not None:
        return _run([sys.executable, "-B", str(local), *arguments]).returncode

    try:
        source = _download_setup_script(channel)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        print(f"下载 {channel} 配置脚本失败：{exc}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="cx-notifier-setup-") as temporary:
        script = Path(temporary) / f"setup_{channel}.py"
        script.write_bytes(source)
        return _run([sys.executable, "-B", str(script), *arguments]).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client",
        choices=("auto", "codex", "claude", "both"),
        default="auto",
        help="安装目标；auto 会安装到当前检测到的所有客户端",
    )
    parser.add_argument(
        "--channel",
        choices=("desktop", "feishu", "none"),
        default="desktop",
        help="安装后配置的渠道；默认 desktop",
    )
    parser.add_argument("--config", type=Path, help="覆盖默认配置文件路径")
    parser.add_argument("--replace", action="store_true", help="替换同名现有渠道")
    parser.add_argument("--no-test", action="store_true", help="配置后不发送测试通知")
    parser.add_argument(
        "--no-signature",
        action="store_true",
        help="飞书机器人未启用签名校验",
    )
    parser.add_argument(
        "--mention-all",
        action="store_true",
        help="飞书通知追加 @所有人",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.channel != "feishu" and (args.no_signature or args.mention_all):
        print("--no-signature 和 --mention-all 仅适用于飞书渠道。", file=sys.stderr)
        return 2

    try:
        clients = _select_clients(args.client)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    for client, executable in clients:
        if not _install_for(client, executable):
            return 1

    if args.channel != "none":
        result = _run_setup(args.channel, _setup_arguments(args))
        if result != 0:
            return result

    print("CX Notifier 安装完成。请重新加载插件或启动新会话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
