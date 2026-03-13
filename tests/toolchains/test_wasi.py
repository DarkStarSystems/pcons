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
        assert "$cc.sysroot_flag" in v["objcmd"]

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
        assert "$link.sysroot_flag" in v["progcmd"]

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
