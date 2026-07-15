from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))

from cx_notify.config import default_config, load_config, write_config  # noqa: E402
from cx_notify.config import ResolvedChannel  # noqa: E402
from cx_notify.providers import DeliveryResult, send_once  # noqa: E402
from cx_notify.runtime import make_test_event, run_hook  # noqa: E402
from cx_notify.state import DeliveryState, SafeLogger  # noqa: E402


def _claim_from_process(data_dir: str, start_event, result_queue) -> None:
    start_event.wait(5)
    try:
        state = DeliveryState(Path(data_dir))
        try:
            won = state.claim("process-key", "process-id", dedupe_ttl_seconds=600)
        finally:
            state.close()
        result_queue.put(("ok", won))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__))


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.json"
        self.data_dir = self.root / "data"
        self.environment = {
            "CX_NOTIFY_CONFIG": str(self.config_path),
            "CX_NOTIFY_DATA": str(self.data_dir),
            "TEST_WEBHOOK": "https://hooks.example.com/codex",
        }
        data = default_config()
        data["channels"] = [
            {
                "name": "primary",
                "type": "webhook",
                "webhook_env": "TEST_WEBHOOK",
            }
        ]
        write_config(data, self.config_path)
        self.hook_input = {
            "hook_event_name": "PermissionRequest",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": "/tmp/project",
            "tool_name": "Bash",
            "tool_input": {"command": "echo secret-value"},
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_success_then_duplicate_is_not_sent_again(self) -> None:
        calls: list[str] = []

        def transport(channel, event, **kwargs):
            calls.append(event.notification_id)
            return DeliveryResult(True, False, 200, "accepted")

        first = run_hook(
            self.hook_input,
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        second = run_hook(
            self.hook_input,
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["deduplicated"], 1)
        self.assertEqual(len(calls), 1)

    def test_retry_reuses_notification_id(self) -> None:
        calls: list[str] = []

        def transport(channel, event, **kwargs):
            calls.append(event.notification_id)
            if len(calls) == 1:
                return DeliveryResult(False, True, 500, "http_error")
            return DeliveryResult(True, False, 200, "accepted")

        summary = run_hook(
            self.hook_input,
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    def test_failure_is_fail_open_and_log_is_sanitized(self) -> None:
        def transport(channel, event, **kwargs):
            raise RuntimeError("https://hooks.example.com/?token=secret-value")

        summary = run_hook(
            self.hook_input,
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        self.assertEqual(summary["failed"], 1)
        log = (self.data_dir / "events.log").read_text(encoding="utf-8")
        self.assertNotIn("secret-value", log)
        self.assertNotIn("hooks.example.com", log)
        self.assertNotIn("echo", log)

    def test_unavailable_state_falls_back_to_delivery(self) -> None:
        calls = 0

        def transport(channel, event, **kwargs):
            nonlocal calls
            calls += 1
            return DeliveryResult(True, False, 200, "accepted")

        with mock.patch("cx_notify.runtime.DeliveryState", side_effect=OSError("broken")):
            summary = run_hook(
                self.hook_input,
                environ=self.environment,
                config_path=self.config_path,
                transport=transport,
            )
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(calls, 1)

    def test_normal_stop_sends_task_completed(self) -> None:
        events = []

        def transport(channel, event, **kwargs):
            events.append(event)
            return DeliveryResult(True, False, 200, "accepted")

        prompt_summary = run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "stop-session",
                "turn_id": "stop-turn",
                "prompt": "帮我完成任务。",
            },
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        summary = run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": "stop-session",
                "turn_id": "stop-turn",
                "last_assistant_message": "任务完成。",
            },
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        self.assertEqual(prompt_summary["sent"], 0)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event, "task_completed")
        self.assertEqual(events[0].title, "Codex 任务已结束")
        self.assertEqual(events[0].question_summary, "帮我完成任务。")

    def test_question_summary_is_sanitized_before_delivery(self) -> None:
        events = []

        def transport(channel, event, **kwargs):
            events.append(event)
            return DeliveryResult(True, False, 200, "accepted")

        captured = run_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "stop-session",
                "turn_id": "stop-turn",
                "cwd": "/tmp/demo",
                "prompt": (
                    "请删除 /private/tmp/1.txt，令牌 token=super-secret-value。"
                    + "很长" * 200
                ),
            },
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        summary = run_hook(
            {
                "hook_event_name": "Stop",
                "session_id": "stop-session",
                "turn_id": "stop-turn",
                "cwd": "/tmp/demo",
                "last_assistant_message": (
                    "主人，删除文件属于项目规范的红线操作。"
                    "是否允许我删除一份文件？"
                ),
            },
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        self.assertEqual(captured["sent"], 0)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(len(events), 1)
        payload = events[0].payload()
        self.assertEqual(payload["event"], "task_completed")
        self.assertEqual(payload["title"], "Codex 任务已结束")
        self.assertIn("question_summary", payload)
        self.assertIn("<path>", payload["question_summary"])
        self.assertIn("<redacted>", payload["question_summary"])
        self.assertLessEqual(len(payload["question_summary"]), 160)
        self.assertNotIn("1.txt", str(payload))
        self.assertNotIn("/private/tmp", str(payload))
        self.assertNotIn("主人", str(payload))

        database = (self.data_dir / "deliveries.sqlite3").read_bytes()
        self.assertNotIn(b"super-secret-value", database)
        self.assertNotIn(b"/private/tmp/1.txt", database)

    def test_claude_uses_latest_session_question_without_turn_id(self) -> None:
        events = []

        def transport(channel, event, **kwargs):
            events.append(event)
            return DeliveryResult(True, False, 200, "accepted")

        for prompt in ("第一次提问", "第二次提问"):
            run_hook(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "claude-session",
                    "prompt": prompt,
                },
                environ=self.environment,
                config_path=self.config_path,
                transport=transport,
                client="claude_code",
            )
            run_hook(
                {"hook_event_name": "Stop", "session_id": "claude-session"},
                environ=self.environment,
                config_path=self.config_path,
                transport=transport,
                client="claude_code",
            )

        self.assertEqual([event.question_summary for event in events], ["第一次提问", "第二次提问"])
        self.assertNotEqual(events[0].dedupe_key, events[1].dedupe_key)

    def test_concurrent_claim_has_single_winner(self) -> None:
        state = DeliveryState(self.data_dir)

        def claim() -> bool:
            return state.claim("same-key", "same-id", dedupe_ttl_seconds=600)

        try:
            with ThreadPoolExecutor(max_workers=20) as executor:
                results = list(executor.map(lambda _: claim(), range(20)))
            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 19)
        finally:
            state.close()

    def test_separate_database_connections_have_single_claim_winner(self) -> None:
        barrier = threading.Barrier(12)

        def claim() -> bool:
            barrier.wait(timeout=2)
            state = DeliveryState(self.data_dir)
            try:
                return state.claim("shared-key", "shared-id", dedupe_ttl_seconds=600)
            finally:
                state.close()

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda _: claim(), range(12)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 11)

    def test_separate_processes_have_single_claim_winner(self) -> None:
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_claim_from_process,
                args=(str(self.data_dir), start_event, result_queue),
            )
            for _ in range(8)
        ]
        for process in processes:
            process.start()
        start_event.set()
        results = [result_queue.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual([value for status, value in results if status == "ok"].count(True), 1)
        self.assertEqual([value for status, value in results if status == "ok"].count(False), 7)
        self.assertFalse([value for status, value in results if status == "error"])
        result_queue.close()

    def test_state_and_logger_preserve_existing_directory_permissions(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode check")
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        os.chmod(shared, 0o755)
        state = DeliveryState(shared)
        state.close()
        SafeLogger(shared).write("permission-test")
        self.assertEqual(shared.stat().st_mode & 0o777, 0o755)

    def test_all_sixteen_channels_start_in_one_delivery_wave(self) -> None:
        data = default_config()
        data["channels"] = [
            {
                "name": f"channel-{index}",
                "type": "webhook",
                "webhook_url": f"https://hooks{index}.example.com/codex",
            }
            for index in range(16)
        ]
        write_config(data, self.config_path)
        barrier = threading.Barrier(16)

        def transport(channel, event, **kwargs):
            barrier.wait(timeout=2)
            return DeliveryResult(True, False, 200, "accepted")

        summary = run_hook(
            self.hook_input,
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        self.assertEqual(summary["sent"], 16)

    def test_entrypoint_always_outputs_empty_json(self) -> None:
        environment = os.environ.copy()
        environment.update(self.environment)
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / "hooks" / "notify.py")],
            input=b"not-json",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
            timeout=5,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"{}\n")
        self.assertEqual(completed.stderr, b"")

    def test_entrypoint_permission_stop_and_large_input_are_notification_only(self) -> None:
        empty_config = self.root / "empty-config.json"
        write_config(default_config(), empty_config)
        environment = os.environ.copy()
        environment.update(
            {
                "CX_NOTIFY_CONFIG": str(empty_config),
                "CX_NOTIFY_DATA": str(self.root / "entry-data"),
            }
        )
        inputs = (
            {
                **self.hook_input,
                "tool_input": {"command": "x" * (2 * 1024 * 1024)},
            },
            {
                "hook_event_name": "Stop",
                "session_id": "entry-session",
                "turn_id": "entry-turn",
                "cwd": "/tmp/project",
                "last_assistant_message": "任务完成。",
            },
        )
        for payload in inputs:
            with self.subTest(event=payload["hook_event_name"]):
                completed = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "hooks" / "notify.py")],
                    input=json.dumps(payload).encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    check=False,
                    timeout=5,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, b"{}\n")
                self.assertEqual(completed.stderr, b"")

    def test_mixed_channel_failure_does_not_block_successful_channel(self) -> None:
        data = default_config()
        data["channels"] = [
            {
                "name": "healthy",
                "type": "webhook",
                "webhook_url": "https://healthy.example.com/codex",
            },
            {
                "name": "broken",
                "type": "webhook",
                "webhook_url": "https://broken.example.com/codex",
            },
        ]
        write_config(data, self.config_path)

        def transport(channel, event, **kwargs):
            if channel.name == "healthy":
                return DeliveryResult(True, False, 200, "accepted")
            return DeliveryResult(False, False, 400, "http_error")

        summary = run_hook(
            self.hook_input,
            environ=self.environment,
            config_path=self.config_path,
            transport=transport,
        )
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["failed"], 1)


