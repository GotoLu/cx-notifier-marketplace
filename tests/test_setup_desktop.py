from __future__ import annotations

import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "setup_desktop", ROOT / "scripts" / "setup_desktop.py"
)
assert SPEC is not None and SPEC.loader is not None
setup_desktop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_desktop)


class SetupDesktopTests(unittest.TestCase):
    def test_workflow_adds_validates_and_tests_desktop_channel(self) -> None:
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

            with mock.patch.object(setup_desktop, "locate_configure", return_value=configure), mock.patch.object(
                setup_desktop, "_existing_channels", return_value=[]
            ), mock.patch.object(setup_desktop, "_run_configure", side_effect=fake_run):
                with redirect_stdout(io.StringIO()):
                    result = setup_desktop.main(["--config", str(config)])

            self.assertEqual(result, 0)
            self.assertIn(("init",), calls)
            self.assertIn(
                ("add", "--type", "desktop", "--name", "desktop-main"),
                calls,
            )
            self.assertIn(("validate",), calls)
            self.assertIn(("test", "--channel", "desktop-main"), calls)

    def test_existing_channel_requires_explicit_replace(self) -> None:
        configure = Path("configure.py")
        channel = {"name": "desktop-main", "type": "desktop", "enabled": True}
        with mock.patch.object(setup_desktop, "locate_configure", return_value=configure), mock.patch.object(
            setup_desktop, "_existing_channels", return_value=[channel]
        ):
            with redirect_stderr(io.StringIO()):
                result = setup_desktop.main([])
        self.assertEqual(result, 2)

    def test_failed_desktop_test_preserves_configuration_and_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configure = Path(temporary) / "configure.py"
            config = Path(temporary) / "config.json"

            def fake_run(_configure, _config, *arguments, capture=False):
                del capture
                if arguments == ("init",):
                    config.touch()
                return_code = 1 if arguments == ("test", "--channel", "desktop-main") else 0
                return subprocess.CompletedProcess([], return_code, stdout="", stderr="")

            with mock.patch.object(setup_desktop, "locate_configure", return_value=configure), mock.patch.object(
                setup_desktop, "_existing_channels", return_value=[]
            ), mock.patch.object(setup_desktop, "_run_configure", side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = setup_desktop.main(["--config", str(config)])
            self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
