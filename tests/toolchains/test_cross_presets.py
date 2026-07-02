# SPDX-License-Identifier: MIT
"""Tests for cross-compilation presets.

Tests the CrossPreset dataclass, factory functions, and toolchain
application of cross-compilation settings.
"""

from __future__ import annotations

import pytest

from pcons.core.environment import Environment
from pcons.toolchains.presets import (
    CrossPreset,
    android,
    emscripten,
    ios,
    linux_cross,
    pyodide,
)


def _make_unix_env() -> Environment:
    """Create an environment with cc, cxx, and link tools."""
    env = Environment()
    cc = env.add_tool("cc")
    cc.set("cmd", "clang")
    cc.set("flags", [])
    cc.set("defines", [])

    cxx = env.add_tool("cxx")
    cxx.set("cmd", "clang++")
    cxx.set("flags", [])
    cxx.set("defines", [])

    link = env.add_tool("link")
    link.set("cmd", "clang")
    link.set("flags", [])
    return env


class TestCrossPresetDataclass:
    """Tests for the CrossPreset dataclass."""

    def test_basic_creation(self) -> None:
        preset = CrossPreset(name="test", arch="arm64")
        assert preset.name == "test"
        assert preset.arch == "arm64"
        assert preset.triple is None
        assert preset.sysroot is None

    def test_full_creation(self) -> None:
        preset = CrossPreset(
            name="android-arm64",
            arch="arm64",
            triple="aarch64-linux-android21",
            sysroot="/path/to/sysroot",
            sdk_path="/path/to/sdk",
            extra_compile_flags=("-DANDROID",),
            extra_link_flags=("-llog",),
            env_vars={"CC": "clang"},
        )
        assert preset.triple == "aarch64-linux-android21"
        assert preset.sysroot == "/path/to/sysroot"
        assert "-DANDROID" in preset.extra_compile_flags
        assert "-llog" in preset.extra_link_flags

    def test_frozen(self) -> None:
        """CrossPreset should be immutable."""
        preset = CrossPreset(name="test", arch="arm64")
        with pytest.raises(AttributeError):
            preset.name = "modified"  # type: ignore[misc]


class TestAndroidPreset:
    """Tests for the android() factory function."""

    def test_default_arch(self) -> None:
        preset = android(ndk="/fake/ndk")
        assert preset.name == "android-arm64-v8a"
        assert preset.arch == "arm64-v8a"
        assert "aarch64-linux-android21" in (preset.triple or "")

    def test_custom_arch(self) -> None:
        preset = android(ndk="/fake/ndk", arch="x86_64")
        assert preset.name == "android-x86_64"
        assert "x86_64-linux-android21" in (preset.triple or "")

    def test_custom_api(self) -> None:
        preset = android(ndk="/fake/ndk", api=30)
        assert "android30" in (preset.triple or "")

    def test_unknown_arch_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown Android architecture"):
            android(ndk="/fake/ndk", arch="mips")

    def test_env_vars_set(self) -> None:
        preset = android(ndk="/fake/ndk")
        assert "CC" in preset.env_vars
        assert "CXX" in preset.env_vars
        assert "clang" in preset.env_vars["CC"]
        assert "clang++" in preset.env_vars["CXX"]

    def test_sysroot_set(self) -> None:
        preset = android(ndk="/fake/ndk")
        assert preset.sysroot is not None
        assert "sysroot" in preset.sysroot


class TestIosPreset:
    """Tests for the ios() factory function."""

    def test_default_arm64(self) -> None:
        preset = ios()
        assert preset.name == "ios-arm64"
        assert preset.arch == "arm64"
        assert "arm64-apple-ios" in (preset.triple or "")

    def test_simulator(self) -> None:
        preset = ios(arch="x86_64")
        assert "simulator" in (preset.triple or "")

    def test_min_version(self) -> None:
        preset = ios(min_version="16.0")
        assert "16.0" in (preset.triple or "")

    def test_custom_sdk(self) -> None:
        preset = ios(sdk="/path/to/sdk")
        assert preset.sysroot == "/path/to/sdk"


