# SPDX-License-Identifier: MIT
"""Tests for cross-compilation presets.

Tests the CrossPreset dataclass, factory functions, and toolchain
application of cross-compilation settings.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pcons.core.environment import Environment
from pcons.toolchains.presets import (
    CrossPreset,
    android,
    emscripten,
    ios,
    linux_cross,
    pyodide,
    target_platform_for_triple,
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
        preset = android(ndk="/fake/ndk", api=21)
        assert preset.name == "android-arm64-v8a"
        assert preset.arch == "arm64-v8a"
        assert "aarch64-linux-android21" in (preset.triple or "")

    def test_custom_arch(self) -> None:
        preset = android(ndk="/fake/ndk", arch="x86_64", api=21)
        assert preset.name == "android-x86_64"
        assert "x86_64-linux-android21" in (preset.triple or "")

    def test_custom_api(self) -> None:
        preset = android(ndk="/fake/ndk", api=30)
        assert "android30" in (preset.triple or "")

    def test_the_api_level_has_no_default(self) -> None:
        """A minimum Android release is a product decision, not a guess.

        Whatever number a build system picked would be wrong within a
        year, and it silently decides which NDK headers and which symbols
        the build sees.
        """
        with pytest.raises(TypeError, match="api"):
            android(ndk="/fake/ndk")  # type: ignore[call-arg]

    def test_the_api_level_is_keyword_only(self) -> None:
        with pytest.raises(TypeError):
            android("/fake/ndk", "arm64-v8a", 35)  # type: ignore[misc]

    def test_unknown_arch_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown Android architecture"):
            android(ndk="/fake/ndk", arch="mips", api=21)

    def test_tool_cmds_set(self) -> None:
        preset = android(ndk="/fake/ndk", api=21)
        cmds = preset.resolved_tool_cmds()
        assert "clang" in cmds["cc"]
        assert "clang++" in cmds["cxx"]
        assert "clang++" in cmds["link"]
        assert "llvm-ar" in cmds["ar"]

    def test_sysroot_set(self) -> None:
        preset = android(ndk="/fake/ndk", api=21)
        assert preset.sysroot is not None
        assert "sysroot" in preset.sysroot


class TestAndroidStl:
    """Which C++ runtime an Android artifact links.

    Flags measured against NDK r28c rather than cited: the NDK links
    ``libc++_shared.so`` on its own, so the shared default contributes
    nothing and only the other two choices add a flag.
    """

    def test_the_default_adds_no_flag(self) -> None:
        preset = android(ndk="/fake/ndk", api=21)
        assert preset.extra_link_flags == ()

    def test_shared_adds_no_flag(self) -> None:
        preset = android(ndk="/fake/ndk", api=21, stl="c++_shared")
        assert preset.extra_link_flags == ()

    def test_static_links_a_private_copy(self) -> None:
        preset = android(ndk="/fake/ndk", api=21, stl="c++_static")
        assert preset.extra_link_flags == ("-static-libstdc++",)

    def test_none_links_no_runtime(self) -> None:
        preset = android(ndk="/fake/ndk", api=21, stl="none")
        assert preset.extra_link_flags == ("-nostdlib++",)

    @pytest.mark.parametrize("stl", ["c++_shared", "c++_static", "none"])
    def test_no_choice_names_the_standard_library(self, stl: str) -> None:
        """-stdlib=libc++ is what the NDK already does; naming it is noise."""
        preset = android(ndk="/fake/ndk", api=21, stl=stl)  # type: ignore[arg-type]
        assert "-stdlib=libc++" not in preset.extra_link_flags

    def test_unknown_stl_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown Android STL"):
            android(ndk="/fake/ndk", api=21, stl="libstdc++")  # type: ignore[arg-type]

    def test_the_stl_is_keyword_only(self) -> None:
        with pytest.raises(TypeError):
            android("/fake/ndk", "arm64-v8a", "c++_static")  # type: ignore[misc]


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
        assert preset.tool_cmds["cc"] == "emcc"
        assert preset.tool_cmds["cxx"] == "em++"

    def test_custom_emsdk(self) -> None:
        preset = emscripten(emsdk="/fake/emsdk")
        assert "emcc" in preset.tool_cmds["cc"]
        assert "em++" in preset.tool_cmds["cxx"]


class TestPyodidePreset:
    """Tests for the pyodide() / PEP 783 PyEmscripten factory function."""

    def test_default_abi(self) -> None:
        preset = pyodide()
        assert preset.name == "pyemscripten_2026_0"
        assert preset.arch == "wasm32"
        assert preset.triple == "wasm32-unknown-emscripten"
        # Builds on emscripten() — keeps the emcc/em++ commands.
        assert preset.tool_cmds["cc"] == "emcc"
        assert preset.tool_cmds["cxx"] == "em++"

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

    def test_gcc_rejects_triple_only_preset(self, test_project):  # noqa: F811
        """GCC can't retarget by flag; a triple with no CC/CXX must fail fast."""
        from pcons.toolchains.gcc import GccToolchain

        env = _make_unix_env()
        toolchain = GccToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            triple="aarch64-linux-gnu",
        )
        with pytest.raises(ValueError, match="selects targets by binary"):
            toolchain.apply_cross_preset(env, preset)

    def test_gcc_accepts_triple_with_cross_binaries(self, test_project):  # noqa: F811
        """A triple plus CC/CXX overrides is binary-retargeted; no --target."""
        from pcons.toolchains.gcc import GccToolchain

        env = _make_unix_env()
        toolchain = GccToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            triple="aarch64-linux-gnu",
            env_vars={"CC": "aarch64-linux-gnu-gcc", "CXX": "aarch64-linux-gnu-g++"},
        )
        toolchain.apply_cross_preset(env, preset)

        assert env.cc.cmd == "aarch64-linux-gnu-gcc"
        assert not any("--target=" in str(f) for f in env.cc.flags)
        assert not any("--target=" in str(f) for f in env.cxx.flags)

    def test_cross_preset_arch_is_metadata_only(self, test_project):  # noqa: F811
        """CrossPreset.arch never becomes a flag on any host; the triple
        encodes the CPU (ecosystem arch names like arm64-v8a aren't flag
        vocabulary)."""
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        preset = CrossPreset(
            name="test", arch="arm64-v8a", triple="aarch64-linux-android21"
        )
        LlvmToolchain().apply_cross_preset(env, preset)

        for tool in (env.cc, env.cxx, env.link):
            assert "-arch" not in tool.flags
            assert "arm64-v8a" not in tool.flags

    def test_unix_apply_triple_on_link(self, test_project):  # noqa: F811
        """Clang drives the link too, so the triple goes on the link command."""
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        toolchain = LlvmToolchain()

        preset = CrossPreset(
            name="test",
            arch="arm64",
            triple="aarch64-linux-gnu",
        )
        toolchain.apply_cross_preset(env, preset)

        assert "--target=aarch64-linux-gnu" in env.link.flags

    def test_unix_ios_resolves_apple_sdk(
        self,
        test_project,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An Apple triple with no sysroot resolves the SDK via xcrun."""
        from pcons.toolchains import unix
        from pcons.toolchains.llvm import LlvmToolchain

        monkeypatch.setattr(
            unix, "apple_sdk_for_triple", lambda triple: "/fake/iPhoneOS.sdk"
        )
        env = _make_unix_env()
        toolchain = LlvmToolchain()

        toolchain.apply_cross_preset(env, ios(arch="arm64"))

        for tool in (env.cc, env.cxx, env.link):
            flags = list(tool.flags)
            idx = flags.index("-isysroot")
            assert flags[idx + 1] == "/fake/iPhoneOS.sdk"
        assert "--target=arm64-apple-ios15.0" in env.cc.flags

    def test_unix_ios_explicit_sdk_skips_xcrun(
        self,
        test_project,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An explicit sdk= becomes the sysroot; xcrun is never consulted."""
        from pcons.toolchains import unix
        from pcons.toolchains.llvm import LlvmToolchain

        def _fail(triple: str) -> str:
            raise AssertionError("xcrun resolution should not run")

        monkeypatch.setattr(unix, "apple_sdk_for_triple", _fail)
        env = _make_unix_env()
        toolchain = LlvmToolchain()

        toolchain.apply_cross_preset(env, ios(arch="arm64", sdk="/opt/ios-sdk"))

        assert "--sysroot=/opt/ios-sdk" in env.cc.flags
        assert "-isysroot" not in env.cc.flags

    def test_unix_non_apple_triple_no_sdk(self, test_project):  # noqa: F811
        """Non-Apple triples get no -isysroot even without a sysroot."""
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        toolchain = LlvmToolchain()

        preset = CrossPreset(name="test", arch="arm64", triple="aarch64-linux-gnu")
        toolchain.apply_cross_preset(env, preset)

        assert "-isysroot" not in env.cc.flags

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

    def test_msvc_rejects_cross_preset(self, test_project):  # noqa: F811
        """MSVC has no different-platform targets; cross presets fail fast."""
        env = self._make_msvc_env()
        toolchain = self._concrete_msvc()

        preset = CrossPreset(name="test", arch="arm64")
        with pytest.raises(ValueError, match="set_target_arch"):
            toolchain.apply_cross_preset(env, preset)

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

    def test_wasm_target_arch_wasm32_only(self, test_project):  # noqa: F811
        """WasmToolchain accepts wasm32 (a declared no-op realization) and
        rejects any other arch rather than silently coercing."""
        from pcons.toolchains.emscripten import EmscriptenToolchain

        env = _make_unix_env()
        toolchain = EmscriptenToolchain()

        assert toolchain.apply_target_arch(env, "wasm32") is True
        assert env.target_arch == "wasm32"

        with pytest.raises(ValueError, match="wasm32 only"):
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


class TestBinaryRetarget:
    """tool_cmds is the binary-retarget mechanism; env_vars is a deprecated
    alias (docs/presets.md, cross-target contract)."""

    def test_tool_cmds_repoints_all_named_tools(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        ar = env.add_tool("ar")
        ar.set("cmd", "ar")
        ar.set("flags", [])

        preset = CrossPreset(
            name="test",
            arch="aarch64",
            triple="aarch64-linux-gnu",
            tool_cmds={
                "cc": "/x/cc",
                "cxx": "/x/cxx",
                "link": "/x/link",
                "ar": "/x/ar",
            },
        )
        LlvmToolchain().apply_cross_preset(env, preset)

        assert env.cc.cmd == "/x/cc"
        assert env.cxx.cmd == "/x/cxx"
        assert env.link.cmd == "/x/link"
        assert env.ar.cmd == "/x/ar"

    def test_env_vars_alias_still_works(self, test_project):  # noqa: F811
        from pcons.toolchains.gcc import GccToolchain

        env = _make_unix_env()
        preset = CrossPreset(
            name="legacy",
            arch="aarch64",
            triple="aarch64-linux-gnu",
            env_vars={"CC": "aarch64-linux-gnu-gcc", "CXX": "aarch64-linux-gnu-g++"},
        )
        GccToolchain().apply_cross_preset(env, preset)

        assert env.cc.cmd == "aarch64-linux-gnu-gcc"
        assert env.cxx.cmd == "aarch64-linux-gnu-g++"

    def test_tool_cmds_wins_over_env_vars(self) -> None:
        preset = CrossPreset(
            name="both",
            arch="x",
            tool_cmds={"cc": "new-cc"},
            env_vars={"CC": "old-cc", "AR": "old-ar"},
        )
        cmds = preset.resolved_tool_cmds()
        assert cmds["cc"] == "new-cc"
        assert cmds["ar"] == "old-ar"


class TestWasmPresetsNeedWasmToolchain:
    """wasm cross presets on a native toolchain fail fast: the dedicated
    toolchains own suffixes, shared-lib rules, and the link driver."""

    def test_emscripten_preset_on_llvm_raises(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        with pytest.raises(ValueError, match="dedicated toolchain"):
            LlvmToolchain().apply_cross_preset(env, emscripten())

    def test_wasi_preset_on_gcc_raises(self, test_project):  # noqa: F811
        from pcons.toolchains.gcc import GccToolchain
        from pcons.toolchains.presets import wasi_sdk

        env = _make_unix_env()
        with pytest.raises(ValueError, match="dedicated toolchain"):
            GccToolchain().apply_cross_preset(env, wasi_sdk())

    def test_pyodide_on_emscripten_toolchain_ok(self, test_project):  # noqa: F811
        """Already covered above, but assert the positive path explicitly:
        wasm presets on wasm toolchains apply their extra flags."""
        from pcons.toolchains.emscripten import EmscriptenToolchain

        env = _make_unix_env()
        EmscriptenToolchain().apply_cross_preset(env, pyodide())
        assert "-sSIDE_MODULE=1" in env.link.flags


class TestEmsdkSetupPreset:
    """emsdk tool commands are declared via setup_presets (attributable)."""

    def test_setup_presets_wires_emsdk(self, test_project, tmp_path):  # noqa: F811
        from pcons.toolchains.emscripten import EmscriptenToolchain

        emcc_dir = tmp_path / "upstream" / "emscripten"
        emcc_dir.mkdir(parents=True)
        (emcc_dir / "emcc").touch()

        tc = EmscriptenToolchain()
        tc._emsdk_path = tmp_path

        env = _make_unix_env()
        ar = env.add_tool("ar")
        ar.set("cmd", "ar")
        ar.set("flags", [])

        presets = tc.setup_presets(env)
        assert [p.name for p in presets] == ["emsdk"]
        env.apply(presets[0])

        assert env.cc.cmd.endswith("emcc")
        assert env.cxx.cmd.endswith("em++")
        assert env.link.cmd.endswith("emcc")
        assert env.ar.cmd.endswith("emar")


class TestEnvironmentRemembersItsTarget:
    """env.cross reads back the preset the environment was retargeted with."""

    def test_env_reads_back_the_preset(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()
        preset = CrossPreset(
            name="test",
            arch="arm64",
            triple="aarch64-linux-gnu",
            sysroot="/opt/sysroots/aarch64",
        )
        env.apply_cross_preset(preset)

        assert env.cross is preset
        assert env.cross.sysroot == "/opt/sysroots/aarch64"
        assert env.cross.triple == "aarch64-linux-gnu"

    def test_an_untouched_env_reads_none(self, test_project):  # noqa: F811
        assert _make_unix_env().cross is None

    def test_a_clone_carries_the_preset(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()
        preset = CrossPreset(name="test", arch="arm64", triple="aarch64-linux-gnu")
        env.apply_cross_preset(preset)

        clone = env.clone()
        assert clone.cross is preset

        other = CrossPreset(name="other", arch="armv7", triple="arm-linux-gnueabihf")
        clone.apply_cross_preset(other)
        assert clone.cross is other
        assert env.cross is preset

    def test_the_last_preset_wins(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()
        first = CrossPreset(name="first", arch="arm64", triple="aarch64-linux-gnu")
        second = CrossPreset(name="second", arch="armv7", triple="arm-linux-gnueabihf")
        env.apply_cross_preset(first)
        env.apply_cross_preset(second)

        assert env.cross is second

    def test_a_refused_preset_leaves_cross_alone(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()
        preset = CrossPreset(name="test", arch="arm64", triple="aarch64-linux-gnu")
        env.apply_cross_preset(preset)

        with pytest.raises(ValueError, match="dedicated toolchain"):
            env.apply_cross_preset(emscripten())

        assert env.cross is preset

    def test_no_toolchain_records_nothing(self, test_project):  # noqa: F811
        env = Environment()
        env.apply_cross_preset(
            CrossPreset(name="test", arch="arm64", triple="aarch64-linux-gnu")
        )

        assert env.cross is None


class TestTriplesNameAPlatform:
    """target_platform_for_triple reads a target out of a compiler triple."""

    def test_android_beats_the_linux_in_its_own_triple(self) -> None:
        plat = target_platform_for_triple("aarch64-linux-android35")
        assert plat is not None
        assert plat.os == "android"
        assert plat.arch == "arm64"
        assert plat.is_64bit

    def test_a_32_bit_android_triple(self) -> None:
        plat = target_platform_for_triple("armv7a-linux-androideabi21")
        assert plat is not None
        assert plat.os == "android"
        assert plat.arch == "arm"
        assert not plat.is_64bit

    def test_mingw_is_windows_with_gnu_names(self) -> None:
        plat = target_platform_for_triple("x86_64-w64-mingw32")
        assert plat is not None
        assert plat.os == "windows"
        assert plat.exe_suffix == ".exe"
        assert (plat.shared_lib_prefix, plat.shared_lib_suffix) == ("", ".dll")
        assert (plat.static_lib_prefix, plat.static_lib_suffix) == ("lib", ".a")
        assert plat.object_suffix == ".o"

    def test_msvc_is_windows_with_microsoft_names(self) -> None:
        plat = target_platform_for_triple("x86_64-pc-windows-msvc")
        assert plat is not None
        assert (plat.static_lib_prefix, plat.static_lib_suffix) == ("", ".lib")
        assert plat.object_suffix == ".obj"

    def test_ios_and_darwin_use_dylib(self) -> None:
        for triple in ("arm64-apple-ios15.0", "x86_64-apple-darwin"):
            plat = target_platform_for_triple(triple)
            assert plat is not None
            assert plat.shared_lib_suffix == ".dylib"

    def test_the_ios_simulator_keeps_its_arch(self) -> None:
        plat = target_platform_for_triple("x86_64-apple-ios15.0-simulator")
        assert plat is not None
        assert plat.os == "ios"
        assert plat.arch == "x86_64"

    def test_a_linux_cross_triple(self) -> None:
        plat = target_platform_for_triple("aarch64-linux-gnu")
        assert plat is not None
        assert plat.os == "linux"
        assert plat.shared_lib_suffix == ".so"

    @pytest.mark.parametrize(
        "triple", ["wasm32-wasi", "wasm32-unknown-emscripten", "nonsense"]
    )
    def test_an_unrecognized_triple_is_none_not_an_error(self, triple: str) -> None:
        assert target_platform_for_triple(triple) is None


class TestPresetsNameAPlatform:
    """CrossPreset.target_platform: explicit first, then the triple."""

    def test_derived_from_the_triple(self) -> None:
        preset = CrossPreset(name="p", arch="arm64", triple="aarch64-linux-android35")
        plat = preset.target_platform
        assert plat is not None
        assert plat.os == "android"

    def test_an_explicit_target_wins(self) -> None:
        override = target_platform_for_triple("x86_64-w64-mingw32")
        preset = CrossPreset(
            name="p", arch="arm64", triple="aarch64-linux-android35", target=override
        )
        assert preset.target_platform is override

    def test_no_triple_and_no_target_says_nothing(self) -> None:
        assert CrossPreset(name="p", arch="arm64").target_platform is None

    def test_the_android_factory_targets_android(self) -> None:
        plat = android(ndk="/nowhere", arch="arm64-v8a", api=21).target_platform
        assert plat is not None
        assert plat.os == "android"
        assert plat.shared_lib_prefix == "lib"
        assert plat.shared_lib_suffix == ".so"
        assert plat.exe_suffix == ""

    def test_the_ios_factory_targets_ios(self) -> None:
        plat = ios(arch="arm64").target_platform
        assert plat is not None
        assert plat.os == "ios"

    def test_the_wasm_factories_say_nothing(self) -> None:
        assert emscripten().target_platform is None


def _make_unix_env_with_ar() -> Environment:
    """_make_unix_env plus an archiver, which the android preset repoints."""
    env = _make_unix_env()
    ar = env.add_tool("ar")
    ar.set("cmd", "ar")
    ar.set("flags", [])
    return env


class TestEnvironmentKnowsItsTarget:
    """env.target: what is being built for, defaulting to the host."""

    def test_an_untouched_env_reads_the_host(self, test_project):  # noqa: F811
        from pcons.configure.platform import get_platform

        assert _make_unix_env().target == get_platform()

    def test_an_android_preset_retargets_the_env(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env_with_ar()
        env._toolchain = LlvmToolchain()
        env.apply_cross_preset(android(ndk="/nowhere", arch="arm64-v8a", api=21))

        assert env.target.os == "android"
        assert env.target.shared_lib_prefix == "lib"
        assert env.target.shared_lib_suffix == ".so"
        assert env.target.exe_suffix == ""

    def test_a_mingw_preset_names_gnu_windows(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()
        env.apply_cross_preset(
            CrossPreset(name="mingw", arch="x86_64", triple="x86_64-w64-mingw32")
        )

        assert env.target.exe_suffix == ".exe"
        assert env.target.shared_lib_suffix == ".dll"
        assert env.target.static_lib_suffix == ".a"
        assert env.target.static_lib_prefix == "lib"

    def test_an_explicit_target_on_the_preset_wins(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()
        override = target_platform_for_triple("x86_64-w64-mingw32")
        env.apply_cross_preset(
            CrossPreset(
                name="odd", arch="arm64", triple="aarch64-linux-gnu", target=override
            )
        )

        assert env.target is override

    def test_an_unrecognized_triple_falls_back_to_the_host(
        self,
        test_project,  # noqa: F811
    ) -> None:
        from pcons.configure.platform import get_platform
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env()
        env._toolchain = LlvmToolchain()
        env.apply_cross_preset(
            CrossPreset(name="odd", arch="sparc", triple="sparc9-sun-solaris")
        )

        assert env.target == get_platform()

    def test_a_clone_carries_the_target(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env_with_ar()
        env._toolchain = LlvmToolchain()
        env.apply_cross_preset(android(ndk="/nowhere", arch="arm64-v8a", api=21))

        clone = env.clone()
        assert clone.target.os == "android"

        clone.apply_cross_preset(
            CrossPreset(name="mingw", arch="x86_64", triple="x86_64-w64-mingw32")
        )
        assert clone.target.os == "windows"
        assert env.target.os == "android"

    def test_a_refused_preset_leaves_the_target_alone(self, test_project):  # noqa: F811
        from pcons.toolchains.llvm import LlvmToolchain

        env = _make_unix_env_with_ar()
        env._toolchain = LlvmToolchain()
        env.apply_cross_preset(android(ndk="/nowhere", arch="arm64-v8a", api=21))

        with pytest.raises(ValueError, match="dedicated toolchain"):
            env.apply_cross_preset(emscripten())

        assert env.target.os == "android"

    def test_no_toolchain_records_no_target(self, test_project):  # noqa: F811
        from pcons.configure.platform import get_platform

        env = Environment()
        env.apply_cross_preset(android(ndk="/nowhere", arch="arm64-v8a", api=21))

        assert env.target == get_platform()


class TestAndroidPresetAgainstARealNdk:
    """The android() factory against an installed NDK, not a guessed layout."""

    def test_the_tool_commands_it_names_exist(self, android_ndk) -> None:
        preset = android(ndk=str(android_ndk), arch="arm64-v8a", api=21)

        for tool, cmd in preset.resolved_tool_cmds().items():
            assert Path(cmd).is_file(), f"{tool} -> {cmd}"

    def test_the_sysroot_it_names_exists(self, android_ndk) -> None:
        preset = android(ndk=str(android_ndk), arch="arm64-v8a", api=21)

        assert preset.sysroot is not None
        assert (Path(preset.sysroot) / "usr" / "include").is_dir()

    @pytest.mark.parametrize("arch", ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"])
    def test_every_supported_arch_has_a_wrapper(self, arch: str, android_ndk) -> None:
        preset = android(ndk=str(android_ndk), arch=arch, api=21)

        assert Path(preset.resolved_tool_cmds()["cxx"]).is_file()

    @pytest.mark.parametrize(
        "stl,shared_runtime",
        [("c++_shared", True), ("c++_static", False), ("none", False)],
    )
    def test_the_stl_flags_produce_the_runtime_they_claim(
        self, stl: str, shared_runtime: bool, android_ndk, tmp_path: Path
    ) -> None:
        """Link a shared library and read back what it depends on.

        The unit tests above pin the flag; this pins what the flag does,
        so a future NDK changing its default fails here rather than in a
        user's APK.
        """
        preset = android(ndk=str(android_ndk), arch="arm64-v8a", api=21, stl=stl)  # type: ignore[arg-type]
        source = tmp_path / "s.cpp"
        source.write_text("#include <string>\nstd::string f() { return {}; }\n")
        out = tmp_path / "libs.so"

        subprocess.run(
            [
                preset.resolved_tool_cmds()["cxx"],
                "-shared",
                "-fPIC",
                str(source),
                *preset.extra_link_flags,
                "-o",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        ar = Path(preset.resolved_tool_cmds()["ar"])
        readelf = ar.with_name(f"llvm-readelf{ar.suffix}")
        needed = subprocess.run(
            [str(readelf), "-d", str(out)], capture_output=True, text=True, check=True
        ).stdout

        assert ("libc++_shared.so" in needed) is shared_runtime


_MINGW = CrossPreset(
    name="mingw",
    arch="x86_64",
    triple="x86_64-w64-mingw32",
    tool_cmds={
        "cc": "x86_64-w64-mingw32-gcc",
        "cxx": "x86_64-w64-mingw32-g++",
        "link": "x86_64-w64-mingw32-g++",
        "ar": "x86_64-w64-mingw32-ar",
    },
)

_ANDROID = CrossPreset(
    name="android",
    arch="arm64-v8a",
    triple="aarch64-linux-android35",
    tool_cmds={
        "cc": "aarch64-linux-android35-clang",
        "cxx": "aarch64-linux-android35-clang++",
        "link": "aarch64-linux-android35-clang++",
        "ar": "llvm-ar",
    },
)


def _cross_project(tmp_path, gcc_toolchain, preset=None):
    """A project with one C source and an environment, optionally retargeted."""
    from pcons.core.project import Project

    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.c").write_text("int f(void) { return 1; }\n")

    project = Project("p", root_dir=tmp_path)
    env = project.Environment(toolchain=gcc_toolchain)
    if preset is not None:
        env.apply_cross_preset(preset)
    return project, env


def _names(target) -> set[str]:
    return {node.path.name for node in target.output_nodes}


class TestCrossOutputNamingFollowsTheTarget:
    """A cross build's artifacts are named for the platform they target."""

    def test_a_host_build_is_named_the_way_it_always_was(self, tmp_path, gcc_toolchain):
        from pcons.configure.platform import get_platform

        project, env = _cross_project(tmp_path, gcc_toolchain)
        prog = project.Program("foo", env, sources=["src/foo.c"])
        shared = project.SharedLibrary("bar", env, sources=["src/foo.c"])
        static = project.StaticLibrary("baz", env, sources=["src/foo.c"])
        project.resolve()

        host = get_platform()
        assert _names(prog) == {host.exe_name("foo")}
        assert host.shared_lib_name("bar") in _names(shared)
        assert _names(static) == {host.static_lib_name("baz")}

    def test_an_android_target_names_a_shared_library(self, tmp_path, gcc_toolchain):
        project, env = _cross_project(tmp_path, gcc_toolchain, _ANDROID)
        prog = project.Program("foo", env, sources=["src/foo.c"])
        shared = project.SharedLibrary("bar", env, sources=["src/foo.c"])
        project.resolve()

        assert _names(prog) == {"foo"}
        assert _names(shared) == {"libbar.so"}

    def test_a_mingw_target_names_windows_artifacts(self, tmp_path, gcc_toolchain):
        project, env = _cross_project(tmp_path, gcc_toolchain, _MINGW)
        prog = project.Program("foo", env, sources=["src/foo.c"])
        static = project.StaticLibrary("baz", env, sources=["src/foo.c"])
        project.resolve()

        assert _names(prog) == {"foo.exe"}
        assert _names(static) == {"libbaz.a"}

    def test_a_mingw_shared_library_is_a_dll(self, tmp_path, gcc_toolchain):
        project, env = _cross_project(tmp_path, gcc_toolchain, _MINGW)
        shared = project.SharedLibrary("bar", env, sources=["src/foo.c"])
        project.resolve()

        assert "bar.dll" in _names(shared)

    def test_an_explicit_suffix_still_wins(self, tmp_path, gcc_toolchain):
        project, env = _cross_project(tmp_path, gcc_toolchain, _ANDROID)
        shared = project.SharedLibrary("bar", env, sources=["src/foo.c"])
        shared.output_prefix = ""
        shared.output_suffix = ".node"
        project.resolve()

        assert _names(shared) == {"bar.node"}

    def test_an_explicit_output_name_still_wins(self, tmp_path, gcc_toolchain):
        project, env = _cross_project(tmp_path, gcc_toolchain, _ANDROID)
        shared = project.SharedLibrary("bar", env, sources=["src/foo.c"])
        shared.output_name = "renamed"
        project.resolve()

        assert _names(shared) == {"librenamed.so"}

    def test_two_environments_in_one_project_name_differently(
        self, tmp_path, gcc_toolchain
    ):
        from pcons.core.project import Project

        src = tmp_path / "src"
        src.mkdir()
        (src / "foo.c").write_text("int f(void) { return 1; }\n")

        from pcons.configure.platform import get_platform

        host = get_platform()
        cross_preset = _ANDROID if host.is_windows else _MINGW
        cross_name = "foo" if host.is_windows else "foo.exe"

        project = Project("p", root_dir=tmp_path)
        host_env = project.Environment(toolchain=gcc_toolchain, name="host")
        cross_env = project.Environment(toolchain=gcc_toolchain, name="cross")
        cross_env.apply_cross_preset(cross_preset)

        host_prog = project.Program("foo", host_env, sources=["src/foo.c"])
        cross_prog = project.Program("foo", cross_env, sources=["src/foo.c"])
        project.resolve()

        assert _names(host_prog) == {host.exe_name("foo")}
        assert _names(cross_prog) == {cross_name}


class TestCrossInstallDirFollowsTheTarget:
    """install_dir asks the environment what it is building for."""

    def test_a_windows_target_installs_a_shared_library_to_bin(
        self, tmp_path, gcc_toolchain
    ):
        from pcons.tools.install import install_dir

        _, env = _cross_project(tmp_path, gcc_toolchain, _MINGW)

        assert install_dir(env, "shared_library") == "bin"
        assert install_dir(env, "static_library") == "lib"
        assert install_dir(env, "program") == "bin"

    def test_an_android_target_installs_a_shared_library_to_lib(
        self, tmp_path, gcc_toolchain
    ):
        from pcons.tools.install import install_dir

        _, env = _cross_project(tmp_path, gcc_toolchain, _ANDROID)

        assert install_dir(env, "shared_library") == "lib"
