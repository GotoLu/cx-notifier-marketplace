#!/usr/bin/env python3
"""Codex Hook entrypoint. It always exits without an approval decision."""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

from cx_notify.limits import HOOK_HARD_DEADLINE_SECONDS, MAX_HOOK_INPUT_BYTES
from cx_notify.runtime import run_hook


_OUTPUT_LOCK = threading.Lock()
_OUTPUT_EMITTED = False


def _emit_empty_response() -> None:
    """Write the notification-only Hook response at most once, without buffering."""

    global _OUTPUT_EMITTED
    with _OUTPUT_LOCK:
        if _OUTPUT_EMITTED:
            return
        try:
            os.write(sys.stdout.fileno(), b"{}\n")
        except (AttributeError, OSError, ValueError):
            sys.stdout.write("{}\n")
            sys.stdout.flush()
        _OUTPUT_EMITTED = True


def _deadline_guard(completed: threading.Event) -> None:
    if completed.wait(HOOK_HARD_DEADLINE_SECONDS):
        return
    _emit_empty_response()
    # The Hook process is disposable. A hard exit is the only reliable bound for
    # DNS and socket operations that the Python standard library cannot cancel.
    os._exit(0)


def _read_input() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def main() -> int:
    completed = threading.Event()
    watchdog = threading.Thread(
        target=_deadline_guard,
        args=(completed,),
        name="cx-notify-deadline",
        daemon=True,
    )
    watchdog.start()
    try:
        data = _read_input()
        if data is not None:
            try:
                run_hook(data)
            except Exception:
                # Notification failures must never change the Codex approval flow.
                pass
    finally:
        _emit_empty_response()
        completed.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