class TestEmscriptenPreset:
    """Tests for the emscripten() factory function."""

    def test_default(self) -> None:
        preset = emscripten()
        assert preset.name == "wasm32-emscripten"
        assert preset.arch == "wasm32"
        assert preset.triple == "wasm32-unknown-emscripten"
        assert preset.env_vars["CC"] == "emcc"
        assert preset.env_vars["CXX"] == "em++"

    def test_custom_emsdk(self) -> None:
        preset = emscripten(emsdk="/fake/emsdk")
        assert "emcc" in preset.env_vars["CC"]
        assert "em++" in preset.env_vars["CXX"]


class TestPyodidePreset:
    """Tests for the pyodide() / PEP 783 PyEmscripten factory function."""

    def test_default_abi(self) -> None:
        preset = pyodide()
        assert preset.name == "pyemscripten_2026_0"
        assert preset.arch == "wasm32"
        assert preset.triple == "wasm32-unknown-emscripten"
        # Builds on emscripten() — keeps the emcc/em++ commands.
        assert preset.env_vars["CC"] == "emcc"
        assert preset.env_vars["CXX"] == "em++"

    def test_side_module_flags(self) -> None:
        preset = pyodide()
        assert "-fPIC" in preset.extra_compile_flags
        assert "-sSIDE_MODULE=1" in preset.extra_link_flags

    def test_explicit_abi(self) -> None:
        assert pyodide(abi="2025_0").name == "pyemscripten_2025_0"

    def test_unknown_abi_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown PyEmscripten ABI"):
            pyodide(abi="9999_0")

    def test_applied_to_emscripten_toolchain(self, test_project):  # noqa: F811
        """Applying pyodide() adds the side-module flags via the wasm toolchain."""
        from pcons.toolchains.emscripten import EmscriptenToolchain

        env = _make_unix_env()
        toolchain = EmscriptenToolchain()
        toolchain.apply_cross_preset(env, pyodide())

        assert "-fPIC" in env.cc.flags
        assert "-fPIC" in env.cxx.flags
        assert "-sSIDE_MODULE=1" in env.link.flags


class TestLinuxCrossPreset:
    """Tests for the linux_cross() factory function."""

    def test_aarch64(self) -> None:
        preset = linux_cross(triple="aarch64-linux-gnu")
        assert preset.name == "linux-aarch64"
        assert preset.arch == "aarch64"
        assert preset.triple == "aarch64-linux-gnu"

    def test_arm_with_sysroot(self) -> None:
        preset = linux_cross(
            triple="arm-linux-gnueabihf",
            sysroot="/opt/sysroot",
        )
        assert preset.sysroot == "/opt/sysroot"
        assert preset.arch == "arm"


