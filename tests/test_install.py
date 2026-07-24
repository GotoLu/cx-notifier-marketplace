from __future__ import annotations

import importlib.util
import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install", ROOT / "scripts" / "install.py"
)
assert SPEC is not None and SPEC.loader is not None
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class InstallTests(unittest.TestCase):
    def test_windows_bootstrap_uses_python_installer_and_feishu_default(self) -> None:
        source = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "https://raw.githubusercontent.com/GotoLu/cx-notifier-marketplace/main/scripts/install.py",
            source,
        )
        self.assertIn('@("--channel", "feishu")', source)
        self.assertIn('"py"', source)
        self.assertIn('"python"', source)
        self.assertIn('"python3"', source)
        self.assertIn("sys.version_info >= (3, 10)", source)
        self.assertIn(r"*\WindowsApps\*", source)
        self.assertIn("Remove-Item", source)

    def test_auto_installs_all_detected_clients_and_configures_once(self) -> None:
        installs: list[tuple[str, str]] = []
        with mock.patch.object(
            install.shutil,
            "which",
            side_effect=lambda name: f"/bin/{name}",
        ), mock.patch.object(
            install,
            "_install_for",
            side_effect=lambda client, executable: installs.append(
                (client, executable)
            )
            or True,
        ), mock.patch.object(
            install,
            "_run_setup",
            return_value=0,
        ) as run_setup:
            with redirect_stdout(io.StringIO()):
                result = install.main([])

        self.assertEqual(result, 0)
        self.assertEqual(
            installs,
            [("codex", "/bin/codex"), ("claude", "/bin/claude")],
        )
        run_setup.assert_called_once_with("desktop", [])

    def test_codex_uses_marketplace_and_plugin_add_commands(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(command, *, capture=False):
            calls.append(tuple(command))
            stdout = "" if capture else None
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        with mock.patch.object(install, "_run", side_effect=fake_run):
            with redirect_stdout(io.StringIO()):
                result = install._install_for("codex", "/bin/codex")

        self.assertTrue(result)
        self.assertIn(
            (
                "/bin/codex",
                "plugin",
                "marketplace",
                "add",
                "GotoLu/cx-notifier-marketplace",
            ),
            calls,
        )
        self.assertIn(
            ("/bin/codex", "plugin", "add", "cx-plugin@cx-notifier"),
            calls,
        )

    def test_existing_plugin_skips_install_commands(self) -> None:
        with mock.patch.object(
            install, "_plugin_installed", return_value=True
        ), mock.patch.object(install, "_run") as run:
            with redirect_stdout(io.StringIO()):
                result = install._install_for("claude", "/bin/claude")

        self.assertTrue(result)
        run.assert_not_called()

    def test_feishu_options_are_forwarded(self) -> None:
        with mock.patch.object(
            install,
            "_select_clients",
            return_value=[("codex", "/bin/codex")],
        ), mock.patch.object(
            install,
            "_install_for",
            return_value=True,
        ), mock.patch.object(
            install,
            "_run_setup",
            return_value=0,
        ) as run_setup:
            with redirect_stdout(io.StringIO()):
                result = install.main(
                    [
                        "--channel",
                        "feishu",
                        "--no-signature",
                        "--mention-all",
                        "--replace",
                        "--no-test",
                    ]
                )

        self.assertEqual(result, 0)
        run_setup.assert_called_once_with(
            "feishu",
            ["--replace", "--no-test", "--no-signature", "--mention-all"],
        )

    def test_missing_requested_client_fails_before_install(self) -> None:
        with mock.patch.object(install.shutil, "which", return_value=None):
            with redirect_stderr(io.StringIO()):
                result = install.main(["--client", "codex"])
        self.assertEqual(result, 2)

    def test_install_failure_stops_before_channel_setup(self) -> None:
        with mock.patch.object(
            install,
            "_select_clients",
            return_value=[("codex", "/bin/codex")],
        ), mock.patch.object(
            install,
            "_install_for",
            return_value=False,
        ), mock.patch.object(install, "_run_setup") as run_setup:
            result = install.main([])

        self.assertEqual(result, 1)
        run_setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
