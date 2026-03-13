# SPDX-License-Identifier: MIT
"""Tests for automatic install_name / SONAME on shared libraries."""

from __future__ import annotations

from unittest.mock import patch

from pcons.core.target import Target, TargetType
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain

# ── target.set_option() / target.get_option() ──────────────────────────────────────────────


class TestTargetSetGet:
    def test_default_is_none(self) -> None:
        t = Target("lib", target_type=TargetType.SHARED_LIBRARY)
        assert t.get_option("install_name") is None

    def test_set_and_get(self) -> None:
        t = Target("lib", target_type=TargetType.SHARED_LIBRARY)
        t.set_option("install_name", "@rpath/libcustom.dylib")
        assert t.get_option("install_name") == "@rpath/libcustom.dylib"

    def test_set_returns_self(self) -> None:
        t = Target("lib", target_type=TargetType.SHARED_LIBRARY)
        assert t.set_option("install_name", "foo") is t

    def test_get_with_default(self) -> None:
        t = Target("lib", target_type=TargetType.SHARED_LIBRARY)
        assert t.get_option("missing_key", "fallback") == "fallback"


# ── Toolchain.get_link_flags_for_target ───────────────────────────────────────


def _make_shared_target(name: str = "foo") -> Target:
    return Target(name, target_type=TargetType.SHARED_LIBRARY)


class TestGccInstallName:
    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_default_install_name(self, mock_platform) -> None:
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = GccToolchain()
        target = _make_shared_target()
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        assert flags == ["-Wl,-install_name,@rpath/libfoo.dylib"]

    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_explicit_install_name(self, mock_platform) -> None:
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "/usr/local/lib/libfoo.2.dylib")
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        assert flags == ["-Wl,-install_name,/usr/local/lib/libfoo.2.dylib"]

    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_disabled_install_name(self, mock_platform) -> None:
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "")
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        assert flags == []

    @patch("pcons.toolchains.unix.get_platform")
    def test_linux_default_soname(self, mock_platform) -> None:
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        tc = GccToolchain()
        target = _make_shared_target()
        flags = tc.get_link_flags_for_target(target, "libfoo.so", [])
        assert flags == ["-Wl,-soname,libfoo.so"]

    @patch("pcons.toolchains.unix.get_platform")
    def test_linux_explicit_soname(self, mock_platform) -> None:
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "libfoo.so.2")
        flags = tc.get_link_flags_for_target(target, "libfoo.so", [])
        assert flags == ["-Wl,-soname,libfoo.so.2"]

    @patch("pcons.toolchains.unix.get_platform")
    def test_linux_disabled_soname(self, mock_platform) -> None:
        mock_platform.return_value.is_macos = False
        mock_platform.return_value.is_linux = True
        tc = GccToolchain()
        target = _make_shared_target()
        target.set_option("install_name", "")
        flags = tc.get_link_flags_for_target(target, "libfoo.so", [])
        assert flags == []

    def test_program_gets_no_flags(self) -> None:
        tc = GccToolchain()
        target = Target("app", target_type=TargetType.PROGRAM)
        flags = tc.get_link_flags_for_target(target, "app", [])
        assert flags == []

    def test_static_library_gets_no_flags(self) -> None:
        tc = GccToolchain()
        target = Target("lib", target_type=TargetType.STATIC_LIBRARY)
        flags = tc.get_link_flags_for_target(target, "libfoo.a", [])
        assert flags == []


class TestLlvmInstallName:
    @patch("pcons.toolchains.unix.get_platform")
    def test_macos_default(self, mock_platform) -> None:
        mock_platform.return_value.is_macos = True
        mock_platform.return_value.is_linux = False
        tc = LlvmToolchain()
        target = _make_shared_target()
        flags = tc.get_link_flags_for_target(target, "libfoo.dylib", [])
        assert flags == ["-Wl,-install_name,@rpath/libfoo.dylib"]
