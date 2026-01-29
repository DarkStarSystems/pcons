# SPDX-License-Identifier: MIT
"""Tests for package finders."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pcons.packages.description import PackageDescription
from pcons.packages.finders.base import BaseFinder, FinderChain
from pcons.packages.finders.pkgconfig import PkgConfigFinder
from pcons.packages.finders.system import PACKAGE_ALIASES, SystemFinder


class MockFinder(BaseFinder):
    """Mock finder for testing."""

    def __init__(self, results: dict[str, PackageDescription | None]) -> None:
        self._results = results

    @property
    def name(self) -> str:
        return "mock"

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        return self._results.get(package_name)


class TestFinderChain:
    """Tests for FinderChain."""

    def test_find_with_first_finder(self) -> None:
        """Test finding with first finder."""
        pkg1 = PackageDescription(name="test", version="1.0")
        finder1 = MockFinder({"test": pkg1})
        finder2 = MockFinder({"test": PackageDescription(name="test", version="2.0")})

        chain = FinderChain([finder1, finder2])
        result = chain.find("test")

        assert result is pkg1  # Should use first finder's result

    def test_find_with_second_finder(self) -> None:
        """Test falling back to second finder."""
        pkg2 = PackageDescription(name="other", version="2.0")
        finder1 = MockFinder({})  # First finder finds nothing
        finder2 = MockFinder({"other": pkg2})

        chain = FinderChain([finder1, finder2])
        result = chain.find("other")

        assert result is pkg2

    def test_find_not_found(self) -> None:
        """Test when no finder finds the package."""
        finder1 = MockFinder({})
        finder2 = MockFinder({})

        chain = FinderChain([finder1, finder2])
        result = chain.find("nonexistent")

        assert result is None


class TestPkgConfigFinder:
    """Tests for PkgConfigFinder."""

    def test_is_available(self) -> None:
        """Test checking if pkg-config is available."""
        finder = PkgConfigFinder()
        # This might be True or False depending on the system
        # Just make sure it doesn't crash
        _ = finder.is_available()

    def test_name(self) -> None:
        """Test finder name."""
        finder = PkgConfigFinder()
        assert finder.name == "pkg-config"

    @pytest.mark.skipif(
        shutil.which("pkg-config") is None, reason="pkg-config not available"
    )
    def test_find_zlib(self) -> None:
        """Test finding zlib (common package)."""
        finder = PkgConfigFinder()
        result = finder.find("zlib")

        # zlib might or might not be installed
        if result is not None:
            assert result.name == "zlib"
            assert result.found_by == "pkg-config"
            assert "z" in result.libraries

    def test_find_nonexistent(self) -> None:
        """Test finding a package that doesn't exist."""
        finder = PkgConfigFinder()
        if finder.is_available():
            result = finder.find("nonexistent_package_xyz_123")
            assert result is None

    def test_parse_flags(self) -> None:
        """Test flag parsing."""
        finder = PkgConfigFinder()
        flags = finder._parse_flags("-I/usr/include -DTEST")
        assert flags == ["-I/usr/include", "-DTEST"]

    def test_extract_includes(self) -> None:
        """Test extracting include directories."""
        finder = PkgConfigFinder()
        flags = ["-I/usr/include", "-I/opt/include", "-DTEST"]
        includes, remaining = finder._extract_includes(flags)
        assert includes == ["/usr/include", "/opt/include"]
        assert remaining == ["-DTEST"]


