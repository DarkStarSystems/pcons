# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.emscripten."""

import pytest

from pcons.core.builder import MultiOutputBuilder
from pcons.core.subst import TargetPath
from pcons.toolchains.emscripten import (
    EmccArchiver,
    EmccCCompiler,
    EmccCxxCompiler,
    EmccLinker,
    EmscriptenToolchain,
    find_emscripten_toolchain,
    find_emsdk,
    is_emcc_available,
)

# =============================================================================
# Tool tests (no SDK required — just exercise class construction)
# =============================================================================


class TestEmccCCompiler:
    def test_creation(self):
        cc = EmccCCompiler()
        assert cc.name == "cc"
        assert cc.language == "c"

    def test_default_vars(self):
        cc = EmccCCompiler()
        v = cc.default_vars()
        assert v["cmd"] == "emcc"
        assert "objcmd" in v
        # emcc doesn't need --target or --sysroot (handles internally)
        objcmd = v["objcmd"]
        assert isinstance(objcmd, list)
        assert "$cc.cmd" in objcmd
        assert "--target=wasm32-wasi" not in objcmd

    def test_builders(self):
        cc = EmccCCompiler()
        builders = cc.builders()
        assert "Object" in builders
        obj = builders["Object"]
        assert ".c" in obj.src_suffixes
        assert ".o" in obj.target_suffixes


class TestEmccCxxCompiler:
    def test_creation(self):
        cxx = EmccCxxCompiler()
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cxx = EmccCxxCompiler()
        v = cxx.default_vars()
        assert v["cmd"] == "em++"
        objcmd = v["objcmd"]
        assert isinstance(objcmd, list)
        assert "$cxx.cmd" in objcmd

    def test_builders(self):
        cxx = EmccCxxCompiler()
        builders = cxx.builders()
        assert "Object" in builders
        obj = builders["Object"]
        assert ".cpp" in obj.src_suffixes
        assert ".cxx" in obj.src_suffixes
        assert ".cc" in obj.src_suffixes


class TestEmccArchiver:
    def test_creation(self):
        ar = EmccArchiver()
        assert ar.name == "ar"

    def test_default_vars(self):
        ar = EmccArchiver()
        v = ar.default_vars()
        assert v["cmd"] == "emar"
        assert v["flags"] == ["rcs"]

    def test_builders(self):
        ar = EmccArchiver()
        builders = ar.builders()
        assert "StaticLibrary" in builders
        lib = builders["StaticLibrary"]
        assert ".o" in lib.src_suffixes
        assert ".a" in lib.target_suffixes


class TestEmccLinker:
    def test_creation(self):
        link = EmccLinker()
        assert link.name == "link"

    def test_default_vars(self):
        link = EmccLinker()
        v = link.default_vars()
        assert v["cmd"] == "emcc"
        assert "progcmd" in v
        # Check -s settings support
        assert v["sprefix"] == "-s"
        assert v["settings"] == []
        progcmd = v["progcmd"]
        assert isinstance(progcmd, list)
        assert "${prefix(link.sprefix, link.settings)}" in progcmd

    def test_builders_has_program(self):
        link = EmccLinker()
        builders = link.builders()
        assert "Program" in builders
        prog = builders["Program"]
        assert isinstance(prog, MultiOutputBuilder)
        assert ".js" in prog.target_suffixes

    def test_program_builder_has_two_outputs(self):
        link = EmccLinker()
        builders = link.builders()
        prog = builders["Program"]
        assert isinstance(prog, MultiOutputBuilder)
        assert len(prog.outputs) == 2
        assert prog.outputs[0].name == "primary"
        assert prog.outputs[0].suffix == ".js"
        assert prog.outputs[1].name == "wasm"
        assert prog.outputs[1].suffix == ".wasm"

    def test_no_shared_library_builder(self):
        link = EmccLinker()
        builders = link.builders()
        assert "SharedLibrary" not in builders


# =============================================================================
# Toolchain tests
# =============================================================================


