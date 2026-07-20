# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.wasi."""

import pytest

from pcons.core.subst import TargetPath
from pcons.toolchains.wasi import (
    WasiArchiver,
    WasiCCompiler,
    WasiCxxCompiler,
    WasiLinker,
    WasiToolchain,
    find_wasi_sdk,
    find_wasi_toolchain,
    is_wasi_sdk_available,
)

# =============================================================================
# Tool tests (no SDK required — just exercise class construction)
# =============================================================================


class TestWasiCCompiler:
    def test_creation(self):
        cc = WasiCCompiler()
        assert cc.name == "cc"
        assert cc.language == "c"

    def test_default_vars(self):
        cc = WasiCCompiler()
        v = cc.default_vars()
        assert v["cmd"] == "clang"
        assert "objcmd" in v
        assert "--target=wasm32-wasi" in v["objcmd"]
        # sysroot is contributed by the wasi-sdk setup preset, not a template var
        assert "$cc.sysroot_flag" not in v["objcmd"]

    def test_builders(self):
        cc = WasiCCompiler()
        builders = cc.builders()
        assert "Object" in builders
        obj = builders["Object"]
        assert ".c" in obj.src_suffixes
        assert ".o" in obj.target_suffixes


class TestWasiCxxCompiler:
    def test_creation(self):
        cxx = WasiCxxCompiler()
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cxx = WasiCxxCompiler()
        v = cxx.default_vars()
        assert v["cmd"] == "clang++"
        assert "--target=wasm32-wasi" in v["objcmd"]

    def test_builders(self):
        cxx = WasiCxxCompiler()
        builders = cxx.builders()
        assert "Object" in builders
        obj = builders["Object"]
        assert ".cpp" in obj.src_suffixes
        assert ".cxx" in obj.src_suffixes
        assert ".cc" in obj.src_suffixes


class TestWasiArchiver:
    def test_creation(self):
        ar = WasiArchiver()
        assert ar.name == "ar"

    def test_default_vars(self):
        ar = WasiArchiver()
        v = ar.default_vars()
        assert v["cmd"] == "llvm-ar"
        assert v["flags"] == ["rcs"]

    def test_builders(self):
        ar = WasiArchiver()
        builders = ar.builders()
        assert "StaticLibrary" in builders
        lib = builders["StaticLibrary"]
        assert ".o" in lib.src_suffixes
        assert ".a" in lib.target_suffixes


class TestWasiLinker:
    def test_creation(self):
        link = WasiLinker()
        assert link.name == "link"

    def test_default_vars(self):
        link = WasiLinker()
        v = link.default_vars()
        assert v["cmd"] == "clang"
        assert "--target=wasm32-wasi" in v["progcmd"]
        assert "$link.sysroot_flag" not in v["progcmd"]

    def test_builders_has_program(self):
        link = WasiLinker()
        builders = link.builders()
        assert "Program" in builders
        prog = builders["Program"]
        assert ".wasm" in prog.target_suffixes

    def test_no_shared_library_builder(self):
        link = WasiLinker()
        builders = link.builders()
        assert "SharedLibrary" not in builders


# =============================================================================
# Toolchain tests
# =============================================================================


class TestWasiToolchain:
    def test_creation(self):
        tc = WasiToolchain()
        assert tc.name == "wasi"

    def test_tools_empty_before_configure(self):
        tc = WasiToolchain()
        assert tc.tools == {}

    def test_program_name(self):
        tc = WasiToolchain()
        assert tc.get_program_name("hello") == "hello.wasm"
        assert tc.get_program_name("app") == "app.wasm"

    def test_shared_library_raises(self):
        tc = WasiToolchain()
        with pytest.raises(NotImplementedError, match="shared libraries"):
            tc.get_shared_library_name("foo")

    def test_shared_library_compile_flags_raises(self):
        tc = WasiToolchain()
        with pytest.raises(NotImplementedError, match="shared libraries"):
            tc.get_compile_flags_for_target_type("shared_library")

    def test_static_library_name(self):
        tc = WasiToolchain()
        assert tc.get_static_library_name("foo") == "libfoo.a"

    def test_object_suffix(self):
        tc = WasiToolchain()
        assert tc.get_object_suffix() == ".o"

    def test_no_fpic_for_programs(self):
        tc = WasiToolchain()
        assert tc.get_compile_flags_for_target_type("program") == []

    def test_no_fpic_for_static_lib(self):
        tc = WasiToolchain()
        assert tc.get_compile_flags_for_target_type("static_library") == []


