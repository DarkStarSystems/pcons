# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.clang_cl."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from pcons.toolchains.clang_cl import (
    ClangClCCompiler,
    ClangClCxxCompiler,
    ClangClLibrarian,
    ClangClLinker,
    ClangClToolchain,
)


class TestClangClConfigure:
    """configure() is Windows-gated and otherwise delegates to _find_tool_config."""

    _CLASSES = [ClangClCCompiler, ClangClCxxCompiler, ClangClLibrarian, ClangClLinker]

    @pytest.mark.parametrize("cls", _CLASSES)
    def test_returns_none_off_windows(self, cls, monkeypatch, tmp_path):
        from pcons.configure.config import Configure
        from pcons.toolchains import clang_cl

        monkeypatch.setattr(
            clang_cl, "get_platform", lambda: SimpleNamespace(is_windows=False)
        )
        config = Configure(build_dir=tmp_path)
        assert cls().configure(config) is None

    @pytest.mark.parametrize("cls", _CLASSES)
    def test_finds_program_on_windows(self, cls, monkeypatch, tmp_path):
        from pcons.configure.config import Configure, ProgramInfo
        from pcons.toolchains import clang_cl

        monkeypatch.setattr(
            clang_cl, "get_platform", lambda: SimpleNamespace(is_windows=True)
        )
        config = Configure(build_dir=tmp_path)
        config.find_program = (  # type: ignore[method-assign]
            lambda *a, **k: ProgramInfo(path=Path("C:/llvm/tool.exe"))
        )
        cfg = cls().configure(config)
        assert cfg is not None
        assert cfg.cmd == str(Path("C:/llvm/tool.exe"))


class TestClangClCCompiler:
    def test_creation(self):
        cc = ClangClCCompiler()
        assert cc.name == "cc"
        assert cc.language == "c"

    def test_default_vars(self):
        cc = ClangClCCompiler()
        vars = cc.default_vars()
        assert vars["cmd"] == "clang-cl"
        assert "/nologo" in vars["flags"]
        assert "objcmd" in vars


class TestClangClCxxCompiler:
    def test_creation(self):
        cxx = ClangClCxxCompiler()
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cxx = ClangClCxxCompiler()
        vars = cxx.default_vars()
        assert vars["cmd"] == "clang-cl"
        assert "objcmd" in vars


class TestClangClLibrarian:
    def test_creation(self):
        lib = ClangClLibrarian()
        assert lib.name == "lib"

    def test_default_vars(self):
        lib = ClangClLibrarian()
        vars = lib.default_vars()
        assert vars["cmd"] == "llvm-lib"
        assert "libcmd" in vars


class TestClangClLinker:
    def test_creation(self):
        link = ClangClLinker()
        assert link.name == "link"

    def test_default_vars(self):
        link = ClangClLinker()
        vars = link.default_vars()
        assert vars["cmd"] == "lld-link"
        assert "progcmd" in vars
        assert "sharedcmd" in vars


class TestClangClToolchain:
    def test_creation(self):
        tc = ClangClToolchain()
        assert tc.name == "clang-cl"

    def test_tools_empty_before_configure(self):
        tc = ClangClToolchain()
        assert tc.tools == {}

    def test_object_suffix(self):
        tc = ClangClToolchain()
        assert tc.get_object_suffix() == ".obj"

    def test_archiver_tool_name(self):
        tc = ClangClToolchain()
        assert tc.get_archiver_tool_name() == "lib"


class TestClangClSourceHandlers:
    """Tests for Clang-CL source handler methods."""

    def test_source_handler_c(self):
        """Test that .c files are handled correctly."""
        tc = ClangClToolchain()
        handler = tc.get_source_handler(".c")
        assert handler is not None
        assert handler.tool_name == "cc"
        assert handler.language == "c"
        assert handler.object_suffix == ".obj"
        assert handler.deps_style == "msvc"

    def test_source_handler_cpp(self):
        """Test that .cpp files are handled correctly."""
        tc = ClangClToolchain()
        handler = tc.get_source_handler(".cpp")
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx"

    def test_source_handler_asm(self):
        """Test that .asm files are handled by the MASM assembler."""
        tc = ClangClToolchain()
        handler = tc.get_source_handler(".asm")
        assert handler is not None
        assert handler.tool_name == "ml"
        assert handler.language == "asm"
        assert handler.object_suffix == ".obj"
        assert handler.command_var == "asmcmd"
        # MASM doesn't generate depfiles
        assert handler.depfile is None
        assert handler.deps_style is None

    def test_source_handler_unknown(self):
        """Test that unknown suffixes return None."""
        tc = ClangClToolchain()
        handler = tc.get_source_handler(".xyz")
        assert handler is None


class TestClangClCompileFlagsForTargetType:
    """Tests for get_compile_flags_for_target_type method."""

    def test_shared_library_no_flags(self):
        """Clang-CL doesn't need special compile flags for shared libraries."""
        tc = ClangClToolchain()
        flags = tc.get_compile_flags_for_target_type("shared_library")
        assert flags == []

    def test_static_library_no_flags(self):
        """Static libraries don't need special flags."""
        tc = ClangClToolchain()
        flags = tc.get_compile_flags_for_target_type("static_library")
        assert flags == []

    def test_program_no_flags(self):
        """Programs don't need special flags."""
        tc = ClangClToolchain()
        flags = tc.get_compile_flags_for_target_type("program")
        assert flags == []


class TestClangClAuxiliaryInputHandler:
    """Tests for the AuxiliaryInputHandler support in Clang-CL toolchain."""

    def test_auxiliary_input_handler_def(self):
        """Test that .def files are recognized as auxiliary inputs."""
        tc = ClangClToolchain()
        handler = tc.get_auxiliary_input_handler(".def")
        assert handler is not None
        assert handler.suffix == ".def"
        assert handler.flag_template == "/DEF:$file"
        assert handler.tool == "link"

    def test_auxiliary_input_handler_def_case_insensitive(self):
        """Test that .DEF files are also recognized (case insensitive)."""
        tc = ClangClToolchain()
        handler = tc.get_auxiliary_input_handler(".DEF")
        assert handler is not None
        assert handler.suffix == ".def"

    def test_auxiliary_input_handler_c_not_auxiliary_input(self):
        """Test that .c files are not auxiliary inputs."""
        tc = ClangClToolchain()
        handler = tc.get_auxiliary_input_handler(".c")
        assert handler is None

    def test_auxiliary_input_handler_unknown(self):
        """Test that unknown suffixes return None."""
        tc = ClangClToolchain()
        handler = tc.get_auxiliary_input_handler(".xyz")
        assert handler is None

    def test_auxiliary_input_handler_manifest(self):
        """Test that .manifest files are recognized as auxiliary inputs."""
        tc = ClangClToolchain()
        handler = tc.get_auxiliary_input_handler(".manifest")
        assert handler is not None
        assert handler.suffix == ".manifest"
        assert handler.flag_template == "/MANIFESTINPUT:$file"
        assert handler.tool == "link"

    def test_auxiliary_input_handler_manifest_case_insensitive(self):
        """Test that .MANIFEST files are also recognized (case insensitive)."""
        tc = ClangClToolchain()
        handler = tc.get_auxiliary_input_handler(".MANIFEST")
        assert handler is not None
        assert handler.suffix == ".manifest"