class TestEmscriptenToolchain:
    def test_creation(self):
        tc = EmscriptenToolchain()
        assert tc.name == "emscripten"

    def test_tools_empty_before_configure(self):
        tc = EmscriptenToolchain()
        assert tc.tools == {}

    def test_program_name(self):
        tc = EmscriptenToolchain()
        assert tc.get_program_name("hello") == "hello.js"
        assert tc.get_program_name("app") == "app.js"

    def test_shared_library_raises(self):
        tc = EmscriptenToolchain()
        with pytest.raises(NotImplementedError, match="shared libraries"):
            tc.get_shared_library_name("foo")

    def test_shared_library_compile_flags_raises(self):
        tc = EmscriptenToolchain()
        with pytest.raises(NotImplementedError, match="shared libraries"):
            tc.get_compile_flags_for_target_type("shared_library")

    def test_static_library_name(self):
        tc = EmscriptenToolchain()
        assert tc.get_static_library_name("foo") == "libfoo.a"

    def test_object_suffix(self):
        tc = EmscriptenToolchain()
        assert tc.get_object_suffix() == ".o"

    def test_no_fpic_for_programs(self):
        tc = EmscriptenToolchain()
        assert tc.get_compile_flags_for_target_type("program") == []

    def test_no_fpic_for_static_lib(self):
        tc = EmscriptenToolchain()
        assert tc.get_compile_flags_for_target_type("static_library") == []


class TestEmscriptenSourceHandlers:
    def test_c_source(self):
        tc = EmscriptenToolchain()
        handler = tc.get_source_handler(".c")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "c"
        assert handler.object_suffix == ".o"
        assert handler.depfile == TargetPath(suffix=".d")
        assert handler.deps_style == "gcc"

    def test_cpp_source(self):
        tc = EmscriptenToolchain()
        handler = tc.get_source_handler(".cpp")
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx"

    def test_cc_source(self):
        tc = EmscriptenToolchain()
        handler = tc.get_source_handler(".cc")
        assert handler is not None
        assert handler.tool_name == "cxx"

    def test_uppercase_C(self):
        tc = EmscriptenToolchain()
        handler = tc.get_source_handler(".C")
        assert handler is not None
        assert handler.tool_name == "cxx"

    def test_no_objc(self):
        """Emscripten toolchain should not handle Objective-C."""
        tc = EmscriptenToolchain()
        assert tc.get_source_handler(".m") is None
        assert tc.get_source_handler(".mm") is None

    def test_no_assembly(self):
        """Emscripten toolchain should not handle assembly."""
        tc = EmscriptenToolchain()
        assert tc.get_source_handler(".s") is None
        assert tc.get_source_handler(".S") is None

    def test_unknown_suffix(self):
        tc = EmscriptenToolchain()
        assert tc.get_source_handler(".xyz") is None


# =============================================================================
# SDK detection (basic — tests probe logic, not actual filesystem)
# =============================================================================


class TestFindEmsdk:
    def test_returns_none_when_not_installed(self, monkeypatch):
        """Without EMSDK env var and no common locations, returns None."""
        monkeypatch.delenv("EMSDK", raising=False)
        # Remove emcc from PATH to avoid detecting it
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        result = find_emsdk()
        assert result is None

    def test_respects_env_var(self, monkeypatch, tmp_path):
        """EMSDK pointing to a valid SDK root is respected."""
        # Create a fake emsdk layout
        (tmp_path / "upstream" / "emscripten").mkdir(parents=True)
        (tmp_path / "upstream" / "emscripten" / "emcc").touch()
        monkeypatch.setenv("EMSDK", str(tmp_path))
        result = find_emsdk()
        assert result == tmp_path

    def test_ignores_invalid_env_var(self, monkeypatch, tmp_path):
        """EMSDK pointing to a non-SDK directory is ignored."""
        monkeypatch.setenv("EMSDK", str(tmp_path))
        # tmp_path exists but has no upstream/emscripten/emcc
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        result = find_emsdk()
        assert result is None

    def test_is_emcc_available_false(self, monkeypatch):
        """is_emcc_available returns False when emcc not found."""
        monkeypatch.delenv("EMSDK", raising=False)
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        assert is_emcc_available() is False

    def test_is_emcc_available_via_path(self, monkeypatch):
        """is_emcc_available returns True when emcc is on PATH."""
        monkeypatch.delenv("EMSDK", raising=False)
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/bin/emcc" if cmd == "emcc" else None
        )
        assert is_emcc_available() is True


