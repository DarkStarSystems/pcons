# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.clang_cl."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import pcons.toolchains.clang_cl as clang_cl_mod
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
        # $cc.flags brings it to the command line, so the command has none.
        assert "/nologo" not in vars["objcmd"]
        assert "$cc.flags" in vars["objcmd"]


class TestClangClCxxCompiler:
    def test_creation(self):
        cxx = ClangClCxxCompiler()
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cxx = ClangClCxxCompiler()
        vars = cxx.default_vars()
        assert vars["cmd"] == "clang-cl"
        assert "/nologo" not in vars["objcmd"]
        assert "$cxx.cmd" in vars["objcmd"]
        assert "$cxx.flags" in vars["objcmd"]


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


class TestClangClLtoPreset:
    """clang-cl LTO is -flto bitcode, not MSVC's /GL (which it ignores)."""

    def test_lto_is_realized_the_clang_way(self):
        from pcons.toolchains.clang_cl import ClangClToolchain
        from pcons.toolchains.msvc import MsvcToolchain

        assert ClangClToolchain.FEATURE_PRESETS["lto"] == {
            "compile_flags": ["-flto"],
        }
        # MSVC keeps its own spelling.
        assert MsvcToolchain.FEATURE_PRESETS["lto"]["compile_flags"] == ["/GL"]

    def test_other_presets_stay_shared(self):
        from pcons.toolchains._msvc_compat import MsvcCompatibleToolchain
        from pcons.toolchains.clang_cl import ClangClToolchain

        base = MsvcCompatibleToolchain.FEATURE_PRESETS
        ours = ClangClToolchain.FEATURE_PRESETS
        assert {k: v for k, v in ours.items() if k != "lto"} == {
            k: v for k, v in base.items() if k != "lto"
        }


