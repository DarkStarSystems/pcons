# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.msvc."""

from pathlib import Path

from pcons.configure.platform import get_platform
from pcons.core.builder import MultiOutputBuilder, OutputGroup
from pcons.core.environment import Environment
from pcons.core.node import FileNode
from pcons.core.subst import SourcePath, TargetPath
from pcons.toolchains.msvc import (
    MsvcAssembler,
    MsvcCompiler,
    MsvcLibrarian,
    MsvcLinker,
    MsvcResourceCompiler,
    MsvcToolchain,
)


class TestMsvcCompiler:
    def test_creation(self):
        cc = MsvcCompiler()
        assert cc.name == "cc"
        assert cc.language == "c"

    def test_creation_with_name(self):
        cxx = MsvcCompiler(name="cxx", language="cxx")
        assert cxx.name == "cxx"
        assert cxx.language == "cxx"

    def test_default_vars(self):
        cc = MsvcCompiler()
        vars = cc.default_vars()
        assert vars["cmd"] == "cl.exe"
        assert vars["flags"] == ["/nologo"]
        assert vars["includes"] == []
        assert vars["defines"] == []
        assert "objcmd" in vars
        # objcmd is now a list template
        objcmd = vars["objcmd"]
        assert isinstance(objcmd, list)
        assert "$cc.cmd" in objcmd
        assert "/c" in objcmd
        # Output uses TargetPath marker which generators convert to native syntax
        assert TargetPath(prefix="/Fo") in objcmd

    def test_depflags(self):
        cc = MsvcCompiler()
        vars = cc.default_vars()
        assert "depflags" in vars
        assert vars["depflags"] == ["/showIncludes"]
        # Verify depflags is in objcmd
        objcmd = vars["objcmd"]
        assert "$cc.depflags" in objcmd

    def test_builder_has_msvc_deps_style(self):
        cc = MsvcCompiler()
        builders = cc.builders()
        obj_builder = builders["Object"]
        # Create a mock environment and build a target to check build_info
        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "cl.exe"
        env.cc.objcmd = ["cl.exe", "/c", TargetPath(prefix="/Fo"), SourcePath()]
        result = obj_builder(env, "test.obj", ["test.c"])
        assert len(result) == 1
        target = result[0]
        assert isinstance(target, FileNode)
        assert target._build_info is not None
        assert target._build_info.get("deps_style") == "msvc"
        # MSVC doesn't use a depfile (uses stdout)
        assert target._build_info.get("depfile") is None

    def test_builders(self):
        cc = MsvcCompiler()
        builders = cc.builders()
        assert "Object" in builders
        obj_builder = builders["Object"]
        assert obj_builder.name == "Object"
        assert ".c" in obj_builder.src_suffixes
        assert ".cpp" in obj_builder.src_suffixes
        assert ".obj" in obj_builder.target_suffixes


class TestMsvcLibrarian:
    def test_creation(self):
        lib = MsvcLibrarian()
        assert lib.name == "lib"

    def test_default_vars(self):
        lib = MsvcLibrarian()
        vars = lib.default_vars()
        assert vars["cmd"] == "lib.exe"
        assert vars["flags"] == ["/nologo"]
        assert "libcmd" in vars
        # libcmd is now a list template
        libcmd = vars["libcmd"]
        assert isinstance(libcmd, list)
        # Output uses TargetPath marker which becomes $out for ninja
        assert TargetPath(prefix="/OUT:") in libcmd

    def test_builders(self):
        lib = MsvcLibrarian()
        builders = lib.builders()
        assert "StaticLibrary" in builders
        lib_builder = builders["StaticLibrary"]
        assert ".obj" in lib_builder.src_suffixes
        assert ".lib" in lib_builder.target_suffixes
        assert lib_builder.name == "StaticLibrary"


