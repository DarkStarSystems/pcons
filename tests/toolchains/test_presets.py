# SPDX-License-Identifier: MIT
"""Tests for semantic presets (warnings, sanitize, profile, lto, hardened).

Each toolchain family defines its own flags for each preset.
"""

from __future__ import annotations

import pytest

from pcons.core.environment import Environment
from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain
from pcons.toolchains.gcc import GccToolchain
from pcons.toolchains.llvm import LlvmToolchain


def _concrete_msvc() -> MsvcCompatibleToolchain:
    class ConcreteMsvc(MsvcCompatibleToolchain):
        def _configure_tools(self, config: object) -> bool:
            return True

    return ConcreteMsvc("test-msvc")


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

    def test_warnings_preset(self, test_project):  # noqa: F811
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "warnings")

        assert "-Wall" in env.cc.flags
        assert "-Wextra" in env.cc.flags
        assert "-Wpedantic" in env.cc.flags
        assert "-Wall" in env.cxx.flags
        # warnings no longer forces -Werror; that's the separate `werror` preset.
        assert "-Werror" not in env.cc.flags

    def test_werror_preset(self, test_project):  # noqa: F811
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "werror")

        assert "-Werror" in env.cc.flags
        assert "-Werror" in env.cxx.flags
        # orthogonal: werror adds only -Werror, not the warning set
        assert "-Wall" not in env.cc.flags

    def test_sanitize_preset(self, test_project):  # noqa: F811
        env = _make_unix_env()
        toolchain = LlvmToolchain()
        toolchain.apply_preset(env, "sanitize")

        assert "-fsanitize=address,undefined" in env.cc.flags
        assert "-fno-omit-frame-pointer" in env.cc.flags
        assert "-fsanitize=address,undefined" in env.cxx.flags
        # Link flags too
        assert "-fsanitize=address,undefined" in env.link.flags

    def test_profile_preset(self, test_project):  # noqa: F811
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "profile")

        assert "-pg" in env.cc.flags
        assert "-g" in env.cc.flags
        assert "-pg" in env.link.flags

    def test_lto_preset(self, test_project):  # noqa: F811
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "lto")

        assert "-flto" in env.cc.flags
        assert "-flto" in env.cxx.flags
        assert "-flto" in env.link.flags

    def test_hardened_preset(self, test_project):  # noqa: F811
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "hardened")

        assert "-fstack-protector-strong" in env.cc.flags
        assert "-D_FORTIFY_SOURCE=2" in env.cc.flags
        assert "-fPIE" in env.cc.flags
        assert "-pie" in env.link.flags
        assert "-Wl,-z,relro,-z,now" in env.link.flags

    def test_unknown_preset_warns(self, test_project):  # noqa: F811
        """Unknown preset should log a warning but not raise."""
        env = _make_unix_env()
        toolchain = GccToolchain()
        # Should not raise
        toolchain.apply_preset(env, "nonexistent")
        # No flags should be added
        assert len(env.cc.flags) == 0

    def test_multiple_presets_combine(self, test_project):  # noqa: F811
        """Applying multiple presets should combine flags."""
        env = _make_unix_env()
        toolchain = GccToolchain()
        toolchain.apply_preset(env, "warnings")
        toolchain.apply_preset(env, "sanitize")

        assert "-Wall" in env.cc.flags
        assert "-fsanitize=address,undefined" in env.cc.flags

    def test_preset_without_link_tool(self, test_project):  # noqa: F811
        """Presets should work even without a link tool."""
        env = Environment()
        cc = env.add_tool("cc")
        cc.set("flags", [])
        cc.set("defines", [])

        toolchain = GccToolchain()
        toolchain.apply_preset(env, "sanitize")

        assert "-fsanitize=address,undefined" in env.cc.flags

    def test_preset_via_env_apply_preset(self, test_project):  # noqa: F811
        """Test the Environment.apply_preset() delegate method."""
        env = _make_unix_env()
        env._toolchain = GccToolchain()

        env.apply_preset("warnings")

        assert "-Wall" in env.cc.flags
        assert "-Wextra" in env.cc.flags