class TestCrossPresetApplication:
    """Tests for applying cross-presets to environments via toolchains."""

    def test_unix_apply_triple(self, test_project):  # noqa: F811
        """UnixToolchain should apply --target flag."""
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        toolchain = LlvmToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            triple="aarch64-linux-gnu",
        )
        toolchain.apply_cross_preset(env, preset)

        assert "--target=aarch64-linux-gnu" in env.cc.flags
        assert "--target=aarch64-linux-gnu" in env.cxx.flags

    def test_gcc_apply_triple_no_target_flag(self, test_project):  # noqa: F811
        """GCC rejects --target=; the cross preset must not add it for gcc."""
        from pcons.toolchains.gcc import GccToolchain

        env = _make_unix_env()
        toolchain = GccToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            triple="aarch64-linux-gnu",
        )
        toolchain.apply_cross_preset(env, preset)

        assert not any("--target=" in str(f) for f in env.cc.flags)
        assert not any("--target=" in str(f) for f in env.cxx.flags)

    def test_unix_apply_sysroot(self, test_project):  # noqa: F811
        """UnixToolchain should apply --sysroot flag."""
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        toolchain = LlvmToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            sysroot="/opt/sysroot",
        )
        toolchain.apply_cross_preset(env, preset)

        assert "--sysroot=/opt/sysroot" in env.cc.flags
        assert "--sysroot=/opt/sysroot" in env.link.flags

    def test_unix_apply_extra_flags(self, test_project):  # noqa: F811
        """Extra compile/link flags should be applied."""
        from pcons.toolchains.gcc import GccToolchain

        env = _make_unix_env()
        toolchain = GccToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            extra_compile_flags=("-DCUSTOM",),
            extra_link_flags=("-lcustom",),
        )
        toolchain.apply_cross_preset(env, preset)

        assert "-DCUSTOM" in env.cc.flags
        assert "-lcustom" in env.link.flags

    def test_unix_apply_env_vars(self, test_project):  # noqa: F811
        """CC/CXX overrides from env_vars should be applied."""
        from pcons.toolchains.gcc import GccToolchain

        env = _make_unix_env()
        toolchain = GccToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            env_vars={"CC": "/usr/bin/custom-gcc", "CXX": "/usr/bin/custom-g++"},
        )
        toolchain.apply_cross_preset(env, preset)

        assert env.cc.cmd == "/usr/bin/custom-gcc"
        assert env.cxx.cmd == "/usr/bin/custom-g++"

    def test_msvc_apply_machine(self, test_project):  # noqa: F811
        """MsvcCompatibleToolchain should apply /MACHINE flags."""
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        env = Environment()
        link = env.add_tool("link")
        link.set("cmd", "link.exe")
        link.set("flags", [])

        lib = env.add_tool("lib")
        lib.set("cmd", "lib.exe")
        lib.set("flags", [])

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        toolchain = ConcreteMsvc("test-msvc")
        preset = CrossPreset(name="test", arch="arm64")
        toolchain.apply_cross_preset(env, preset)

        assert "/MACHINE:ARM64" in env.link.flags

    def _make_msvc_env(self) -> Environment:
        env = Environment()
        for name in ("cc", "cxx", "link", "lib"):
            tool = env.add_tool(name)
            tool.set("cmd", f"{name}.exe")
            tool.set("flags", [])
            tool.set("defines", [])
        return env

    def _concrete_msvc(self):
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain

        class ConcreteMsvc(MsvcCompatibleToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

        return ConcreteMsvc("test-msvc")

    def test_msvc_apply_extra_flags(self, test_project):  # noqa: F811
        """MsvcCompatibleToolchain applies extra compile/link flags."""
        env = self._make_msvc_env()
        toolchain = self._concrete_msvc()

        preset = CrossPreset(
            name="test",
            arch="x64",
            extra_compile_flags=("/DCUSTOM",),
            extra_link_flags=("/LIBPATH:custom",),
        )
        toolchain.apply_cross_preset(env, preset)

        assert "/DCUSTOM" in env.cc.flags
        assert "/DCUSTOM" in env.cxx.flags
        assert "/LIBPATH:custom" in env.link.flags

    def test_msvc_apply_variant(self, test_project):  # noqa: F811
        """MsvcCompatibleToolchain.apply_variant adds flags and defines."""
        env = self._make_msvc_env()
        toolchain = self._concrete_msvc()

        toolchain.apply_variant(env, "debug")

        assert "/Od" in env.cc.flags
        assert "/Zi" in env.cxx.flags
        assert "DEBUG" in env.cc.defines
        assert "_DEBUG" in env.cxx.defines

    def test_wasm_apply_cross_preset(self, test_project):  # noqa: F811
        """WasmToolchain applies extra flags without sysroot handling."""
        from pcons.toolchains.emscripten import EmscriptenToolchain

        env = _make_unix_env()
        toolchain = EmscriptenToolchain()

        preset = CrossPreset(
            name="test",
            arch="wasm32",
            extra_compile_flags=("-DWASM",),
            extra_link_flags=("-sUSE_PTHREADS",),
        )
        toolchain.apply_cross_preset(env, preset)

        assert "-DWASM" in env.cc.flags
        assert "-DWASM" in env.cxx.flags
        assert "-sUSE_PTHREADS" in env.link.flags

    def test_wasm_apply_target_arch_forces_wasm32(self, test_project):  # noqa: F811
        """WasmToolchain ignores the requested arch and uses wasm32."""
        from pcons.toolchains.emscripten import EmscriptenToolchain

        env = _make_unix_env()
        toolchain = EmscriptenToolchain()
        # Any requested arch is accepted but treated as wasm32 (no error).
        toolchain.apply_target_arch(env, "x86_64")

    def test_env_apply_cross_preset_delegates(self, test_project):  # noqa: F811
        """Environment.apply_cross_preset() should delegate to toolchains."""
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            triple="aarch64-linux-gnu",
        )
        env.apply_cross_preset(preset)

        assert "--target=aarch64-linux-gnu" in env.cc.flags
