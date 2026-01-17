# SPDX-License-Identifier: MIT
"""Tests for ConanFinder."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pcons.packages.finders.conan import ConanFinder


class TestConanFinderBasic:
    """Basic tests for ConanFinder."""

    def test_name(self) -> None:
        """Test finder name."""
        finder = ConanFinder()
        assert finder.name == "conan"

    def test_is_available_without_conan(self) -> None:
        """Test is_available when conan is not installed."""
        finder = ConanFinder(conan_cmd="nonexistent_conan_xyz")
        assert finder.is_available() is False

    def test_profile_path(self, tmp_path: Path) -> None:
        """Test profile path calculation."""
        finder = ConanFinder(output_folder=tmp_path / "deps")
        assert finder.profile_path == tmp_path / "deps" / "pcons-profile"

    def test_pkgconfig_dir(self, tmp_path: Path) -> None:
        """Test pkgconfig directory path."""
        finder = ConanFinder(output_folder=tmp_path / "deps")
        assert finder.pkgconfig_dir == tmp_path / "deps"

    def test_repr(self) -> None:
        """Test string representation."""
        finder = ConanFinder(
            conanfile="test.txt",
            output_folder="build/deps",
        )
        repr_str = repr(finder)
        assert "ConanFinder" in repr_str
        assert "test.txt" in repr_str
        # Check for path components (Windows uses backslash)
        assert "build" in repr_str and "deps" in repr_str


class TestConanFinderProfile:
    """Tests for profile generation."""

    def test_sync_profile_creates_file(self, tmp_path: Path) -> None:
        """Test that sync_profile creates profile file."""
        finder = ConanFinder(output_folder=tmp_path)
        profile_path = finder.sync_profile()

        assert profile_path.exists()
        content = profile_path.read_text()
        assert "[settings]" in content

    def test_sync_profile_includes_os(self, tmp_path: Path) -> None:
        """Test that profile includes OS setting."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile()

        content = finder.profile_path.read_text()
        # Should have one of these OS values
        assert any(
            os_name in content for os_name in ["os=Macos", "os=Linux", "os=Windows"]
        )

    def test_sync_profile_includes_build_type(self, tmp_path: Path) -> None:
        """Test that profile includes build type."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile(build_type="Debug")

        content = finder.profile_path.read_text()
        assert "build_type=Debug" in content

    def test_set_profile_setting(self, tmp_path: Path) -> None:
        """Test custom profile settings."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.set_profile_setting("compiler.cppstd", "20")
        finder.sync_profile()

        content = finder.profile_path.read_text()
        assert "compiler.cppstd=20" in content

    def test_set_profile_conf(self, tmp_path: Path) -> None:
        """Test custom profile conf values."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.set_profile_conf("tools.build:cxxflags", ["-Wall", "-Werror"])
        finder.sync_profile()

        content = finder.profile_path.read_text()
        assert "[conf]" in content
        assert "tools.build:cxxflags" in content


class TestConanFinderInstall:
    """Tests for conan install with mocked subprocess."""

    def test_install_without_conan_raises(self, tmp_path: Path) -> None:
        """Test that install raises when conan is not available."""
        finder = ConanFinder(
            conanfile=tmp_path / "conanfile.txt",
            output_folder=tmp_path / "deps",
            conan_cmd="nonexistent_conan_xyz",
        )

        with pytest.raises(RuntimeError, match="Conan is not available"):
            finder.install()

    def test_install_runs_conan_command(self, tmp_path: Path) -> None:
        """Test that install runs the correct conan command."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
            build_missing=True,
        )
        finder.sync_profile()

        # Create a mock .pc file so parsing succeeds
        pc_file = output_folder / "zlib.pc"
        pc_file.write_text(
            """prefix=/usr/local
libdir=${prefix}/lib
includedir=${prefix}/include

Name: zlib
Description: zlib compression library
Version: 1.3
Libs: -L${libdir} -lz
Cflags: -I${includedir}
"""
        )

        captured_calls: list = []

        def mock_run(cmd, **kwargs):
            captured_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Installing packages..."
            result.stderr = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
        ):
            finder.install()

            # Find the conan install call (not pkg-config calls)
            # Note: conan may be invoked via "uvx conan" so check entire command
            conan_calls = [c for c in captured_calls if "conan" in str(c)]
            assert len(conan_calls) > 0, f"Expected conan call, got: {captured_calls}"
            call_args = conan_calls[0]
            assert "install" in call_args
            assert "-g" in call_args
            assert "PkgConfigDeps" in call_args
            assert "--build=missing" in call_args

    def test_install_parses_pc_files(self, tmp_path: Path) -> None:
        """Test that install parses generated .pc files."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile()

        # Create mock .pc file
        pc_file = output_folder / "zlib.pc"
        pc_file.write_text(
            """prefix=/opt/conan