class TestClangClDriverRuntimes:
    """clang-cl's AddressSanitizer and OpenMP objects don't name their
    runtime, so its link needs are what clang-cl's own driver would add."""

    @staticmethod
    def _fake_driver(monkeypatch, link_line) -> None:
        """subprocess.run answering ``-###`` with *link_line(flags, obj)*,
        or failing when that returns None."""
        import subprocess

        def run(cmd, **kwargs):
            flags, obj = cmd[1:-2], cmd[-1]
            line = link_line(flags, obj)
            if line is None:
                return subprocess.CompletedProcess(cmd, 1, "", "error: not allowed")
            return subprocess.CompletedProcess(
                cmd, 0, "", f'clang version 19.1.5\n "cc1" ...\n{line}\n'
            )

        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr(clang_cl_mod, "driver_link_args", _uncached())

    def test_the_drivers_link_arguments_are_read(self, monkeypatch):
        def link_line(flags, obj):
            esc = obj.replace("\\", "\\\\")
            return (
                ' "C:\\\\VS\\\\link.exe" "-out:probe.exe" "-nologo"'
                ' "C:\\\\LLVM\\\\lib\\\\clang_rt.asan_dynamic-x86_64.lib"'
                ' "-include:__asan_seh_interceptor"'
                f' "{esc}" "/INFERASANLIBS:NO"'
            )

        self._fake_driver(monkeypatch, link_line)
        assert clang_cl_mod.driver_link_args("clang-cl", ("/MD",)) == (
            "-nologo",
            "C:\\LLVM\\lib\\clang_rt.asan_dynamic-x86_64.lib",
            "-include:__asan_seh_interceptor",
            "/INFERASANLIBS:NO",
        )

    def test_a_feature_adds_what_the_driver_adds(self, monkeypatch):
        """The AddressSanitizer runtime is a different library for /MD and
        /MT, and a debug C runtime is refused."""

        def link_line(flags, obj):
            if "-fsanitize=address" in flags and ("/MDd" in flags or "/MTd" in flags):
                return None
            line = ' "link.exe" "-out:p.exe" "-libpath:C:\\\\LLVM\\\\lib"'
            if "/LD" in flags:
                line += ' "-dll"'
            if "-fsanitize=address" in flags:
                asan = "asan_dynamic" if "/MD" in flags else "asan"
                if "/MT" in flags and "/LD" in flags:
                    asan = "asan_dll_thunk"
                line += f' "clang_rt.{asan}-x86_64.lib" "/INFERASANLIBS:NO"'
            return line

        self._fake_driver(monkeypatch, link_line)
        add = clang_cl_mod.driver_link_additions
        san = "-fsanitize=address"
        assert add("clang-cl", ("/MD",), san) == (
            "clang_rt.asan_dynamic-x86_64.lib",
            "/INFERASANLIBS:NO",
        )
        assert add("clang-cl", ("/MT",), san) == (
            "clang_rt.asan-x86_64.lib",
            "/INFERASANLIBS:NO",
        )
        assert add("clang-cl", ("/MT", "/LD"), san) == (
            "clang_rt.asan_dll_thunk-x86_64.lib",
            "/INFERASANLIBS:NO",
        )
        assert add("clang-cl", ("/MDd",), san) is None

    def test_the_linker_chooses_the_asan_runtime(self, monkeypatch, tmp_path):
        """clang-cl's objects need the AddressSanitizer runtime; its linker
        chooses which, for the C runtime and the kind of link."""
        from pcons import Project
        from pcons.toolchains.clang_cl import (
            ClangClCCompiler,
            ClangClCxxCompiler,
            ClangClLinker,
        )

        def additions(clang_cl, link, flag):
            if link[0].endswith("d"):
                return None
            return (f"asan-for-{'-'.join(link)}.lib",)

        monkeypatch.setattr(clang_cl_mod, "driver_link_additions", additions)
        toolchain = ClangClToolchain()
        toolchain._tools = {
            t.name: t
            for t in (ClangClCCompiler(), ClangClCxxCompiler(), ClangClLinker())
        }
        toolchain._configured = True
        env = Project("asan", root_dir=tmp_path).Environment(toolchain=toolchain)
        env.apply_preset("sanitize")
        program = SimpleNamespace(name="app", target_type="program", _env=env)
        dll = SimpleNamespace(name="lib", target_type="shared_library", _env=env)
        needs = toolchain.link_needs(program, {"cxx"}, "cxx", env)
        assert needs.sanitizers == ("address",)

        def link(target=program):
            return toolchain.realize_link_needs(needs, target, [])

        assert link() == ["asan-for-/MD.lib"]
        env.set_runtime_linkage("static")
        assert link() == ["asan-for-/MT.lib"]
        assert link(dll) == ["asan-for-/MT-/LD.lib"]
        env.set_variant("debug")
        with pytest.raises(ValueError, match="relwithdebinfo"):
            link()

    def test_link_exe_links_its_own_asan_runtime(self):
        """For objects that don't name it, as ifx's don't."""
        from pcons.toolchains.msvc import MsvcToolchain
        from pcons.tools.toolchain import LinkNeeds

        target = SimpleNamespace(name="app", target_type="program")
        needs = LinkNeeds(sanitizers=("address",))
        assert MsvcToolchain().realize_link_needs(needs, target, []) == [
            "/INFERASANLIBS"
        ]

    def test_openmp_links_what_the_driver_adds(self, monkeypatch, tmp_path):
        """clang-cl's /openmp objects name no runtime; the link gets the
        libomp arguments its driver would add, or raises without them."""
        from pcons import Project
        from pcons.toolchains.clang_cl import (
            ClangClCCompiler,
            ClangClCxxCompiler,
            ClangClLinker,
        )

        omp = (
            "-nodefaultlib:vcomp.lib",
            "-libpath:C:\\LLVM\\lib",
            "-defaultlib:libomp.lib",
        )
        monkeypatch.setattr(
            clang_cl_mod, "driver_link_additions", lambda clang_cl, link, flag: omp
        )
        toolchain = ClangClToolchain()
        toolchain._tools = {
            t.name: t
            for t in (ClangClCCompiler(), ClangClCxxCompiler(), ClangClLinker())
        }
        toolchain._configured = True
        env = Project("omp", root_dir=tmp_path).Environment(toolchain=toolchain)
        env.apply_preset("openmp")
        assert "/openmp" in env.cxx.flags
        program = SimpleNamespace(name="app", target_type="program", _env=env)
        needs = toolchain.link_needs(program, {"cxx"}, "cxx", env)
        assert needs.libs == ("libomp",)
        assert needs.libdirs == ("C:\\LLVM\\lib",)
        assert needs.excluded_default_libs == ("vcomp.lib",)
        monkeypatch.setattr(
            clang_cl_mod, "driver_link_additions", lambda clang_cl, link, flag: None
        )
        with pytest.raises(ValueError, match="libomp"):
            toolchain.link_needs(program, {"cxx"}, "cxx", env)


def _uncached():
    """driver_link_args without its cache, so each test probes afresh."""
    return clang_cl_mod.driver_link_args.__wrapped__


def test_a_driver_argument_with_no_neutral_form_is_an_error() -> None:
    with pytest.raises(ValueError, match="-wholearchive"):
        clang_cl_mod.link_needs_from_args(("-wholearchive:foo.lib",))
