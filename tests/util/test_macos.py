# SPDX-License-Identifier: MIT
"""Tests for pcons.util.macos."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Only import on macOS to avoid issues on other platforms
if sys.platform == "darwin":
    from pcons.util.macos import fix_dylib_references, get_dylib_install_name


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
class TestGetDylibInstallName:
    def test_parses_otool_output(self):
        """Test parsing of otool -D output."""
        mock_output = "/path/to/libfoo.1.2.3.dylib:\n/usr/local/lib/libfoo.1.dylib\n"

        with patch("subprocess.check_output", return_value=mock_output):
            result = get_dylib_install_name("/path/to/libfoo.1.2.3.dylib")

        assert result == "libfoo.1.dylib"

    def test_returns_basename_only(self):
        """Test that only the basename is returned."""
        mock_output = "/some/path/lib.dylib:\n/very/long/path/to/libbar.2.dylib\n"

        with patch("subprocess.check_output", return_value=mock_output):
            result = get_dylib_install_name("/some/path/lib.dylib")

        assert result == "libbar.2.dylib"
        assert "/" not in result

    def test_raises_on_no_install_name(self):
        """Test error when dylib has no install name."""
        mock_output = "/path/to/lib.dylib:\n"

        with patch("subprocess.check_output", return_value=mock_output):
            with pytest.raises(ValueError, match="No install name"):
                get_dylib_install_name("/path/to/lib.dylib")

    def test_accepts_path_object(self):
        """Test that Path objects are accepted."""
        mock_output = "/path/lib.dylib:\n/usr/lib/lib.dylib\n"

        with patch("subprocess.check_output", return_value=mock_output) as mock:
            get_dylib_install_name(Path("/path/lib.dylib"))

        # Verify the path was converted to string for subprocess
        call_args = mock.call_args[0][0]
        assert "/path/lib.dylib" in call_args

    def test_real_system_dylib(self):
        """Test with a real system dylib (integration test)."""
        # Use a dylib that should exist on all macOS systems
        libsystem = Path("/usr/lib/libSystem.B.dylib")
        if libsystem.exists():
            result = get_dylib_install_name(libsystem)
            assert result.startswith("libSystem")
            assert result.endswith(".dylib")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
class TestFixDylibReferences:
    def test_adds_post_build_commands(self):
        """Test that post_build commands are added to target."""
        mock_target = MagicMock()
        mock_output = "/path/lib.dylib:\n/opt/lib/libfoo.1.dylib\n"

        with patch("subprocess.check_output", return_value=mock_output):
            fix_dylib_references(
                mock_target,
                ["/path/lib.dylib"],
                "/opt/lib",
            )

        mock_target.post_build.assert_called_once()
        call_arg = mock_target.post_build.call_args[0][0]
        assert "install_name_tool -change" in call_arg
        assert "/opt/lib/libfoo.1.dylib" in call_arg
        assert "@rpath/libfoo.1.dylib" in call_arg

    def test_returns_install_names(self):
        """Test that install names are returned."""
        mock_target = MagicMock()

        def mock_otool(cmd, **kwargs):
            path = cmd[-1]
            if "core" in path:
                return "/p:\n/lib/libcore.1.dylib\n"
            else:
                return "/p:\n/lib/libutil.2.dylib\n"

        with patch("subprocess.check_output", side_effect=mock_otool):
            names = fix_dylib_references(
                mock_target,
                ["/path/libcore.1.0.dylib", "/path/libutil.2.0.dylib"],
                "/lib",
            )

        assert names == ["libcore.1.dylib", "libutil.2.dylib"]

    def test_custom_rpath_prefix(self):
        """Test custom rpath prefix."""
        mock_target = MagicMock()
        mock_output = "/p:\n/lib/libfoo.dylib\n"

        with patch("subprocess.check_output", return_value=mock_output):
            fix_dylib_references(
                mock_target,
                ["/path/lib.dylib"],
                "/lib",
                rpath_prefix="@loader_path/../Frameworks",
            )

        call_arg = mock_target.post_build.call_args[0][0]
        assert "@loader_path/../Frameworks/libfoo.dylib" in call_arg
