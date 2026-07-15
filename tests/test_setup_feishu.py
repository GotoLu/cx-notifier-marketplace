from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "setup_feishu", ROOT / "scripts" / "setup_feishu.py"
)
assert SPEC is not None and SPEC.loader is not None
setup_feishu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_feishu)


class SetupFeishuTests(unittest.TestCase):
    def test_claude_plugin_list_resolves_installed_configurer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "plugin"
            configure = plugin / "scripts" / "configure.py"
            configure.parent.mkdir(parents=True)
            configure.touch()
            output = json.dumps(
                [
                    {
                        "id": "cx-plugin@cx-notifier",
                        "installPath": str(plugin),
                        "installedAt": "2026-07-15T00:00:00Z",
                    }
                ]
            )
            with mock.patch.object(setup_feishu.shutil, "which", return_value="claude"), mock.patch.object(
                setup_feishu, "_run_output", return_value=output
            ):
                self.assertEqual(setup_feishu._from_claude(), configure)

    def test_codex_plugin_list_resolves_path_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "plugin with spaces"
            configure = plugin / "scripts" / "configure.py"
            configure.parent.mkdir(parents=True)
            configure.touch()
            output = (
                "cx-plugin@cx-notifier  installed, enabled  0.2.0  "
                f"{plugin}\n"
            )
            with mock.patch.object(setup_feishu.Path, "home", return_value=Path(temporary) / "home"), mock.patch.object(
                setup_feishu.shutil, "which", return_value="codex"
            ), mock.patch.object(setup_feishu, "_run_output", return_value=output):
                self.assertEqual(setup_feishu._from_codex(), configure)

    def test_workflow_uses_hidden_prompts_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configure = Path(temporary) / "configure.py"
            config = Path(temporary) / "config.json"
            calls: list[tuple[str, ...]] = []

            def fake_run(_configure, _config, *arguments, capture=False):
                del capture
                calls.append(arguments)
                if arguments == ("init",):
                    config.touch()
                return subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(setup_feishu, "locate_configure", return_value=configure), mock.patch.object(
                setup_feishu, "_existing_channels", return_value=[]
            ), mock.patch.object(setup_feishu, "_run_configure", side_effect=fake_run):
                with redirect_stdout(io.StringIO()):
                    result = setup_feishu.main(
                        ["--config", str(config), "--no-test", "--mention-all"]
                    )

            self.assertEqual(result, 0)
            self.assertIn(("init",), calls)
            self.assertIn(
                (
                    "add",
                    "--type",
                    "feishu",
                    "--name",
                    "feishu-main",
                    "--webhook-prompt",
                    "--secret-prompt",
                    "--mention-all",
                ),
                calls,
            )
            self.assertIn(("validate",), calls)


if __name__ == "__main__":
    unittest.main()