libdir=${prefix}/lib
includedir=${prefix}/include

Name: zlib
Description: zlib compression library
Version: 1.3.1
Libs: -L${libdir} -lz
Cflags: -I${includedir}
"""
        )

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
        ):
            packages = finder.install()

            assert "zlib" in packages
            pkg = packages["zlib"]
            assert pkg.name == "zlib"
            assert pkg.found_by == "conan"


class TestConanFinderCaching:
    """Tests for caching functionality."""

    def test_cache_key_changes_with_conanfile(self, tmp_path: Path) -> None:
        """Test that cache key changes when conanfile changes."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile()

        key1 = finder._compute_cache_key()

        # Modify conanfile
        conanfile.write_text("[requires]\nzlib/1.3.1\n")
        key2 = finder._compute_cache_key()

        assert key1 != key2

    def test_cache_key_changes_with_profile(self, tmp_path: Path) -> None:
        """Test that cache key changes when profile changes."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        output_folder = tmp_path / "deps"
        output_folder.mkdir()

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=output_folder,
        )
        finder.sync_profile(build_type="Release")
        key1 = finder._compute_cache_key()

        finder.sync_profile(build_type="Debug")
        key2 = finder._compute_cache_key()

        assert key1 != key2

    def test_is_cache_valid_false_initially(self, tmp_path: Path) -> None:
        """Test that cache is invalid initially."""
        finder = ConanFinder(output_folder=tmp_path)
        finder.sync_profile()

        assert finder._is_cache_valid() is False

    def test_cache_valid_after_save(self, tmp_path: Path) -> None:
        """Test that cache is valid after saving."""
        conanfile = tmp_path / "conanfile.txt"
        conanfile.write_text("[requires]\nzlib/1.3\n")

        finder = ConanFinder(
            conanfile=conanfile,
            output_folder=tmp_path,
        )
        finder.sync_profile()
        finder._save_cache_key()

        assert finder._is_cache_valid() is True


class TestConanFinderPcParsing:
    """Tests for .pc file parsing."""

    def test_parse_single_pc_file(self, tmp_path: Path) -> None:
        """Test parsing a single .pc file."""
        pc_file = tmp_path / "test.pc"
        pc_file.write_text(
            """prefix=/usr/local
libdir=${prefix}/lib
includedir=${prefix}/include

