# SPDX-License-Identifier: MIT
"""Tests for pcons.toolchains.msvc."""

from pathlib import Path

import pytest

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
    _find_msvc_bin_dir,
    _host_arch_dirs,
    _sorted_version_dirs,
    _version_sort_key,
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

    def test_builder_has_msvc_deps_style(self, test_project):  # noqa: F811
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

    def test_shared_library_returns_output_group(self, test_project):  # noqa: F811
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

    def test_resource_builder_creates_node(self, test_project):  # noqa: F811
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
        assert target.path == Path("build/app.res")

    def test_resource_builder_no_depfile(self, test_project):  # noqa: F811
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

    @pytest.mark.parametrize("suffix", [".cppm", ".ixx", ".cxxm", ".c++m"])
    def test_source_handler_module_interface(self, suffix):
        """All recognized C++20 module-interface extensions map to cxx_module."""
        tc = MsvcToolchain()
        handler = tc.get_source_handler(suffix)
        assert handler is not None
        assert handler.tool_name == "cxx"
        assert handler.language == "cxx_module"
        assert handler.object_suffix == ".obj"
        # No make-style depfile (cl.exe doesn't emit one) — but deps_style is
        # "msvc" so ninja still parses /showIncludes to track #includes inside
        # the module's global module fragment. Inter-module ordering is
        # handled by the cxx_modules.dyndep file.
        assert handler.depfile is None
        assert handler.deps_style == "msvc"


