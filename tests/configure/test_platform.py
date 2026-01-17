# SPDX-License-Identifier: MIT
"""Tests for pcons.configure.platform."""

import platform as stdlib_platform

from pcons.configure.platform import Platform, detect_platform, get_platform


class TestPlatform:
    def test_creation(self):
        p = Platform(
            os="linux",
            arch="x86_64",
            is_64bit=True,
            exe_suffix="",
            shared_lib_suffix=".so",
            shared_lib_prefix="lib",
            static_lib_suffix=".a",
            static_lib_prefix="lib",
            object_suffix=".o",
        )
        assert p.os == "linux"
        assert p.arch == "x86_64"
        assert p.is_64bit is True

    def test_is_properties(self):
        linux = Platform(
            os="linux",
            arch="x86_64",
            is_64bit=True,
            exe_suffix="",
            shared_lib_suffix=".so",
            shared_lib_prefix="lib",
            static_lib_suffix=".a",
            static_lib_prefix="lib",
            object_suffix=".o",
        )
        assert linux.is_linux is True
        assert linux.is_windows is False
        assert linux.is_macos is False
        assert linux.is_posix is True

        windows = Platform(
            os="windows",
            arch="x86_64",
            is_64bit=True,
            exe_suffix=".exe",
            shared_lib_suffix=".dll",
            shared_lib_prefix="",
            static_lib_suffix=".lib",
            static_lib_prefix="",
            object_suffix=".obj",
        )
        assert windows.is_windows is True
        assert windows.is_linux is False
        assert windows.is_posix is False

        macos = Platform(
            os="darwin",
            arch="arm64",
            is_64bit=True,
            exe_suffix="",
            shared_lib_suffix=".dylib",
            shared_lib_prefix="lib",
            static_lib_suffix=".a",
            static_lib_prefix="lib",
            object_suffix=".o",
        )
        assert macos.is_macos is True
        assert macos.is_posix is True

    def test_shared_lib_name(self):
        linux = Platform(
            os="linux",
            arch="x86_64",
            is_64bit=True,
            exe_suffix="",
            shared_lib_suffix=".so",
            shared_lib_prefix="lib",
            static_lib_suffix=".a",
            static_lib_prefix="lib",
            object_suffix=".o",
        )
        assert linux.shared_lib_name("foo") == "libfoo.so"

        windows = Platform(
            os="windows",
            arch="x86_64",
            is_64bit=True,
            exe_suffix=".exe",
            shared_lib_suffix=".dll",
            shared_lib_prefix="",
            static_lib_suffix=".lib",
            static_lib_prefix="",
            object_suffix=".obj",
        )
        assert windows.shared_lib_name("foo") == "foo.dll"

    def test_static_lib_name(self):
        linux = Platform(
            os="linux",
            arch="x86_64",
            is_64bit=True,
            exe_suffix="",
            shared_lib_suffix=".so",
            shared_lib_prefix="lib",
            static_lib_suffix=".a",
            static_lib_prefix="lib",
            object_suffix=".o",
        )
        assert linux.static_lib_name("foo") == "libfoo.a"

    def test_exe_name(self):
        linux = Platform(
            os="linux",
            arch="x86_64",
            is_64bit=True,
            exe_suffix="",
            shared_lib_suffix=".so",
            shared_lib_prefix="lib",
            static_lib_suffix=".a",
            static_lib_prefix="lib",
            object_suffix=".o",
        )
        assert linux.exe_name("app") == "app"

        windows = Platform(
            os="windows",
            arch="x86_64",
            is_64bit=True,
            exe_suffix=".exe",
            shared_lib_suffix=".dll",
            shared_lib_prefix="",
            static_lib_suffix=".lib",
            static_lib_prefix="",
            object_suffix=".obj",
        )
        assert windows.exe_name("app") == "app.exe"

    def test_frozen(self):
        p = Platform(
            os="linux",
            arch="x86_64",
            is_64bit=True,
            exe_suffix="",
            shared_lib_suffix=".so",
            shared_lib_prefix="lib",
            static_lib_suffix=".a",
            static_lib_prefix="lib",
            object_suffix=".o",
        )
        # Platform is frozen (dataclass)
        import dataclasses

        import pytest

        with pytest.raises(dataclasses.FrozenInstanceError):
            p.os = "windows"  # type: ignore


class TestDetectPlatform:
    def test_returns_platform(self):
        p = detect_platform()
        assert isinstance(p, Platform)

    def test_detects_current_os(self):
        p = detect_platform()
        system = stdlib_platform.system().lower()
        if system == "darwin":
            assert p.os == "darwin"
        elif system == "windows":
            assert p.os == "windows"
        elif system == "linux":
            assert p.os == "linux"

    def test_detects_architecture(self):
        p = detect_platform()
        # Should be one of the known architectures
        assert p.arch in ("x86_64", "arm64", "i686", "arm") or p.arch != ""

    def test_has_correct_suffixes(self):
        p = detect_platform()
        if p.is_windows:
            assert p.exe_suffix == ".exe"
            assert p.object_suffix == ".obj"
        else:
            assert p.exe_suffix == ""
            assert p.object_suffix == ".o"


class TestGetPlatform:
    def test_returns_same_instance(self):
        p1 = get_platform()
        p2 = get_platform()
        assert p1 is p2

    def test_returns_platform(self):
        p = get_platform()
        assert isinstance(p, Platform)