class TestWasiSourceHandlers:
    def test_c_source(self):
        tc = WasiToolchain()
        handler = tc.get_source_handler(".c")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "c"
        assert handler.object_suffix == ".o"
        assert handler.depfile == TargetPath(suffix=".d")
        assert handler.deps_style == "gcc"

    def test_cpp_source(self):
        tc = WasiToolchain()
        handler = tc.get_source_handler(".cpp")
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx"

    def test_cc_source(self):
        tc = WasiToolchain()
        handler = tc.get_source_handler(".cc")
        assert handler is not None
        assert handler.tool_name == "cxx"

    def test_uppercase_C(self):
        tc = WasiToolchain()
        handler = tc.get_source_handler(".C")
        assert handler is not None
        assert handler.tool_name == "cxx"

    def test_no_objc(self):
        """WASI toolchain should not handle Objective-C."""
        tc = WasiToolchain()
        assert tc.get_source_handler(".m") is None
        assert tc.get_source_handler(".mm") is None

    def test_no_assembly(self):
        """WASI toolchain should not handle assembly."""
        tc = WasiToolchain()
        assert tc.get_source_handler(".s") is None
        assert tc.get_source_handler(".S") is None

    def test_unknown_suffix(self):
        tc = WasiToolchain()
        assert tc.get_source_handler(".xyz") is None


# =============================================================================
# SDK detection (basic — tests probe logic, not actual filesystem)
# =============================================================================


class TestFindWasiSdk:
    def test_returns_none_when_not_installed(self, monkeypatch):
        """Without WASI_SDK_PATH and no common locations, returns None."""
        monkeypatch.delenv("WASI_SDK_PATH", raising=False)
        # We can't easily mock the filesystem probes, but on a system
        # without wasi-sdk the function should return None.
        # (If wasi-sdk IS installed, this test still passes — it just
        # returns a path.)
        result = find_wasi_sdk()
        # We just verify it returns a Path or None (no crash)
        assert result is None or hasattr(result, "is_dir")

    def test_respects_env_var(self, monkeypatch, tmp_path):
        """WASI_SDK_PATH pointing to a valid SDK root is respected."""
        # Create a fake wasi-sdk layout
        (tmp_path / "bin").mkdir()
        (tmp_path / "share" / "wasi-sysroot").mkdir(parents=True)
        monkeypatch.setenv("WASI_SDK_PATH", str(tmp_path))
        result = find_wasi_sdk()
        assert result == tmp_path

    def test_ignores_invalid_env_var(self, monkeypatch, tmp_path):
        """WASI_SDK_PATH pointing to a non-SDK directory is ignored."""
        monkeypatch.setenv("WASI_SDK_PATH", str(tmp_path))
        # tmp_path exists but has no bin/ or share/wasi-sysroot/
        result = find_wasi_sdk()
        # Should not return tmp_path (falls through to probing)
        # Might return None or an actual SDK if one is installed
        assert result != tmp_path or result is None


# =============================================================================
# Registration
# =============================================================================


class TestWasiRegistration:
    def test_registered_in_registry(self):
        from pcons.tools.toolchain import toolchain_registry

        entry = toolchain_registry.get("wasi")
        assert entry is not None
        assert entry.toolchain_class is WasiToolchain
        assert entry.category == "wasm"

    def test_alias_wasi_sdk(self):
        from pcons.tools.toolchain import toolchain_registry

        entry = toolchain_registry.get("wasi-sdk")
        assert entry is not None
        assert entry.toolchain_class is WasiToolchain

    def test_is_available_wired_to_real_sdk_probe(self):
        """The registry entry must gate on a real wasi-sdk probe, not just
        ``shutil.which("clang")`` — otherwise any system clang would make
        the toolchain look available (see find_available)."""
        from pcons.tools.toolchain import toolchain_registry

        entry = toolchain_registry.get("wasi")
        assert entry is not None
        assert entry.is_available is is_wasi_sdk_available