Name: test
Version: 1.2.3
Cflags: -I${includedir} -DTEST_DEFINE
Libs: -L${libdir} -ltest -lpthread
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        pkg = finder._parse_single_pc_file(pc_file)

        assert pkg is not None
        assert pkg.name == "test"
        assert pkg.version == "1.2.3"
        assert "/usr/local/include" in pkg.include_dirs
        assert "TEST_DEFINE" in pkg.defines
        assert "/usr/local/lib" in pkg.library_dirs
        assert "test" in pkg.libraries
        assert "pthread" in pkg.libraries

    def test_parse_pc_files_manually(self, tmp_path: Path) -> None:
        """Test manual .pc file parsing."""
        # Create multiple .pc files
        (tmp_path / "foo.pc").write_text(
            """Name: foo
Version: 1.0
Libs: -lfoo
"""
        )
        (tmp_path / "bar.pc").write_text(
            """Name: bar
Version: 2.0
Libs: -lbar
Cflags: -I/opt/bar/include
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        packages = finder._parse_pc_files_manually()

        assert "foo" in packages
        assert "bar" in packages
        assert packages["foo"].version == "1.0"
        assert packages["bar"].version == "2.0"
        assert "bar" in packages["bar"].libraries
        assert "/opt/bar/include" in packages["bar"].include_dirs


class TestConanFinderFind:
    """Tests for find() method."""

    def test_find_without_install_returns_none(self, tmp_path: Path) -> None:
        """Test find returns None when install hasn't been called."""
        finder = ConanFinder(output_folder=tmp_path)
        result = finder.find("zlib")
        assert result is None

    def test_find_after_parsing(self, tmp_path: Path) -> None:
        """Test find after .pc files are parsed."""
        # Create .pc file with a unique package name to avoid system packages
        (tmp_path / "pcons_test_pkg.pc").write_text(
            """Name: pcons_test_pkg
Version: 2.5.0
Libs: -lpcons_test
Cflags: -I/opt/pcons_test/include
"""
        )

        finder = ConanFinder(output_folder=tmp_path)
        # Use manual parsing to avoid pkg-config finding system packages
        finder._packages = finder._parse_pc_files_manually()

        result = finder.find("pcons_test_pkg")
        assert result is not None
        assert result.name == "pcons_test_pkg"
        assert result.version == "2.5.0"

    def test_find_nonexistent_package(self, tmp_path: Path) -> None:
        """Test find with nonexistent package."""
        (tmp_path / "foo.pc").write_text("Name: foo\nVersion: 1.0\n")

        finder = ConanFinder(output_folder=tmp_path)
        finder._parse_pkgconfig_files()

        result = finder.find("nonexistent")
        assert result is None


class TestConanFinderWithToolchain:
    """Tests for toolchain integration."""

    def test_detect_compiler_settings_with_gcc_toolchain(self, tmp_path: Path) -> None:
        """Test compiler detection with GCC toolchain."""
        finder = ConanFinder(output_folder=tmp_path)

        # Mock toolchain
        toolchain = MagicMock()
        toolchain.name = "gcc"
        toolchain.version = "12.3.0"

        settings = finder._detect_compiler_settings(toolchain)

        assert settings["compiler"] == "gcc"
        assert settings["compiler.version"] == "12"
        assert settings["compiler.libcxx"] == "libstdc++11"

    def test_detect_compiler_settings_with_clang_toolchain(
        self, tmp_path: Path
    ) -> None:
        """Test compiler detection with Clang toolchain."""
        from pcons.configure.platform import Platform

        finder = ConanFinder(output_folder=tmp_path)

        toolchain = MagicMock()
        toolchain.name = "clang"
        toolchain.version = "15.0.0"

        # Create a mock platform that's not macOS
        mock_platform = MagicMock(spec=Platform)
        mock_platform.is_macos = False
        mock_platform.is_linux = True
        mock_platform.is_windows = False
        mock_platform.arch = "x86_64"

        original_platform = finder._platform
        finder._platform = mock_platform
        try:
            settings = finder._detect_compiler_settings(toolchain)
        finally:
            finder._platform = original_platform

        assert settings["compiler"] == "clang"
        assert settings["compiler.version"] == "15"

    def test_sync_profile_with_toolchain(self, tmp_path: Path) -> None:
        """Test profile generation with toolchain."""
        finder = ConanFinder(output_folder=tmp_path)

        toolchain = MagicMock()
        toolchain.name = "gcc"
        toolchain.version = "11"

        finder.sync_profile(toolchain=toolchain)

        content = finder.profile_path.read_text()
        assert "compiler=gcc" in content


class TestConanFinderCommandResolution:
    """Tests for conan command resolution."""

    def test_explicit_conan_cmd(self) -> None:
        """Test that explicit conan_cmd is used."""
        finder = ConanFinder(conan_cmd="/custom/path/conan")
        assert finder.conan_cmd == ["/custom/path/conan"]

    def test_pcons_conan_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test PCONS_CONAN environment variable."""
        monkeypatch.setenv("PCONS_CONAN", "/env/conan")
        # Clear any cached resolution
        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["/env/conan"]

    def test_conan_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test CONAN environment variable."""
        monkeypatch.delenv("PCONS_CONAN", raising=False)
        monkeypatch.setenv("CONAN", "/env/conan2")
        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["/env/conan2"]

    def test_pcons_conan_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test PCONS_CONAN takes precedence over CONAN."""
        monkeypatch.setenv("PCONS_CONAN", "/pcons/conan")
        monkeypatch.setenv("CONAN", "/other/conan")
        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["/pcons/conan"]

    def test_conan_cmd_is_list(self) -> None:
        """Test that conan_cmd returns a list."""
        finder = ConanFinder()
        cmd = finder.conan_cmd
        assert isinstance(cmd, list)
        assert len(cmd) >= 1

    def test_uvx_fallback_when_no_conan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test fallback to uvx when conan not in PATH."""
        monkeypatch.delenv("PCONS_CONAN", raising=False)
        monkeypatch.delenv("CONAN", raising=False)

        # Mock shutil.which to simulate conan not found but uvx found
        original_which = shutil.which

        def mock_which(cmd: str) -> str | None:
            if cmd == "conan":
                return None
            if cmd == "uvx":
                return "/usr/bin/uvx"
            if cmd == "uv":
                return "/usr/bin/uv"
            return original_which(cmd)

        monkeypatch.setattr(shutil, "which", mock_which)

        finder = ConanFinder()
        finder._resolved_conan_cmd = None
        assert finder.conan_cmd == ["uvx", "conan"]


@pytest.mark.skipif(shutil.which("conan") is None, reason="conan not available")
class TestConanFinderIntegration:
    """Integration tests that require conan to be installed."""

    def test_is_available_with_conan(self) -> None:
        """Test is_available when conan is installed."""
        finder = ConanFinder()
        assert finder.is_available() is True

    def test_conan_version_can_be_checked(self) -> None:
        """Test that we can run conan --version."""
        finder = ConanFinder()
        result = finder._run_conan("--version", check=False)
        assert result.returncode == 0
        assert "Conan" in result.stdout or "conan" in result.stdout.lower()