class TestMsvcModulesBmiKeying:
    """after_resolve keys each TU by a hash of its BMI-sensitive flags and
    records the key in the scope manifest; the collate resolves IFC paths
    from those keys at build time, so the same logical module compiled with
    incompatible flags (e.g. /std:c++23 vs /std:c++latest) lands in
    distinct <moddir>/<key>/ directories instead of colliding on one path.
    """

    def _scanned_project(self, tmp_path, monkeypatch, flags_a, flags_b):
        import json
        from types import SimpleNamespace

        from pcons.core.project import Project
        from pcons.core.target import Target

        monkeypatch.chdir(tmp_path)
        tc = MsvcToolchain()
        project = Project("bmi", root_dir=tmp_path, build_dir="build")
        env = SimpleNamespace(
            cxx=SimpleNamespace(
                cmd="cl.exe", flags=["/nologo"], defines=[], dprefix="/D"
            ),
            register_node=lambda _node: None,
            toolchains=(tc,),
            name=None,
        )

        def make_obj(path, flags):
            src = FileNode(
                tmp_path / ("provider.cppm" if "provider" in path else "consumer.cpp")
            )
            obj = FileNode(path)
            obj._build_info = {
                "env": env,
                "sources": [src],
                "context": SimpleNamespace(flags=list(flags), includes=[], defines=[]),
            }
            return obj

        def make_target(name, provider, consumer):
            target = Target(name, target_type="static_library", project=project)
            target._env = env  # type: ignore[assignment]
            target.intermediate_nodes.extend([provider, consumer])
            project._targets.append(target)
            return target

        prov_a = make_obj("build/obj.lib1/provider.cppm.obj", flags_a)
        cons_a = make_obj("build/obj.lib1/consumer.cpp.obj", flags_a)
        prov_b = make_obj("build/obj.lib3/provider.cppm.obj", flags_b)
        cons_b = make_obj("build/obj.lib3/consumer.cpp.obj", flags_b)
        make_target("lib1", prov_a, cons_a)
        make_target("lib3", prov_b, cons_b)

        monkeypatch.setattr(tc, "_setup_std_modules", lambda *_a, **_k: ({}, None))
        tc.after_resolve(
            project,
            {
                "cxx_module": [
                    (tmp_path / "provider.cppm", prov_a),
                    (tmp_path / "provider.cppm", prov_b),
                ],
                "cxx": [
                    (tmp_path / "consumer.cpp", cons_a),
                    (tmp_path / "consumer.cpp", cons_b),
                ],
            },
        )
        from pcons.core.scan import ScannerResolver

        ScannerResolver(project).run(project.targets)

        def manifest(scope):
            path = (
                tmp_path / "build" / "scan" / "cxx-modules" / f"{scope}.manifest.json"
            )
            return json.loads(path.read_text())

        return manifest("bmi.lib1"), manifest("bmi.lib3"), (prov_a, cons_a)

    @staticmethod
    def _keys(manifest):
        return {e["out"]: e["extra"]["key"] for e in manifest["edges"]}

    def test_incompatible_dialects_get_separate_keys(self, tmp_path, monkeypatch):
        m1, m3, _ = self._scanned_project(
            tmp_path, monkeypatch, ["/std:c++23"], ["/std:c++latest"]
        )

        k1 = self._keys(m1)
        k3 = self._keys(m3)
        # Within a target, provider and consumer share a key; across the
        # dialect boundary the keys differ, so the collate resolves each
        # consumer against its own class's IFC.
        assert k1["obj.lib1/provider.cppm.obj"] == k1["obj.lib1/consumer.cpp.obj"]
        assert k3["obj.lib3/provider.cppm.obj"] == k3["obj.lib3/consumer.cpp.obj"]
        assert k1["obj.lib1/provider.cppm.obj"] != k3["obj.lib3/provider.cppm.obj"]
        # Per-scope BMI directories make cross-scope collisions impossible.
        assert m1["extra"]["moddir"] == "cxx_modules/bmi.lib1"
        assert m3["extra"]["moddir"] == "cxx_modules/bmi.lib3"
        assert m1["extra"]["style"] == "msvc"
        assert m1["extra"]["bmi_ext"] == ".ifc"

    def test_compatible_compiles_share_one_key(self, tmp_path, monkeypatch):
        m1, m3, _ = self._scanned_project(
            tmp_path, monkeypatch, ["/std:c++23"], ["/std:c++23"]
        )

        assert (
            self._keys(m1)["obj.lib1/provider.cppm.obj"]
            == self._keys(m3)["obj.lib3/provider.cppm.obj"]
        )

    def test_scanned_tus_compile_with_the_modmap_template(self, tmp_path, monkeypatch):
        _m1, _m3, (prov, cons) = self._scanned_project(
            tmp_path, monkeypatch, ["/std:c++23"], ["/std:c++23"]
        )

        for obj in (prov, cons):
            bi = obj._build_info
            assert bi is not None
            assert bi["command_var"] == "modobjcmd"
            assert bi["vars"]["CXX_MODMAPREF"].startswith("@")
            assert bi["vars"]["CXX_MODMAPREF"].endswith(".modmap")
        # /TP is the suffix-static pre-flag on the interface unit alone.
        assert "/TP" in prov._build_info["context"].flags
        assert "/TP" not in cons._build_info["context"].flags


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

    def test_auxiliary_input_handler_def(self, test_project):  # noqa: F811
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

    def test_auxiliary_input_paths_are_path_flags(self):
        """/MANIFESTINPUT: and /DEF: take node paths, so generators
        must make them execution-relative, not source-relative."""
        tc = MsvcToolchain()
        assert "/MANIFESTINPUT:" in tc.get_path_flags()
        assert "/DEF:" in tc.get_path_flags()

    def test_auxiliary_input_handler_manifest_case_insensitive(self):
        """Test that .MANIFEST files are also recognized (case insensitive)."""
        tc = MsvcToolchain()
        handler = tc.get_auxiliary_input_handler(".MANIFEST")
        assert handler is not None
        assert handler.suffix == ".manifest"


class TestVersionSortKey:
    """_version_sort_key parses MSVC/SDK version directory names for
    numeric (not lexicographic) comparison."""

    def test_parses_msvc_tool_version(self):
        assert _version_sort_key("14.38.33130") == (14, 38, 33130)

    def test_parses_sdk_version(self):
        assert _version_sort_key("10.0.22621.0") == (10, 0, 22621, 0)

    def test_rejects_non_numeric_component(self):
        assert _version_sort_key("14.38.33130-preview") is None

    def test_rejects_empty_string(self):
        assert _version_sort_key("") is None