class TestIsWasiSdkAvailable:
    def test_false_when_no_sdk(self, monkeypatch):
        from pcons.toolchains import wasi

        monkeypatch.setattr(wasi, "find_wasi_sdk", lambda: None)
        assert is_wasi_sdk_available() is False

    def test_true_when_sdk_found(self, monkeypatch, tmp_path):
        from pcons.toolchains import wasi

        monkeypatch.setattr(wasi, "find_wasi_sdk", lambda: tmp_path)
        assert is_wasi_sdk_available() is True


class TestFindWasiToolchain:
    """find_wasi_toolchain() must not return a toolchain whose wasi-sdk
    isn't actually installed — plain ``clang`` on PATH is not enough."""

    def test_raises_when_no_wasi_sdk(self, monkeypatch):
        from pcons.toolchains import wasi

        monkeypatch.setattr(wasi, "find_wasi_sdk", lambda: None)
        with pytest.raises(RuntimeError, match="wasi-sdk not found"):
            find_wasi_toolchain()

    def test_does_not_silently_return_emscripten(self, monkeypatch):
        """Even if Emscripten happens to be available (category-wide
        fallback), find_wasi_toolchain must raise rather than silently
        return an EmscriptenToolchain mislabeled as WASI."""
        from pcons.toolchains import wasi

        monkeypatch.setattr(wasi, "find_wasi_sdk", lambda: None)
        monkeypatch.delenv("EMSDK", raising=False)
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/bin/emcc" if cmd == "emcc" else None
        )
        with pytest.raises(RuntimeError, match="wasi-sdk not found"):
            find_wasi_toolchain()


class TestWasiHints:
    """Tests for _wasi_hints search-hint helper."""

    def test_none_when_no_sdk(self, monkeypatch):
        from pcons.toolchains import wasi

        monkeypatch.setattr(wasi, "find_wasi_sdk", lambda: None)
        assert wasi._wasi_hints() is None

    def test_returns_bin_when_found(self, monkeypatch, tmp_path):
        from pcons.toolchains import wasi

        monkeypatch.setattr(wasi, "find_wasi_sdk", lambda: tmp_path)
        assert wasi._wasi_hints() == [tmp_path / "bin"]


class TestWasiConfigureMissing:
    """configure() returns None when the program is not found."""

    @pytest.mark.parametrize(
        "cls", [WasiCCompiler, WasiCxxCompiler, WasiArchiver, WasiLinker]
    )
    def test_configure_returns_none(self, cls, monkeypatch, tmp_path):
        from pcons.configure.config import Configure
        from pcons.toolchains import wasi

        monkeypatch.setattr(wasi, "_wasi_hints", lambda: None)
        config = Configure(build_dir=tmp_path)
        config.find_program = lambda *a, **k: None  # type: ignore[method-assign]
        assert cls().configure(config) is None


class TestWasiSdkSetupPreset:
    """SDK wiring is declared via setup_presets, attributable in explain()."""

    def test_setup_presets_wires_sdk(self, test_project, tmp_path):  # noqa: F811
        from pcons.core.environment import Environment
        from pcons.toolchains.wasi import WasiToolchain

        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "llvm-ar").touch()
        sysroot = tmp_path / "share" / "wasi-sysroot"
        sysroot.mkdir(parents=True)

        tc = WasiToolchain()
        tc._sdk_path = tmp_path
        tc._sysroot = sysroot

        env = Environment()
        for name in ("cc", "cxx", "link", "ar"):
            tool = env.add_tool(name)
            tool.set("cmd", name)
            tool.set("flags", [])

        presets = tc.setup_presets(env)
        assert [p.name for p in presets] == ["wasi-sdk"]
        env.apply(presets[0])

        assert env.cc.cmd.endswith("clang")
        assert env.cxx.cmd.endswith("clang++")
        assert env.link.cmd.endswith("clang")
        assert env.ar.cmd.endswith("llvm-ar")
        assert f"--sysroot={sysroot}" in env.cc.flags
        assert f"--sysroot={sysroot}" in env.link.flags

    def test_no_sdk_no_preset(self, test_project):  # noqa: F811
        from pcons.core.environment import Environment
        from pcons.toolchains.wasi import WasiToolchain

        tc = WasiToolchain()
        tc._sdk_path = None
        tc._sysroot = None
        assert tc.setup_presets(Environment()) == []
