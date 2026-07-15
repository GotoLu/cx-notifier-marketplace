from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginFileTests(unittest.TestCase):
    def test_manifest_and_default_hook_discovery(self) -> None:
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], ROOT.name)
        self.assertEqual(manifest["version"].split("+", 1)[0], "0.5.0")
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("apps", manifest)
        self.assertNotIn("mcpServers", manifest)
        self.assertTrue((ROOT / "hooks" / "hooks.json").is_file())

    def test_claude_manifest_and_shared_hooks(self) -> None:
        manifest = json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], ROOT.name)
        self.assertNotIn("hooks", manifest)
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))[
            "hooks"
        ]
        self.assertEqual(set(hooks), {"PermissionRequest", "UserPromptSubmit", "Stop"})
        for groups in hooks.values():
            for group in groups:
                for handler in group["hooks"]:
                    self.assertEqual(handler["timeout"], 5)
                    self.assertIn("${CLAUDE_PLUGIN_ROOT:-${PLUGIN_ROOT}}", handler["command"])

    def test_hooks_are_notification_only_and_bounded(self) -> None:
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), {"PermissionRequest", "UserPromptSubmit", "Stop"})
        self.assertEqual(hooks["PermissionRequest"][0]["matcher"], "*")
        for groups in hooks.values():
            for group in groups:
                for handler in group["hooks"]:
                    self.assertEqual(handler["type"], "command")
                    self.assertEqual(handler["timeout"], 5)
                    self.assertIn("${PLUGIN_ROOT}", handler["command"])
                    self.assertIn("${CLAUDE_PLUGIN_ROOT", handler["command"])
                    command = handler["command"].lower()
                    self.assertNotIn("allow", command)
                    self.assertNotIn("deny", command)

    def test_no_prompt_protocol_hook_remains(self) -> None:
        self.assertFalse((ROOT / "hooks" / "session_start.py").exists())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("CODEX_CONFIRM_V1", readme)
        self.assertNotIn("红线确认", readme)

    def test_readme_does_not_offer_serverchan_or_remote_approval(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn('"type": "serverchan"', readme.lower())
        self.assertIn("不允许", readme)
        self.assertIn("远程批准", readme)


if __name__ == "__main__":
    unittest.main()