class TestMsvcLinker:
    def test_creation(self):
        link = MsvcLinker()
        assert link.name == "link"

    def test_default_vars(self):
        link = MsvcLinker()
        vars = link.default_vars()
        assert vars["cmd"] == "link.exe"
        assert vars["flags"] == ["/nologo"]
        assert vars["libs"] == []
        assert vars["libdirs"] == []
        assert "progcmd" in vars
        assert "sharedcmd" in vars
        # progcmd and sharedcmd are now list templates
        progcmd = vars["progcmd"]
        sharedcmd = vars["sharedcmd"]
        assert isinstance(progcmd, list)
        assert isinstance(sharedcmd, list)
        # Output uses TargetPath marker which becomes $out for ninja
        assert TargetPath(prefix="/OUT:") in progcmd
        assert "/DLL" in sharedcmd

    def test_builders(self):
        link = MsvcLinker()
        builders = link.builders()
        assert "Program" in builders
        assert "SharedLibrary" in builders
        prog_builder = builders["Program"]
        assert ".exe" in prog_builder.target_suffixes
        shared_builder = builders["SharedLibrary"]
        assert ".dll" in shared_builder.target_suffixes

    def test_shared_library_is_multi_output_builder(self):
        link = MsvcLinker()
        builders = link.builders()
        shared_builder = builders["SharedLibrary"]
        assert isinstance(shared_builder, MultiOutputBuilder)

    def test_shared_library_outputs(self):
        link = MsvcLinker()
        builders = link.builders()
        shared_builder = builders["SharedLibrary"]

        # Check output specs
        outputs = shared_builder.outputs
        assert len(outputs) == 3

        # Primary should be .dll
        assert outputs[0].name == "primary"
        assert outputs[0].suffix == ".dll"
        assert outputs[0].implicit is False

        # Import lib should be .lib
        assert outputs[1].name == "import_lib"
        assert outputs[1].suffix == ".lib"
        assert outputs[1].implicit is False

        # Export file should be .exp and implicit
        assert outputs[2].name == "export_file"
        assert outputs[2].suffix == ".exp"
        assert outputs[2].implicit is True

    def test_shared_library_returns_output_group(self):
        link = MsvcLinker()
        builders = link.builders()
        shared_builder = builders["SharedLibrary"]

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.flags = ["/nologo"]
        env.link.sharedcmd = [
            "$link.cmd",
            "/DLL",
            TargetPath(prefix="/OUT:"),
            SourcePath(),
        ]

        result = shared_builder(env, "build/mylib.dll", ["a.obj", "b.obj"])

        assert isinstance(result, OutputGroup)
        assert result.primary.path == Path("build/mylib.dll")
        assert result.import_lib.path == Path("build/mylib.lib")
        assert result.export_file.path == Path("build/mylib.exp")

    def test_sharedcmd_includes_implib(self):
        link = MsvcLinker()
        vars = link.default_vars()
        sharedcmd = vars["sharedcmd"]
        # Check that IMPLIB flag is in the command
        assert any("/IMPLIB:" in str(item) for item in sharedcmd)


class TestMsvcToolchain:
    def test_creation(self):
        tc = MsvcToolchain()
        assert tc.name == "msvc"

    def test_tools_empty_before_configure(self):
        tc = MsvcToolchain()
        # Tools should be empty before configure
        assert tc.tools == {}

    def test_configure_returns_false_on_non_windows(self):
        platform = get_platform()
        if not platform.is_windows:
            tc = MsvcToolchain()

            # Create a mock config object
            class MockConfig:
                pass

            # Should return False on non-Windows
            result = tc._configure_tools(MockConfig())
            assert result is False


class TestMsvcCompileFlagsForTargetType:
    """Tests for get_compile_flags_for_target_type method."""

    def test_shared_library_no_flags(self):
        """MSVC doesn't need special compile flags for shared libraries."""
        tc = MsvcToolchain()
        flags = tc.get_compile_flags_for_target_type("shared_library")
        # MSVC uses __declspec(dllexport) in code, not compiler flags
        assert flags == []

    def test_static_library_no_flags(self):
        """Static libraries don't need special flags."""
        tc = MsvcToolchain()
        flags = tc.get_compile_flags_for_target_type("static_library")
        assert flags == []

    def test_program_no_flags(self):
        """Programs don't need special flags."""
        tc = MsvcToolchain()
        flags = tc.get_compile_flags_for_target_type("program")
        assert flags == []

    def test_interface_no_flags(self):
        """Interface targets don't need special flags."""
        tc = MsvcToolchain()
        flags = tc.get_compile_flags_for_target_type("interface")
        assert flags == []


class TestMsvcResourceCompiler:
    def test_creation(self):
        rc = MsvcResourceCompiler()
        assert rc.name == "rc"

    def test_default_vars(self):
        rc = MsvcResourceCompiler()
        vars = rc.default_vars()
        assert vars["cmd"] == "rc.exe"
        assert vars["flags"] == ["/nologo"]
        assert vars["includes"] == []
        assert vars["defines"] == []
        assert "rccmd" in vars
        # rccmd is a list template
        rccmd = vars["rccmd"]
        assert isinstance(rccmd, list)
        assert "$rc.cmd" in rccmd
        # Output uses TargetPath marker which becomes $out for ninja
        assert TargetPath(prefix="/fo") in rccmd

    def test_builders(self):
        rc = MsvcResourceCompiler()
        builders = rc.builders()
        assert "Resource" in builders
        res_builder = builders["Resource"]
        assert res_builder.name == "Resource"
        assert ".rc" in res_builder.src_suffixes
        assert ".res" in res_builder.target_suffixes

    def test_resource_builder_creates_node(self):
        rc = MsvcResourceCompiler()
        builders = rc.builders()
        res_builder = builders["Resource"]

        env = Environment()
        env.add_tool("rc")
        env.rc.cmd = "rc.exe"
        env.rc.rccmd = ["rc.exe", "/nologo", TargetPath(prefix="/fo"), SourcePath()]

        result = res_builder(env, "app.res", ["app.rc"])
        assert len(result) == 1
        target = result[0]
        assert isinstance(target, FileNode)
        assert target.path == Path("app.res")

    def test_resource_builder_no_depfile(self):
        """Resource compiler doesn't generate depfiles."""
        rc = MsvcResourceCompiler()
        builders = rc.builders()
        res_builder = builders["Resource"]

        env = Environment()
        env.add_tool("rc")
        env.rc.cmd = "rc.exe"
        env.rc.rccmd = ["rc.exe", "/nologo", TargetPath(prefix="/fo"), SourcePath()]

        result = res_builder(env, "app.res", ["app.rc"])
        assert len(result) == 1
        target = result[0]
        assert isinstance(target, FileNode)
        assert target._build_info is not None
        # No depfile for resource files
        assert target._build_info.get("depfile") is None
        assert target._build_info.get("deps_style") is None


