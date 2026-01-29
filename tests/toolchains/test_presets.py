# SPDX-License-Identifier: MIT
"""Tests for semantic presets (warnings, sanitize, profile, lto, hardened).

Each toolchain family defines its own flags for each preset.
"""

from __future__ import annotations

from pcons.core.environment import Environment
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain


def _make_unix_env() -> Environment:
    """Create an environment with cc, cxx, and link tools."""
    env = Environment()
    cc = env.add_tool("cc")
    cc.set("cmd", "gcc")
    cc.set("flags", [])
    cc.set("defines", [])

    cxx = env.add_tool("cxx")
    cxx.set("cmd", "g++")
    cxx.set("flags", [])
    cxx.set("defines", [])

    link = env.add_tool("link")
    link.set("cmd", "gcc")
    link.set("flags", [])
    return env


def _make_msvc_env() -> Environment:
    """Create an environment with MSVC-style tools."""
    env = Environment()
    cc = env.add_tool("cc")
    cc.set("cmd", "cl.exe")
    cc.set("flags", [])
    cc.set("defines", [])

    cxx = env.add_tool("cxx")
    cxx.set("cmd", "cl.exe")
    cxx.set("flags", [])
    cxx.set("defines", [])

    link = env.add_tool("link")
    link.set("cmd", "link.exe")
    link.set("flags", [])
    return env


class TestUnixPresets:
    """Tests for Unix (GCC/LLVM) preset application."""

    def test_warnings_preset(self) -> None:
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "warnings")

        assert "-Wall" in env.cc.flags
        assert "-Wextra" in env.cc.flags
        assert "-Wpedantic" in env.cc.flags
        assert "-Werror" in env.cc.flags
        assert "-Wall" in env.cxx.flags

    def test_sanitize_preset(self) -> None:
        env = _make_unix_env()
        toolchain = LlvmToolchain()
        toolchain.apply_preset(env, "sanitize")

        assert "-fsanitize=address,undefined" in env.cc.flags
        assert "-fno-omit-frame-pointer" in env.cc.flags
        assert "-fsanitize=address,undefined" in env.cxx.flags
        # Link flags too
        assert "-fsanitize=address,undefined" in env.link.flags

    def test_profile_preset(self) -> None:
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "profile")

        assert "-pg" in env.cc.flags
        assert "-g" in env.cc.flags
        assert "-pg" in env.link.flags

    def test_lto_preset(self) -> None:
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "lto")

        assert "-flto" in env.cc.flags
        assert "-flto" in env.cxx.flags
        assert "-flto" in env.link.flags

    def test_hardened_preset(self) -> None:
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "hardened")

        assert "-fstack-protector-strong" in env.cc.flags
        assert "-D_FORTIFY_SOURCE=2" in env.cc.flags
        assert "-fPIE" in env.cc.flags
        assert "-pie" in env.link.flags
        assert "-Wl,-z,relro,-z,now" in env.link.flags

    def test_unknown_preset_warns(self) -> None:
        """Unknown preset should log a warning but not raise."""
        env = _make_unix_env()
        toolchain = GccToolchain()
        # Should not raise
        toolchain.apply_preset(env, "nonexistent")
        # No flags should be added
        assert len(env.cc.flags) == 0

    def test_multiple_presets_combine(self) -> None:
        """Applying multiple presets should combine flags."""
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "warnings")
        toolchain.apply_preset(env, "sanitize")

        assert "-Wall" in env.cc.flags
        assert "-fsanitize=address,undefined" in env.cc.flags

    def test_preset_without_link_tool(self) -> None:
        """Presets should work even without a link tool."""
        env = Environment()
        cc = env.add_tool("cc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_preset(env, "sanitize")

        assert "-fsanitize=address,undefined" in env.cc.flags

    def test_preset_via_env_apply_preset(self) -> None:
        """Test the Environment.apply_preset() delegate method."""
        env = _make_unix_env()
        env._toolchain = GccToolchain()

        env.apply_preset("warnings")

        assert "-Wall" in env.cc.flags
        assert "-Werror" in env.cc.flags


class TestMsvcPresets:
    """Tests for MSVC-compatible preset application."""

    def test_warnings_preset(self) -> None:
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        # MsvcCompatibleToolchain is abstract, so use a concrete subclass
        # or just call apply_preset directly on an instance
        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "warnings")

        assert "/W4" in env.cc.flags
        assert "/WX" in env.cc.flags
        assert "/W4" in env.cxx.flags

    def test_sanitize_preset(self) -> None:
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "sanitize")

        assert "/fsanitize=address" in env.cc.flags

    def test_lto_preset(self) -> None:
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "lto")

        assert "/GL" in env.cc.flags
        assert "/LTCG" in env.link.flags

    def test_hardened_preset(self) -> None:
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "hardened")

        assert "/GS" in env.cc.flags
        assert "/guard:cf" in env.cc.flags
        assert "/DYNAMICBASE" in env.link.flags
        assert "/NXCOMPAT" in env.link.flags

    def test_profile_preset(self) -> None:
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "profile")

        # MSVC profile is linker-only
        assert "/PROFILE" in env.link.flags

    def test_unknown_preset_warns(self) -> None:
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "nonexistent")

        assert len(env.cc.flags) == 0