class LocalWebhookTests(unittest.TestCase):
    def test_real_http_delivery_uses_post_and_idempotency_key(self) -> None:
        records: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                records.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "idempotency": self.headers.get("Idempotency-Key"),
                        "body": json.loads(body),
                    }
                )
                self.send_response(204)
                self.end_headers()

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config_path = root / "config.json"
                data = default_config()
                data["channels"] = [
                    {
                        "name": "local",
                        "type": "webhook",
                        "webhook_url": f"http://127.0.0.1:{server.server_port}/notify",
                    }
                ]
                data["delivery"]["allow_insecure_localhost"] = True
                write_config(data, config_path)
                summary = run_hook(
                    {
                        "hook_event_name": "PermissionRequest",
                        "session_id": "s",
                        "turn_id": "t",
                        "cwd": "/tmp/project",
                        "tool_name": "Bash",
                        "tool_input": {"command": "echo hidden"},
                    },
                    environ={
                        "CX_NOTIFY_CONFIG": str(config_path),
                        "CX_NOTIFY_DATA": str(root / "data"),
                    },
                    config_path=config_path,
                )
                self.assertEqual(summary["sent"], 1)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["method"], "POST")
            self.assertTrue(records[0]["idempotency"].startswith("cxn_"))
            self.assertEqual(records[0]["body"]["schema"], "codex.notification.v1")
            self.assertNotIn("echo hidden", json.dumps(records[0]["body"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_redirect_is_not_followed_and_slow_body_respects_deadline(self) -> None:
        paths: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                paths.append(self.path)
                if self.path in {"/redirect", "/redirect307"}:
                    self.send_response(302 if self.path == "/redirect" else 307)
                    self.send_header("Location", "/accepted")
                    self.end_headers()
                    return
                if self.path == "/slow-feishu":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "10")
                    self.end_headers()
                    self.wfile.flush()
                    threading.Event().wait(0.4)
                    try:
                        self.wfile.write(b'{"code":0}')
                    except BrokenPipeError:
                        pass
                    return
                self.send_response(204)
                self.end_headers()

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            redirect = ResolvedChannel(
                name="redirect",
                type="webhook",
                webhook_url=f"http://127.0.0.1:{server.server_port}/redirect",
            )
            redirect_result = send_once(
                redirect,
                make_test_event(),
                timeout_seconds=0.2,
                allow_insecure_localhost=True,
            )
            self.assertFalse(redirect_result.success)
            self.assertEqual(redirect_result.status, 302)
            self.assertEqual(paths, ["/redirect"])

            redirect_307 = ResolvedChannel(
                name="redirect-307",
                type="webhook",
                webhook_url=f"http://127.0.0.1:{server.server_port}/redirect307",
            )
            redirect_307_result = send_once(
                redirect_307,
                make_test_event(),
                timeout_seconds=0.2,
                allow_insecure_localhost=True,
            )
            self.assertFalse(redirect_307_result.success)
            self.assertEqual(redirect_307_result.status, 307)
            self.assertEqual(paths, ["/redirect", "/redirect307"])

            slow = ResolvedChannel(
                name="slow",
                type="feishu",
                webhook_url=f"http://127.0.0.1:{server.server_port}/slow-feishu",
            )
            slow_result = send_once(
                slow,
                make_test_event(),
                timeout_seconds=0.05,
                allow_insecure_localhost=True,
            )
            self.assertFalse(slow_result.success)
            self.assertTrue(slow_result.retryable)
            self.assertEqual(slow_result.diagnostic, "timeout")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_hook_watchdog_returns_before_outer_five_second_timeout(self) -> None:
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                release.wait(6)
                try:
                    self.send_response(204)
                    self.end_headers()
                except BrokenPipeError:
                    pass

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config_path = root / "config.json"
                data = default_config()
                data["delivery"]["max_attempts"] = 1
                data["channels"] = [
                    {
                        "name": "hanging",
                        "type": "webhook",
                        "webhook_url": f"http://127.0.0.1:{server.server_port}/hang",
                    }
                ]
                data["delivery"]["allow_insecure_localhost"] = True
                write_config(data, config_path)
                environment = os.environ.copy()
                environment.update(
                    {
                        "CX_NOTIFY_CONFIG": str(config_path),
                        "CX_NOTIFY_DATA": str(root / "data"),
                    }
                )
                started = time.monotonic()
                completed = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "hooks" / "notify.py")],
                    input=json.dumps(
                        {
                            "hook_event_name": "PermissionRequest",
                            "session_id": "watchdog-session",
                            "turn_id": "watchdog-turn",
                            "cwd": "/tmp/project",
                            "tool_name": "Bash",
                            "tool_input": {"command": "echo hidden"},
                        }
                    ).encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    check=False,
                    timeout=5.5,
                )
                elapsed = time.monotonic() - started
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, b"{}\n")
                self.assertEqual(completed.stderr, b"")
                self.assertLess(elapsed, 5.0)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
