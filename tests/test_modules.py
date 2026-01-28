# SPDX-License-Identifier: MIT
"""Tests for the pcons module/add-on system."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass


@pytest.fixture
def module_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for test modules."""
    modules_dir = tmp_path / "test_modules"
    modules_dir.mkdir()
    return modules_dir


@pytest.fixture(autouse=True)
def clean_modules() -> None:
    """Clean up loaded modules before and after each test."""
    # Import here to avoid issues with module replacement
    from pcons import modules  # type: ignore[attr-defined]

    modules.clear_modules()
    yield
    modules.clear_modules()


class TestGetSearchPaths:
    """Tests for get_search_paths()."""

    def test_empty_when_no_paths_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns empty list when no module directories exist."""
        from pcons import modules  # type: ignore[attr-defined]

        monkeypatch.delenv("PCONS_MODULES_PATH", raising=False)
        monkeypatch.chdir(tmp_path)  # No pcons_modules here

        paths = modules.get_search_paths()
        # Should not include non-existent paths
        assert all(p.exists() for p in paths)

    def test_includes_env_var_paths(
        self, module_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Includes paths from PCONS_MODULES_PATH environment variable."""
        from pcons import modules  # type: ignore[attr-defined]

        monkeypatch.setenv("PCONS_MODULES_PATH", str(module_dir))
        paths = modules.get_search_paths()
        assert module_dir in paths

    def test_includes_local_pcons_modules(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Includes ./pcons_modules/ if it exists."""
        from pcons import modules  # type: ignore[attr-defined]

        local_modules = tmp_path / "pcons_modules"
        local_modules.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PCONS_MODULES_PATH", raising=False)

        paths = modules.get_search_paths()
        assert local_modules in paths


class TestLoadModules:
    """Tests for load_modules()."""

    def test_loads_module_from_path(self, module_dir: Path) -> None:
        """Successfully loads a module from the search path."""
        from pcons import modules  # type: ignore[attr-defined]

        # Create a test module
        test_module = module_dir / "testmod.py"
        test_module.write_text(
            '''\
"""Test module."""

__pcons_module__ = {
    "name": "testmod",
    "version": "1.0.0",
}

def hello() -> str:
    return "world"
'''
        )

        # Load modules with the extra path
        loaded = modules.load_modules([module_dir])

        assert "testmod" in loaded
        assert loaded["testmod"].hello() == "world"

    def test_calls_register_function(self, module_dir: Path) -> None:
        """Calls register() function on loaded modules."""
        from pcons import modules  # type: ignore[attr-defined]

        # Create a module with register function
        test_module = module_dir / "regmod.py"
        test_module.write_text(
            """\
_registered = False

def register() -> None:
    global _registered
    _registered = True

def was_registered() -> bool:
    return _registered
"""
        )

        modules.load_modules([module_dir])
        loaded = modules.get_module("regmod")

        assert loaded is not None
        assert loaded.was_registered() is True

    def test_skips_underscore_files(self, module_dir: Path) -> None:
        """Skips files starting with underscore."""
        from pcons import modules  # type: ignore[attr-defined]

        # Create a file that should be skipped
        (module_dir / "_private.py").write_text("x = 1")
        (module_dir / "__init__.py").write_text("")

        modules.load_modules([module_dir])

        assert "_private" not in modules.list_modules()
        assert "__init__" not in modules.list_modules()

    def test_first_found_wins(self, tmp_path: Path) -> None:
        """First module found (higher priority path) wins."""
        from pcons import modules  # type: ignore[attr-defined]

        # Create two directories with same-named module
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        (dir1 / "dupmod.py").write_text("value = 'first'")
        (dir2 / "dupmod.py").write_text("value = 'second'")

        # Load with dir1 first
        modules.load_modules([dir1, dir2])
        loaded = modules.get_module("dupmod")

        assert loaded is not None
        assert loaded.value == "first"

    def test_handles_invalid_module_gracefully(self, module_dir: Path) -> None:
        """Handles modules with syntax errors gracefully."""
        from pcons import modules  # type: ignore[attr-defined]

        # Create a valid module
        (module_dir / "valid.py").write_text("x = 1")
        # Create an invalid module
        (module_dir / "invalid.py").write_text("def broken(")

        # Should not raise, just log warning
        loaded = modules.load_modules([module_dir])

        assert "valid" in loaded
        assert "invalid" not in loaded


class TestModuleNamespace:
    """Tests for the pcons.modules namespace."""

    def test_attribute_access(self, module_dir: Path) -> None:
        """Can access loaded modules via attribute."""
        from pcons import modules  # type: ignore[attr-defined]

        (module_dir / "attrmod.py").write_text("value = 42")
        modules.load_modules([module_dir])

        assert modules.attrmod.value == 42

    def test_raises_attribute_error_for_missing(self) -> None:
        """Raises AttributeError for non-existent modules."""
        from pcons import modules  # type: ignore[attr-defined]

        with pytest.raises(AttributeError, match="No module named"):
            _ = modules.nonexistent

    def test_dir_lists_modules(self, module_dir: Path) -> None:
        """dir() lists loaded modules."""
        from pcons import modules  # type: ignore[attr-defined]

        (module_dir / "dirmod.py").write_text("x = 1")
        modules.load_modules([module_dir])

        attrs = dir(modules)
        assert "dirmod" in attrs
        # Also includes module-level functions
        assert "load_modules" in attrs
        assert "list_modules" in attrs

    def test_import_from_syntax(self, module_dir: Path) -> None:
        """Can import modules using 'from pcons.modules import x' syntax."""
        # Note: This test is a bit tricky because we need the module
        # to be registered in sys.modules before import
        from pcons import modules  # type: ignore[attr-defined]

        (module_dir / "importmod.py").write_text("answer = 42")
        modules.load_modules([module_dir])

        # The module should now be importable
        assert "pcons.modules.importmod" in sys.modules


class TestListAndClearModules:
    """Tests for list_modules() and clear_modules()."""

    def test_list_modules_returns_names(self, module_dir: Path) -> None:
        """list_modules() returns list of loaded module names."""
        from pcons import modules  # type: ignore[attr-defined]

        (module_dir / "mod1.py").write_text("")
        (module_dir / "mod2.py").write_text("")
        modules.load_modules([module_dir])

        names = modules.list_modules()
        assert "mod1" in names
        assert "mod2" in names

    def test_clear_modules_removes_all(self, module_dir: Path) -> None:
        """clear_modules() removes all loaded modules."""
        from pcons import modules  # type: ignore[attr-defined]

        (module_dir / "clearmod.py").write_text("")
        modules.load_modules([module_dir])
        assert "clearmod" in modules.list_modules()

        modules.clear_modules()
        assert modules.list_modules() == []
        assert "pcons.modules.clearmod" not in sys.modules


class TestContribModules:
    """Tests for the pcons.contrib package."""

    def test_contrib_package_imports(self) -> None:
        """Can import contrib package."""
        from pcons.contrib import list_modules

        modules = list_modules()
        assert isinstance(modules, list)

    def test_bundle_module_imports(self) -> None:
        """Can import bundle module."""
        from pcons.contrib import bundle

        assert hasattr(bundle, "generate_info_plist")
        assert hasattr(bundle, "create_macos_bundle")
        assert hasattr(bundle, "create_flat_bundle")
        assert hasattr(bundle, "get_arch_subdir")

    def test_platform_module_imports(self) -> None:
        """Can import platform module."""
        from pcons.contrib import platform

        assert hasattr(platform, "is_macos")
        assert hasattr(platform, "is_linux")
        assert hasattr(platform, "is_windows")
        assert hasattr(platform, "get_shared_lib_extension")


class TestBundleHelpers:
    """Tests for contrib.bundle helpers."""

    def test_generate_info_plist(self) -> None:
        """generate_info_plist creates valid plist content."""
        from pcons.contrib.bundle import generate_info_plist

        plist = generate_info_plist("MyPlugin", "1.2.3")

        assert "<?xml version" in plist
        assert "<key>CFBundleName</key>" in plist
        assert "<string>MyPlugin</string>" in plist
        assert "<string>1.2.3</string>" in plist

    def test_generate_info_plist_with_options(self) -> None:
        """generate_info_plist accepts custom options."""
        from pcons.contrib.bundle import generate_info_plist

        plist = generate_info_plist(
            "MyApp",
            "2.0.0",
            bundle_type="APPL",
            identifier="com.mycompany.myapp",
            executable="MyAppExec",
            extra_keys={"NSHighResolutionCapable": "true"},
        )

        assert "<string>APPL</string>" in plist
        assert "<string>com.mycompany.myapp</string>" in plist
        assert "<string>MyAppExec</string>" in plist
        assert "<key>NSHighResolutionCapable</key>" in plist

    def test_get_arch_subdir_macos(self) -> None:
        """get_arch_subdir returns correct values for macOS."""
        from pcons.contrib.bundle import get_arch_subdir

        assert get_arch_subdir("darwin", "x86_64") == "MacOS-x86-64"
        assert get_arch_subdir("darwin", "arm64") == "MacOS-arm-64"

    def test_get_arch_subdir_linux(self) -> None:
        """get_arch_subdir returns correct values for Linux."""
        from pcons.contrib.bundle import get_arch_subdir

        assert get_arch_subdir("linux", "x86_64") == "Linux-x86-64"
        assert get_arch_subdir("linux2", "x86_64") == "Linux-x86-64"

    def test_get_arch_subdir_windows(self) -> None:
        """get_arch_subdir returns correct values for Windows."""
        from pcons.contrib.bundle import get_arch_subdir

        assert get_arch_subdir("win32", "x86_64") == "Win64"
        assert get_arch_subdir("win32", "x86") == "Win32"


class TestPlatformHelpers:
    """Tests for contrib.platform helpers."""

    def test_get_platform_name(self) -> None:
        """get_platform_name returns current platform."""
        from pcons.contrib.platform import get_platform_name

        name = get_platform_name()
        assert name in ("darwin", "linux", "win32")

    def test_platform_checks_are_exclusive(self) -> None:
        """Exactly one platform check returns True."""
        from pcons.contrib.platform import is_linux, is_macos, is_windows

        checks = [is_macos(), is_linux(), is_windows()]
        assert sum(checks) == 1

    def test_get_shared_lib_extension(self) -> None:
        """get_shared_lib_extension returns correct extension."""
        from pcons.contrib.platform import get_shared_lib_extension

        ext = get_shared_lib_extension()
        assert ext in (".dylib", ".so", ".dll")
        assert ext.startswith(".")

    def test_format_shared_lib_name(self) -> None:
        """format_shared_lib_name creates correct filename."""
        from pcons.contrib.platform import format_shared_lib_name

        name = format_shared_lib_name("mylib")
        assert "mylib" in name
        # Should have extension
        assert name.endswith((".dylib", ".so", ".dll"))
