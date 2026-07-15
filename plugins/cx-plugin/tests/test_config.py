from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))

from cx_notify.config import (  # noqa: E402
    ConfigError,
    ResolvedChannel,
    default_config,
    load_config,
    resolve_data_dir,
    write_config,
)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "config.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_config_round_trip_and_permissions(self) -> None:
        write_config(default_config(), self.path)
        config = load_config(self.path)
        self.assertEqual(config.channels, ())
        self.assertEqual(config.delivery.timeout_seconds, 1.5)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_rules_route_by_event_project_and_client(self) -> None:
        data = default_config()
        data["channels"] = [
            {"name": "urgent", "type": "desktop", "enabled": True},
            {"name": "normal", "type": "desktop", "enabled": True},
        ]
        data["rules"] = [
            {
                "name": "codex-production-permission",
                "events": ["permission_*"],
                "projects": ["prod-*"],
                "clients": ["codex"],
                "channels": ["urgent"],
            },
            {
                "name": "completion",
                "events": ["task_completed"],
                "channels": ["normal"],
            },
        ]
        write_config(data, self.path)
        config = load_config(self.path)
        channels, diagnostics = config.resolve_channels({})
        self.assertEqual(diagnostics, ())
        routed = config.route_channels(
            channels,
            event="permission_request",
            project="prod-api",
            project_id="sha256:project",
            client="codex",
        )
        self.assertEqual([channel.name for channel in routed], ["urgent"])
        self.assertEqual(
            config.route_channels(
                channels,
                event="permission_request",
                project="dev-api",
                project_id="sha256:project",
                client="codex",
            ),
            (),
        )

    def test_rules_reject_unknown_channels_and_hmac_header_injection(self) -> None:
        data = default_config()
        data["channels"] = [{"name": "desktop", "type": "desktop"}]
        data["rules"] = [{"name": "bad", "channels": ["missing"]}]
        write_config(data, self.path)
        with self.assertRaisesRegex(ConfigError, "unknown channel"):
            load_config(self.path)

        data = default_config()
        data["channels"] = [
            {
                "name": "signed",
                "type": "hmac",
                "webhook_env": "HOOK",
                "secret_env": "SECRET",
                "signature_header": "X-Good\nInjected",
            }
        ]
        write_config(data, self.path)
        with self.assertRaisesRegex(ConfigError, "valid HTTP header"):
            load_config(self.path)

    def test_claude_plugin_data_directory_is_supported(self) -> None:
        claude_data = Path(self.temporary.name) / "claude-data"
        self.assertEqual(
            resolve_data_dir(
                {"CLAUDE_PLUGIN_DATA": str(claude_data)}, config_path=self.path
            ),
            claude_data,
        )

    def test_environment_references_are_resolved_without_persisting_values(self) -> None:
        data = default_config()
        data["channels"] = [
            {
                "name": "alerts",
                "type": "feishu",
                "webhook_env": "FEISHU_WEBHOOK",
                "secret_env": "FEISHU_SECRET",
                "mention_all": True,
            }
        ]
        write_config(data, self.path)
        config = load_config(self.path)
        channels, diagnostics = config.resolve_channels(
            {
                "FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/example",
                "FEISHU_SECRET": "signing-secret",
            }
        )
        self.assertEqual(diagnostics, ())
        self.assertEqual(channels[0].secret, "signing-secret")
        self.assertTrue(channels[0].mention_all)
        self.assertNotIn("signing-secret", self.path.read_text(encoding="utf-8"))

    def test_mention_all_is_feishu_only_and_type_strict(self) -> None:
        invalid_type = default_config()
        invalid_type["channels"] = [
            {
                "name": "alerts",
                "type": "feishu",
                "webhook_env": "FEISHU_WEBHOOK",
                "mention_all": "yes",
            }
        ]
        write_config(invalid_type, self.path)
        with self.assertRaisesRegex(ConfigError, "mention_all must be boolean"):
            load_config(self.path)

        wrong_provider = default_config()
        wrong_provider["channels"] = [
            {
                "name": "alerts",
                "type": "wecom",
                "webhook_env": "WECOM_WEBHOOK",
                "mention_all": True,
            }
        ]
        write_config(wrong_provider, self.path)
        with self.assertRaisesRegex(ConfigError, "unknown keys for wecom"):
            load_config(self.path)

    def test_missing_environment_value_skips_channel(self) -> None:
        data = default_config()
        data["channels"] = [
            {"name": "alerts", "type": "wecom", "webhook_env": "WECOM_WEBHOOK"}
        ]
        write_config(data, self.path)
        channels, diagnostics = load_config(self.path).resolve_channels({})
        self.assertEqual(channels, ())
        self.assertEqual(diagnostics, ("channel_unconfigured:alerts",))

    def test_inline_secret_requires_owner_only_file(self) -> None:
        data = default_config()
        data["channels"] = [
            {
                "name": "alerts",
                "type": "wecom",
                "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret",
            }
        ]
        write_config(data, self.path)
        if os.name == "nt":
            self.skipTest("POSIX mode check")
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(ConfigError, "0600"):
            load_config(self.path)

    def test_unknown_and_conflicting_fields_are_rejected(self) -> None:
        invalid = default_config()
        invalid["channels"] = [
            {
                "name": "alerts",
                "type": "wecom",
                "webhook_url": "https://qyapi.weixin.qq.com/example",
                "webhook_env": "WECOM_WEBHOOK",
            }
        ]
        write_config(invalid, self.path)
        with self.assertRaises(ConfigError):
            load_config(self.path)

        self.path.write_text(
            '{"version":1,"version":2,"channels":[],"privacy":{},"delivery":{}}',
            encoding="utf-8",
        )
        os.chmod(self.path, 0o600)
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_delivery_numbers_are_type_strict(self) -> None:
        invalid_values = (
            ("timeout_seconds", True),
            ("timeout_seconds", "1.5"),
            ("max_attempts", 1.5),
            ("max_attempts", False),
            ("backoff_seconds", "0.2"),
            ("dedupe_ttl_seconds", 60.5),
            ("timeout_seconds", 2.0),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                data = default_config()
                data["delivery"][field] = value
                write_config(data, self.path)
                with self.assertRaises(ConfigError):
                    load_config(self.path)

    def test_write_config_does_not_repermission_existing_parent(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX mode check")
        parent = Path(self.temporary.name) / "existing"
        parent.mkdir(mode=0o755)
        os.chmod(parent, 0o755)
        write_config(default_config(), parent / "config.json")
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)

    def test_configuration_cli_init_add_validate_and_list(self) -> None:
        script = ROOT / "scripts" / "configure.py"
        environment = os.environ.copy()
        environment["CLI_WEBHOOK"] = "https://hooks.example.com/codex"
        commands = (
            ("init",),
            ("add", "--type", "webhook", "--name", "cli", "--webhook-env", "CLI_WEBHOOK"),
            ("validate",),
            ("list",),
            ("remove", "cli"),
            ("list",),
        )
        outputs: list[str] = []
        for command in commands:
            completed = subprocess.run(
                [sys.executable, "-B", str(script), "--config", str(self.path), *command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
                timeout=5,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            outputs.append(completed.stdout)
        self.assertIn("Configuration is valid", outputs[2])
        listed = json.loads(outputs[3])
        self.assertEqual(listed["channels"][0]["webhook_env"], "CLI_WEBHOOK")
        self.assertNotIn(environment["CLI_WEBHOOK"], outputs[3])
        self.assertEqual(json.loads(outputs[5])["channels"], [])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_cli_doctor_simulate_and_status(self) -> None:
        script = ROOT / "scripts" / "configure.py"
        data = default_config()
        data["channels"] = [{"name": "desktop", "type": "desktop", "enabled": True}]
        data["rules"] = [{"name": "all", "channels": ["desktop"]}]
        write_config(data, self.path)
        for command in (("simulate",), ("status",)):
            completed = subprocess.run(
                [sys.executable, "-B", str(script), "--config", str(self.path), *command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            if command[0] == "simulate":
                self.assertFalse(payload["sent"])
                self.assertEqual(payload["matched_channels"], ["desktop"])
            else:
                self.assertIn("codes", payload)

    def test_cli_rejects_prompted_insecure_url_without_persisting_it(self) -> None:
        script = ROOT / "scripts" / "configure.py"
        subprocess.run(
            [sys.executable, "-B", str(script), "--config", str(self.path), "init"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=5,
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--config",
                str(self.path),
                "add",
                "--type",
                "webhook",
                "--name",
                "bad",
                "--webhook-prompt",
            ],
            input="http://example.com/hook\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(load_config(self.path).channels, ())
        self.assertNotIn("http://example.com", self.path.read_text(encoding="utf-8"))

        invalid = default_config()
        invalid["unexpected"] = True
        write_config(invalid, self.path)
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_cli_can_toggle_feishu_mention_all_without_exposing_secrets(self) -> None:
        data = default_config()
        data["channels"] = [
            {
                "name": "feishu-main",
                "type": "feishu",
                "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/private",
                "secret": "private-signing-secret",
            }
        ]
        write_config(data, self.path)
        script = ROOT / "scripts" / "configure.py"
        for state, expected in (("on", True), ("off", False)):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(script),
                    "--config",
                    str(self.path),
                    "set-mention-all",
                    "feishu-main",
                    state,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=5,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(load_config(self.path).channels[0].mention_all, expected)
            self.assertNotIn("private-signing-secret", completed.stdout)
            self.assertNotIn("private", completed.stdout)

    def test_config_example_is_valid(self) -> None:
        example = ROOT / "config.example.json"
        json.loads(example.read_text(encoding="utf-8"))
        config = load_config(example)
        self.assertEqual(len(config.channels), 6)
        self.assertEqual(len(config.rules), 2)


if __name__ == "__main__":
    unittest.main()
