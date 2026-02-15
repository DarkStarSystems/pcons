# SPDX-License-Identifier: MIT
"""Tests for pcons.contrib.windows.msvcup."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pcons.contrib.windows.msvcup import MsvcUp, ensure_msvc


class TestMsvcUpInit:
    """Tests for MsvcUp construction."""

    def test_basic_init(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        assert up._msvc_version == "14.44.17.14"
        assert up._sdk_version == "10.0.22621.7"
        assert up._target_cpu is None
        assert up._msvcup_version == "v2026_02_07"
        assert up._lock_file is None

    def test_init_with_all_options(self):
        up = MsvcUp(
            "14.44.17.14",
            "10.0.22621.7",
            target_cpu="arm64",
            msvcup_version="v2025_09_04",
            lock_file="msvcup.lock",
        )
        assert up._target_cpu == "arm64"
        assert up._msvcup_version == "v2025_09_04"
        assert up._lock_file == Path("msvcup.lock")


class TestArchDetection:
    """Tests for architecture auto-detection."""

    def test_resolve_target_cpu_x86_64(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        mock_platform = MagicMock(arch="x86_64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            assert up._resolve_target_cpu() == "x64"

    def test_resolve_target_cpu_arm64(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        mock_platform = MagicMock(arch="arm64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            assert up._resolve_target_cpu() == "arm64"

    def test_resolve_target_cpu_explicit_override(self):
        """Explicit target_cpu bypasses auto-detection."""
        up = MsvcUp("14.44.17.14", "10.0.22621.7", target_cpu="arm64")
        # Should return "arm64" without calling get_platform at all
        assert up._resolve_target_cpu() == "arm64"

    def test_resolve_target_cpu_cross_compile(self):
        """x86_64 host targeting arm64."""
        up = MsvcUp("14.44.17.14", "10.0.22621.7", target_cpu="arm64")
        mock_platform = MagicMock(arch="x86_64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            assert up._resolve_target_cpu() == "arm64"

    def test_resolve_target_cpu_unsupported_arch(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        mock_platform = MagicMock(arch="riscv64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            with pytest.raises(RuntimeError, match="Unsupported host architecture"):
                up._resolve_target_cpu()

    def test_resolve_host_arch_x86_64(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        mock_platform = MagicMock(arch="x86_64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            assert up._resolve_host_arch() == "x86_64"

    def test_resolve_host_arch_arm64(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        mock_platform = MagicMock(arch="arm64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            assert up._resolve_host_arch() == "aarch64"

    def test_resolve_host_arch_unsupported(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        mock_platform = MagicMock(arch="riscv64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            with pytest.raises(RuntimeError, match="No msvcup binary available"):
                up._resolve_host_arch()


class TestDownloadUrl:
    """Tests for download URL construction."""

    def test_url_x86_64(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7", msvcup_version="v2026_02_07")
        mock_platform = MagicMock(arch="x86_64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            host_arch = up._resolve_host_arch()
            url = up.RELEASE_URL.format(version=up._msvcup_version, host_arch=host_arch)
            assert url == (
                "https://github.com/marler8997/msvcup/releases/download"
                "/v2026_02_07/msvcup-x86_64-windows.zip"
            )

    def test_url_arm64(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7", msvcup_version="v2026_02_07")
        mock_platform = MagicMock(arch="arm64")
        with patch(
            "pcons.contrib.windows.msvcup.get_platform", return_value=mock_platform
        ):
            host_arch = up._resolve_host_arch()
            url = up.RELEASE_URL.format(version=up._msvcup_version, host_arch=host_arch)
            assert url == (
                "https://github.com/marler8997/msvcup/releases/download"
                "/v2026_02_07/msvcup-aarch64-windows.zip"
            )


class TestCommandConstruction:
    """Tests for subprocess command construction."""

    @patch("subprocess.run")
    def test_install_command(self, mock_run: MagicMock):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        msvcup_exe = Path(r"C:\msvcup\bin\msvcup.exe")
        up._run_install(msvcup_exe)
        default_lock = str(Path(MsvcUp.MSVCUP_DIR) / "msvcup.lock")
        mock_run.assert_called_once_with(
            [
                str(msvcup_exe),
                "install",
                "--lock-file",
                default_lock,
                "--manifest-update-off",
                "msvc-14.44.17.14",
                "sdk-10.0.22621.7",
            ],
            check=True,
        )

    @patch("subprocess.run")
    def test_install_command_with_lock_file(self, mock_run: MagicMock):
        up = MsvcUp("14.44.17.14", "10.0.22621.7", lock_file="msvcup.lock")
        msvcup_exe = Path(r"C:\msvcup\bin\msvcup.exe")
        up._run_install(msvcup_exe)
        mock_run.assert_called_once_with(
            [
                str(msvcup_exe),
                "install",
                "--lock-file",
                "msvcup.lock",
                "--manifest-update-off",
                "msvc-14.44.17.14",
                "sdk-10.0.22621.7",
            ],
            check=True,
        )

    @patch("subprocess.run")
    def test_autoenv_command_x64(self, mock_run: MagicMock):
        up = MsvcUp("14.44.17.14", "10.0.22621.7", target_cpu="x64")
        msvcup_exe = Path(r"C:\msvcup\bin\msvcup.exe")
        autoenv_dir = up._run_autoenv(msvcup_exe)
        # Compare as strings: on Linux, Path("C:\msvcup") / "autoenv-x64"
        # uses forward slashes; on Windows it uses backslashes. Both are fine.
        assert str(autoenv_dir).endswith("autoenv-x64")
        assert "msvcup" in str(autoenv_dir)
        args = mock_run.call_args[0][0]
        assert args[0] == str(msvcup_exe)
        assert args[1:3] == ["autoenv", "--target-cpu"]
        assert args[3] == "x64"
        assert args[4] == "--out-dir"
        assert args[5] == str(autoenv_dir)
        assert args[6:] == ["msvc-14.44.17.14", "sdk-10.0.22621.7"]

    @patch("subprocess.run")
    def test_autoenv_command_arm64(self, mock_run: MagicMock):
        up = MsvcUp("14.44.17.14", "10.0.22621.7", target_cpu="arm64")
        msvcup_exe = Path(r"C:\msvcup\bin\msvcup.exe")
        autoenv_dir = up._run_autoenv(msvcup_exe)
        assert str(autoenv_dir).endswith("autoenv-arm64")
        args = mock_run.call_args[0][0]
        assert args[3] == "arm64"
        assert args[6:] == ["msvc-14.44.17.14", "sdk-10.0.22621.7"]


class TestPathModification:
    """Tests for PATH environment variable modification."""

    def test_add_to_path_prepends(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        autoenv_dir = Path(r"C:\msvcup\autoenv-x64")
        original_path = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = r"C:\Windows\system32"
            up._add_to_path(autoenv_dir)
            assert os.environ["PATH"].startswith(str(autoenv_dir))
            assert os.environ["PATH"].endswith(r"C:\Windows\system32")
        finally:
            os.environ["PATH"] = original_path

    def test_add_to_path_idempotent(self):
        up = MsvcUp("14.44.17.14", "10.0.22621.7")
        autoenv_dir = Path(r"C:\msvcup\autoenv-x64")
        original_path = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(autoenv_dir) + os.pathsep + r"C:\Windows\system32"
            up._add_to_path(autoenv_dir)
            # Should not add a second copy
            assert os.environ["PATH"].count(str(autoenv_dir)) == 1
        finally:
            os.environ["PATH"] = original_path


class TestEnsureMsvc:
    """Tests for the ensure_msvc convenience function."""

    def test_non_windows_returns_empty_path(self):
        """On non-Windows, ensure_msvc is a no-op."""
        with patch("pcons.contrib.windows.msvcup.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = ensure_msvc("14.44.17.14", "10.0.22621.7")
            assert result == Path()

    def test_non_windows_skips_on_real_platform(self):
        """On the actual non-Windows test platform, ensure_msvc skips."""
        if sys.platform == "win32":
            pytest.skip("Test only applicable on non-Windows")
        result = ensure_msvc("14.44.17.14", "10.0.22621.7")
        assert result == Path()

    @patch.object(MsvcUp, "ensure_installed")
    def test_windows_delegates_to_msvcup(self, mock_ensure: MagicMock):
        """On Windows, ensure_msvc delegates to MsvcUp.ensure_installed."""
        mock_ensure.return_value = Path(r"C:\msvcup\autoenv-x64")
        with patch("pcons.contrib.windows.msvcup.sys") as mock_sys:
            mock_sys.platform = "win32"
            result = ensure_msvc(
                "14.44.17.14",
                "10.0.22621.7",
                target_cpu="x64",
                msvcup_version="v2025_09_04",
            )
            assert result == Path(r"C:\msvcup\autoenv-x64")
            mock_ensure.assert_called_once()


class TestModuleImport:
    """Tests for module importability."""

    def test_importable_on_all_platforms(self):
        """Module should be importable regardless of platform."""
        from pcons.contrib.windows import msvcup  # noqa: F811

        assert hasattr(msvcup, "ensure_msvc")
        assert hasattr(msvcup, "MsvcUp")

    def test_listed_in_windows_modules(self):
        """msvcup should be listed in the windows contrib package."""
        from pcons.contrib.windows import list_modules

        assert "msvcup" in list_modules()