class TestMsvcAssembler:
    """Tests for the MASM assembler tool."""

    def test_creation(self):
        ml = MsvcAssembler()
        assert ml.name == "ml"

    def test_default_vars(self):
        ml = MsvcAssembler()
        vars = ml.default_vars()
        assert vars["cmd"] == "ml64.exe"
        assert vars["flags"] == ["/nologo"]
        assert "asmcmd" in vars
        asmcmd = vars["asmcmd"]
        assert isinstance(asmcmd, list)
        assert "$ml.cmd" in asmcmd
        assert "/c" in asmcmd

    def test_builders(self):
        ml = MsvcAssembler()
        builders = ml.builders()
        assert "AsmObject" in builders
        asm_builder = builders["AsmObject"]
        assert asm_builder.name == "AsmObject"
        assert ".asm" in asm_builder.src_suffixes
        assert ".obj" in asm_builder.target_suffixes


class TestMsvcSourceHandlers:
    def test_source_handler_rc(self):
        """Test that .rc files are handled by the resource compiler."""
        tc = MsvcToolchain()
        handler = tc.get_source_handler(".rc")
        assert handler is not None
        assert handler.tool_name == "rc"
        assert handler.language == "resource"
        assert handler.object_suffix == ".res"
        assert handler.deps_style is None  # No depfile support

    def test_source_handler_c(self):
        """Test that .c files are still handled correctly."""
        tc = MsvcToolchain()
        handler = tc.get_source_handler(".c")
        assert handler is not None
        assert handler.tool_name == "cc"

    def test_source_handler_cpp(self):
        """Test that .cpp files are still handled correctly."""
        tc = MsvcToolchain()
        handler = tc.get_source_handler(".cpp")
        assert handler is not None
        assert handler.tool_name == "cxx"

    def test_source_handler_asm(self):
        """Test that .asm files are handled by the MASM assembler."""
        tc = MsvcToolchain()
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
        tc = MsvcToolchain()
        handler = tc.get_source_handler(".xyz")
        assert handler is None


class TestMsvcLinkerAcceptsRes:
    def test_program_builder_accepts_res(self):
        """Test that Program builder accepts .res files."""
        link = MsvcLinker()
        builders = link.builders()
        prog_builder = builders["Program"]
        assert ".res" in prog_builder.src_suffixes

    def test_shared_library_builder_accepts_res(self):
        """Test that SharedLibrary builder accepts .res files."""
        link = MsvcLinker()
        builders = link.builders()
        shared_builder = builders["SharedLibrary"]
        assert ".res" in shared_builder.src_suffixes


class TestMsvcAuxiliaryInputHandler:
    """Tests for the AuxiliaryInputHandler support in MSVC toolchain."""

    def test_auxiliary_input_handler_def(self):
        """Test that .def files are recognized as auxiliary inputs."""
        tc = MsvcToolchain()
        handler = tc.get_auxiliary_input_handler(".def")
        assert handler is not None
        assert handler.suffix == ".def"
        assert handler.flag_template == "/DEF:$file"
        assert handler.tool == "link"

    def test_auxiliary_input_handler_def_case_insensitive(self):
        """Test that .DEF files are also recognized (case insensitive)."""
        tc = MsvcToolchain()
        handler = tc.get_auxiliary_input_handler(".DEF")
        assert handler is not None
        assert handler.suffix == ".def"

    def test_auxiliary_input_handler_c_not_auxiliary_input(self):
        """Test that .c files are not auxiliary inputs."""
        tc = MsvcToolchain()
        handler = tc.get_auxiliary_input_handler(".c")
        assert handler is None

    def test_auxiliary_input_handler_unknown(self):
        """Test that unknown suffixes return None."""
        tc = MsvcToolchain()
        handler = tc.get_auxiliary_input_handler(".xyz")
        assert handler is None

    def test_auxiliary_input_handler_manifest(self):
        """Test that .manifest files are recognized as auxiliary inputs."""
        tc = MsvcToolchain()
        handler = tc.get_auxiliary_input_handler(".manifest")
        assert handler is not None
        assert handler.suffix == ".manifest"
        assert handler.flag_template == "/MANIFESTINPUT:$file"
        assert handler.tool == "link"

    def test_auxiliary_input_handler_manifest_case_insensitive(self):
        """Test that .MANIFEST files are also recognized (case insensitive)."""
        tc = MsvcToolchain()
        handler = tc.get_auxiliary_input_handler(".MANIFEST")
        assert handler is not None
        assert handler.suffix == ".manifest"