class TestMsvcPresets:
    """Tests for MSVC-compatible preset application."""

    def test_warnings_preset(self, test_project):  # noqa: F811
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
        assert "/W4" in env.cxx.flags
        # /WX (warnings-as-errors) is the separate `werror` preset now.
        assert "/WX" not in env.cc.flags

    def test_werror_preset(self, test_project):  # noqa: F811
        env = _make_msvc_env()
        toolchain = _concrete_msvc()
        toolchain.apply_preset(env, "werror")

        assert "/WX" in env.cc.flags
        assert "/WX" in env.cxx.flags
        assert "/W4" not in env.cc.flags

    def test_sanitize_preset(self, test_project):  # noqa: F811
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "sanitize")

        assert "/fsanitize=address" in env.cc.flags

    def test_lto_preset(self, test_project):  # noqa: F811
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "lto")

        assert "/GL" in env.cc.flags
        assert "/LTCG" in env.link.flags

    def test_hardened_preset(self, test_project):  # noqa: F811
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

    def test_profile_preset(self, test_project):  # noqa: F811
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "profile")

        # MSVC profile is linker-only
        assert "/PROFILE" in env.link.flags

    def test_unknown_preset_warns(self, test_project):  # noqa: F811
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = _make_msvc_env()

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        toolchain.apply_preset(env, "nonexistent")

        assert len(env.cc.flags) == 0


class TestCxxStandard:
    """Tests for env.set_cxx_standard() across toolchains."""

    def test_gcc_sets_std_on_cxx_only(self, test_project):  # noqa: F811
        env = _make_unix_env()
        env._toolchain = GccToolchain()
        env.set_cxx_standard("c++20")
        assert "-std=c++20" in env.cxx.flags
        assert "-std=c++20" not in env.cc.flags  # C++ standard, not C

    def test_msvc_concrete_standard(self, test_project):  # noqa: F811
        env = _make_msvc_env()
        env._toolchain = _concrete_msvc()
        env.set_cxx_standard(20)
        assert "/std:c++20" in env.cxx.flags

    def test_msvc_maps_above_20_to_latest(self, test_project):  # noqa: F811
        # MSVC has no /std:c++23 switch, so c++23/c++26 -> /std:c++latest.
        env = _make_msvc_env()
        env._toolchain = _concrete_msvc()
        env.set_cxx_standard("c++23")
        assert "/std:c++latest" in env.cxx.flags

    def test_accepts_int_str_and_prefixed(self, test_project):  # noqa: F811
        for value in (20, "20", "c++20"):
            env = _make_unix_env()
            env._toolchain = GccToolchain()
            env.set_cxx_standard(value)
            assert "-std=c++20" in env.cxx.flags

    def test_invalid_standard_raises(self, test_project):  # noqa: F811
        env = _make_unix_env()
        env._toolchain = GccToolchain()
        with pytest.raises(ValueError, match="Unsupported C\\+\\+ standard"):
            env.set_cxx_standard("c++19")
        with pytest.raises(ValueError, match="Invalid C\\+\\+ standard"):
            env.set_cxx_standard("bogus")

    def test_explain_attributes_to_language(self, test_project):  # noqa: F811
        env = _make_unix_env()
        env._toolchain = GccToolchain()
        env.set_cxx_standard("c++20")
        rows = [r for r in env.cxx.explain().rows if r.token == "-std=c++20"]
        assert rows and rows[0].source == "c++20" and rows[0].category == "language"


def _make_fortran_env() -> Environment:
    """Environment with a Fortran compiler tool (fc) and a linker."""
    env = Environment()
    fc = env.add_tool("fc")
    fc.set("cmd", "gfortran")
    fc.set("flags", [])
    fc.set("defines", [])
    link = env.add_tool("link")
    link.set("cmd", "gfortran")
    link.set("flags", [])
    return env


class TestFortranPresets:
    """Feature presets realize on the Fortran compiler (fc), not cc/cxx."""

    def test_warnings_targets_fc(self, test_project):  # noqa: F811
        from pcons.toolchains.gfortran import GfortranToolchain

        env = _make_fortran_env()
        GfortranToolchain().apply_preset(env, "warnings")

        assert "-Wall" in env.fc.flags
        assert "-Wextra" in env.fc.flags
        assert "-Werror" not in env.fc.flags

    def test_werror_targets_fc(self, test_project):  # noqa: F811
        from pcons.toolchains.gfortran import GfortranToolchain

        env = _make_fortran_env()
        GfortranToolchain().apply_preset(env, "werror")

        assert "-Werror" in env.fc.flags


class TestWasmPresets:
    """WASM toolchains are clang-based and inherit the C/C++ realizations."""

    def test_emscripten_inherits_warnings(self, test_project):  # noqa: F811
        from pcons.toolchains.emscripten import EmscriptenToolchain

        env = _make_unix_env()
        EmscriptenToolchain().apply_preset(env, "warnings")

        assert "-Wall" in env.cc.flags
        assert "-Werror" not in env.cc.flags

    def test_emscripten_werror(self, test_project):  # noqa: F811
        from pcons.toolchains.emscripten import EmscriptenToolchain

        env = _make_unix_env()
        EmscriptenToolchain().apply_preset(env, "werror")

        assert "-Werror" in env.cc.flags