class TestSortedVersionDirs:
    """_sorted_version_dirs must sort numerically, not lexicographically,
    so e.g. 14.10 (newer) beats 14.9, which a plain string sort gets wrong."""

    def test_numeric_ordering_beats_lexicographic(self, tmp_path):
        for name in ("14.9.0", "14.10.0", "14.2.0"):
            (tmp_path / name).mkdir()

        dirs = _sorted_version_dirs(tmp_path)

        assert [d.name for d in dirs] == ["14.10.0", "14.9.0", "14.2.0"]

    def test_realistic_sdk_versions_sort_numerically(self, tmp_path):
        # Lexicographically "10.0.9200.0" > "10.0.22621.0" (character '9' >
        # '2'), but 22621 is the newer SDK build.
        for name in ("10.0.9200.0", "10.0.22621.0", "10.0.19041.0"):
            (tmp_path / name).mkdir()

        dirs = _sorted_version_dirs(tmp_path)

        assert dirs[0].name == "10.0.22621.0"

    def test_garbage_directories_are_skipped_not_crashed(self, tmp_path):
        (tmp_path / "14.38.33130").mkdir()
        (tmp_path / "not-a-version").mkdir()
        (tmp_path / "some_file.txt").write_text("not a dir")

        dirs = _sorted_version_dirs(tmp_path)

        assert [d.name for d in dirs] == ["14.38.33130"]


class TestHostArchDirs:
    """_host_arch_dirs must reflect the detected host, not hardcode x64,
    so ARM64 (and other) hosts get the right Host<ARCH>/<arch> bin dir."""

    @pytest.mark.parametrize(
        "machine,expected",
        [
            ("AMD64", ("Hostx64", "x64")),
            ("x86_64", ("Hostx64", "x64")),
            ("ARM64", ("HostARM64", "arm64")),
            ("aarch64", ("HostARM64", "arm64")),
        ],
    )
    def test_detected_host(self, monkeypatch, machine, expected):
        monkeypatch.setattr("platform.machine", lambda: machine)
        assert _host_arch_dirs() == expected


class TestFindMsvcBinDirHostAware:
    """_find_msvc_bin_dir (and the vswhere fallbacks in MsvcCompiler.configure
    / MsvcAssembler.configure that delegate to it) must pick the bin dir
    matching the running host's architecture, not a hardcoded Hostx64/x64."""

    def test_picks_arm64_host_dir_on_arm64_host(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pcons.toolchains.msvc._find_msvc_install", lambda: tmp_path
        )
        monkeypatch.setattr("platform.machine", lambda: "ARM64")

        vc_tools = tmp_path / "VC" / "Tools" / "MSVC" / "14.40.12345"
        arm_bin = vc_tools / "bin" / "HostARM64" / "arm64"
        arm_bin.mkdir(parents=True)
        (arm_bin / "cl.exe").touch()
        # An x64 host dir also exists (e.g. a shared VS install) — it must
        # NOT be picked when the running host is ARM64.
        x64_bin = vc_tools / "bin" / "Hostx64" / "x64"
        x64_bin.mkdir(parents=True)
        (x64_bin / "cl.exe").touch()

        assert _find_msvc_bin_dir() == arm_bin

    def test_picks_x64_host_dir_on_x64_host(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pcons.toolchains.msvc._find_msvc_install", lambda: tmp_path
        )
        monkeypatch.setattr("platform.machine", lambda: "AMD64")

        vc_tools = tmp_path / "VC" / "Tools" / "MSVC" / "14.40.12345"
        x64_bin = vc_tools / "bin" / "Hostx64" / "x64"
        x64_bin.mkdir(parents=True)
        (x64_bin / "cl.exe").touch()

        assert _find_msvc_bin_dir() == x64_bin

    def test_picks_newest_version_numerically(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pcons.toolchains.msvc._find_msvc_install", lambda: tmp_path
        )
        monkeypatch.setattr("platform.machine", lambda: "AMD64")

        vc_tools = tmp_path / "VC" / "Tools" / "MSVC"
        for version in ("14.9.0", "14.10.0"):
            bin_dir = vc_tools / version / "bin" / "Hostx64" / "x64"
            bin_dir.mkdir(parents=True)
            (bin_dir / "cl.exe").touch()

        result = _find_msvc_bin_dir()

        assert result is not None
        assert result.parent.parent.parent.name == "14.10.0"