class TestSystemFinder:
    """Tests for SystemFinder."""

    def test_name(self) -> None:
        """Test finder name."""
        finder = SystemFinder()
        assert finder.name == "system"

    def test_find_unknown_package(self) -> None:
        """Test finding a package not in PACKAGE_ALIASES."""
        finder = SystemFinder()
        result = finder.find("totally_unknown_package_xyz")
        assert result is None

    def test_package_aliases_exist(self) -> None:
        """Test that PACKAGE_ALIASES has common packages."""
        assert "zlib" in PACKAGE_ALIASES
        assert "pthread" in PACKAGE_ALIASES
        assert "m" in PACKAGE_ALIASES

    def test_find_with_missing_header(self, tmp_path: Path) -> None:
        """Test that missing header returns None."""
        finder = SystemFinder(
            include_paths=[tmp_path / "include"],
            library_paths=[tmp_path / "lib"],
        )
        (tmp_path / "include").mkdir()
        (tmp_path / "lib").mkdir()

        # zlib.h doesn't exist, should return None
        result = finder.find("zlib")
        assert result is None

    def test_find_with_header_and_lib(self, tmp_path: Path) -> None:
        """Test finding a package with header and library present."""
        include_dir = tmp_path / "include"
        lib_dir = tmp_path / "lib"
        include_dir.mkdir()
        lib_dir.mkdir()

        # Create fake zlib.h with platform-appropriate library file
        (include_dir / "zlib.h").write_text('#define ZLIB_VERSION "1.2.13"\n')
        if sys.platform == "win32":
            lib_name = "z.lib"
        elif sys.platform == "darwin":
            lib_name = "libz.dylib"
        else:
            lib_name = "libz.a"
        (lib_dir / lib_name).write_text("")

        finder = SystemFinder(
            include_paths=[include_dir],
            library_paths=[lib_dir],
        )

        result = finder.find("zlib")
        assert result is not None
        assert result.name == "zlib"
        assert str(include_dir) in result.include_dirs
        assert str(lib_dir) in result.library_dirs
        assert "z" in result.libraries
        assert result.found_by == "system"

    def test_extract_version_from_header(self, tmp_path: Path) -> None:
        """Test extracting version from header."""
        include_dir = tmp_path / "include"
        lib_dir = tmp_path / "lib"
        include_dir.mkdir()
        lib_dir.mkdir()

        # Create header with version
        (include_dir / "zlib.h").write_text(
            '#define ZLIB_VERSION "1.2.13"\nint compress();\n'
        )
        # Use platform-appropriate library name
        if sys.platform == "win32":
            lib_name = "z.lib"
        elif sys.platform == "darwin":
            lib_name = "libz.dylib"
        else:
            lib_name = "libz.so"
        (lib_dir / lib_name).write_text("")

        finder = SystemFinder(
            include_paths=[include_dir],
            library_paths=[lib_dir],
        )

        result = finder.find("zlib")
        assert result is not None
        assert result.version == "1.2.13"


class TestPkgConfigFinderMocked:
    """Tests for PkgConfigFinder with mocked subprocess."""

    def test_find_with_mocked_pkg_config(self) -> None:
        """Test finding a package with mocked pkg-config."""
        finder = PkgConfigFinder()

        # Mock the subprocess.run calls
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if "--exists" in cmd:
                result.returncode = 0
                result.stdout = ""
            elif "--modversion" in cmd:
                result.returncode = 0
                result.stdout = "1.2.3"
            elif "--cflags" in cmd:
                result.returncode = 0
                result.stdout = "-I/usr/include/test -DTEST_LIB"
            elif "--libs" in cmd:
                result.returncode = 0
                result.stdout = "-L/usr/lib -ltest"
            elif "--variable=prefix" in cmd:
                result.returncode = 0
                result.stdout = "/usr"
            elif "--print-requires" in cmd:
                result.returncode = 0
                result.stdout = ""
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        with (
            patch.object(finder, "is_available", return_value=True),
            patch("subprocess.run", side_effect=mock_run),
        ):
            result = finder.find("testpkg")

            assert result is not None
            assert result.name == "testpkg"
            assert result.version == "1.2.3"
            assert "/usr/include/test" in result.include_dirs
            assert "TEST_LIB" in result.defines
            assert "/usr/lib" in result.library_dirs
            assert "test" in result.libraries
            assert result.prefix == "/usr"
            assert result.found_by == "pkg-config"