# =============================================================================
# Registration
# =============================================================================


class TestEmscriptenRegistration:
    def test_registered_in_registry(self):
        from pcons.tools.toolchain import toolchain_registry

        entry = toolchain_registry.get("emscripten")
        assert entry is not None
        assert entry.toolchain_class is EmscriptenToolchain
        assert entry.category == "wasm"

    def test_alias_emcc(self):
        from pcons.tools.toolchain import toolchain_registry

        entry = toolchain_registry.get("emcc")
        assert entry is not None
        assert entry.toolchain_class is EmscriptenToolchain

    def test_is_available_wired_to_real_probe(self):
        """The registry entry must gate on is_emcc_available, not just
        ``shutil.which("emcc")`` directly — this is what lets
        find_available() correctly skip Emscripten when emcc is absent
        even though another wasm-category toolchain's check_command
        (e.g. WASI's "clang") happens to be on PATH."""
        from pcons.tools.toolchain import toolchain_registry

        entry = toolchain_registry.get("emscripten")
        assert entry is not None
        assert entry.is_available is is_emcc_available


class TestFindEmscriptenToolchain:
    """find_emscripten_toolchain() must not return a WASI toolchain when
    emcc is absent — a bare ``clang`` on PATH (WASI's check_command) is
    not Emscripten, and must not be silently substituted."""

    def test_raises_when_no_emcc(self, monkeypatch):
        monkeypatch.delenv("EMSDK", raising=False)
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        with pytest.raises(RuntimeError, match="Emscripten not found"):
            find_emscripten_toolchain()

    def test_does_not_silently_return_wasi(self, monkeypatch, tmp_path):
        """Even if a real wasi-sdk happens to be installed (category-wide
        fallback), find_emscripten_toolchain must raise rather than
        silently return a WasiToolchain — which would produce a bare
        .wasm instead of the expected .js + .wasm pair."""
        from pcons.toolchains import wasi

        monkeypatch.delenv("EMSDK", raising=False)
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        monkeypatch.setattr(wasi, "find_wasi_sdk", lambda: tmp_path)
        with pytest.raises(RuntimeError, match="Emscripten not found"):
            find_emscripten_toolchain()


class TestEmsdkHints:
    """Tests for _emsdk_hints search-hint helper."""

    def test_none_when_no_emsdk(self, monkeypatch):
        from pcons.toolchains import emscripten

        monkeypatch.setattr(emscripten, "find_emsdk", lambda: None)
        assert emscripten._emsdk_hints() is None

    def test_returns_emcc_dir_when_found(self, monkeypatch, tmp_path):
        from pcons.toolchains import emscripten

        emcc_dir = tmp_path / "upstream" / "emscripten"
        monkeypatch.setattr(emscripten, "find_emsdk", lambda: tmp_path)
        monkeypatch.setattr(emscripten, "_find_emcc_dir", lambda _p: emcc_dir)
        assert emscripten._emsdk_hints() == [emcc_dir]

    def test_none_when_emcc_dir_missing(self, monkeypatch, tmp_path):
        from pcons.toolchains import emscripten

        monkeypatch.setattr(emscripten, "find_emsdk", lambda: tmp_path)
        monkeypatch.setattr(emscripten, "_find_emcc_dir", lambda _p: None)
        assert emscripten._emsdk_hints() is None


class TestEmccConfigureMissing:
    """configure() returns None when the program is not found."""

    @pytest.mark.parametrize(
        "cls", [EmccCCompiler, EmccCxxCompiler, EmccArchiver, EmccLinker]
    )
    def test_configure_returns_none(self, cls, monkeypatch, tmp_path):
        from pcons.configure.config import Configure
        from pcons.toolchains import emscripten

        monkeypatch.setattr(emscripten, "_emsdk_hints", lambda: None)
        config = Configure(build_dir=tmp_path)
        config.find_program = lambda *a, **k: None  # type: ignore[method-assign]
        assert cls().configure(config) is None
