from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))

from cx_notify.events import parse_hook_event  # noqa: E402


class EventParsingTests(unittest.TestCase):
    def permission_input(self) -> dict:
        return {
            "hook_event_name": "PermissionRequest",
            "session_id": "session-secret",
            "turn_id": "turn-secret",
            "cwd": "/Users/alice/work/private-repo",
            "tool_name": "Bash",
            "tool_input": {
                "command": "deploy --token sk-supersecretvalue",
                "description": "Deploy using token=sk-supersecretvalue from /Users/alice/key.txt",
            },
        }

    def test_permission_request_is_exact_and_minimal(self) -> None:
        result = parse_hook_event(self.permission_input())
        self.assertIsNotNone(result.event)
        event = result.event
        assert event is not None
        payload = event.payload()
        self.assertEqual(payload["event"], "permission_request")
        self.assertEqual(payload["tool_name"], "Bash")
        self.assertEqual(payload["project_name"], "private-repo")
        self.assertNotIn("detail", payload)
        serialized = str(payload)
        self.assertNotIn("deploy --token", serialized)
        self.assertNotIn("session-secret", serialized)
        self.assertNotIn("turn-secret", serialized)

    def test_optional_description_is_redacted(self) -> None:
        result = parse_hook_event(
            self.permission_input(), include_permission_description=True
        )
        assert result.event is not None
        detail = result.event.detail or ""
        self.assertIn("<redacted>", detail)
        self.assertIn("<path>", detail)
        self.assertNotIn("sk-supersecretvalue", detail)
        self.assertNotIn("/Users/alice", detail)

    def test_stop_includes_a_sanitized_task_summary(self) -> None:
        result = parse_hook_event(
            {
                "hook_event_name": "Stop",
                "session_id": "stop-session",
                "turn_id": "stop-turn",
                "cwd": "/tmp/demo",
                "last_assistant_message": (
                    "已更新 /private/tmp/secret.txt，令牌 token=super-secret-value。"
                ),
            }
        )
        self.assertIsNone(result.diagnostic)
        self.assertIsNotNone(result.event)
        assert result.event is not None
        payload = result.event.payload()
        self.assertEqual(payload["event"], "task_completed")
        self.assertEqual(payload["title"], "Codex 任务已结束")
        self.assertIn("task_summary", payload)
        self.assertIn("<path>", payload["task_summary"])
        self.assertIn("<redacted>", payload["task_summary"])
        self.assertNotIn("/private/tmp", str(payload))
        self.assertNotIn("super-secret-value", str(payload))
        self.assertIn("任务简介：", result.event.render_text())

    def test_stop_without_assistant_message_omits_task_summary(self) -> None:
        result = parse_hook_event({"hook_event_name": "Stop"})
        assert result.event is not None
        self.assertNotIn("task_summary", result.event.payload())

    def test_stop_deduplicates_retries_but_not_distinct_turns(self) -> None:
        base = {
            "hook_event_name": "Stop",
            "session_id": "same-session",
            "last_assistant_message": "任务完成。",
        }
        first = parse_hook_event({**base, "turn_id": "turn-1"})
        retry = parse_hook_event({**base, "turn_id": "turn-1"})
        next_turn = parse_hook_event({**base, "turn_id": "turn-2"})
        assert first.event is not None and retry.event is not None
        assert next_turn.event is not None
        self.assertEqual(first.event.dedupe_key, retry.event.dedupe_key)
        self.assertNotEqual(first.event.dedupe_key, next_turn.event.dedupe_key)

    def test_unknown_event_is_ignored(self) -> None:
        self.assertIsNone(parse_hook_event({"hook_event_name": "PostToolUse"}).event)

    def test_claude_code_event_has_claude_identity_and_schema(self) -> None:
        result = parse_hook_event(self.permission_input(), client="claude_code")
        assert result.event is not None
        payload = result.event.payload()
        self.assertEqual(payload["client"], "claude_code")
        self.assertEqual(payload["schema"], "claude.notification.v1")
        self.assertEqual(payload["title"], "Claude Code 有一项操作等待审批")
        rendered = result.event.render_text()
        self.assertIn("Claude Code 待确认", rendered)
        self.assertIn("请返回 Claude Code", rendered)

    def test_hash_project_mode_hides_directory_name(self) -> None:
        result = parse_hook_event(self.permission_input(), project_name_mode="hash")
        assert result.event is not None
        self.assertTrue(result.event.project_name.startswith("project-"))
        self.assertNotIn("private-repo", result.event.project_name)

    def test_description_redacts_general_paths_and_common_tokens(self) -> None:
        data = self.permission_input()
        data["tool_input"]["description"] = (
            "Read /private/tmp/secret.txt with AKIAABCDEFGHIJKLMNOP and "
            "xoxb-1234567890-secret and "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature123"
        )
        result = parse_hook_event(data, include_permission_description=True)
        assert result.event is not None
        detail = result.event.detail or ""
        self.assertIn("<path>", detail)
        self.assertNotIn("/private/tmp", detail)
        self.assertNotIn("AKIA", detail)
        self.assertNotIn("xoxb-", detail)
        self.assertNotIn("eyJhbGci", detail)


if __name__ == "__main__":
    unittest.main()
