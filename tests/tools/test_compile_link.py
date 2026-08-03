# SPDX-License-Identifier: MIT
"""Unit tests for pcons.tools.compile_link helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcons.core.node import FileNode
from pcons.tools.compile_link import _is_link_input, _propagate_declared_deps


class TestIsLinkInput:
    """`_is_link_input` classifies a dep output as linker-ready or not.

    The split matters when a code-generator dep produces *both* a
    library and a sibling artifact (e.g., cargo + cbindgen produces a
    .a and a .h). The .a belongs on the link command line; the .h is
    a compile-time dep, not a link input.
    """

    def test_static_libs(self):
        assert _is_link_input(Path("libfoo.a"))
        assert _is_link_input(Path("foo.lib"))

    def test_shared_libs(self):
        assert _is_link_input(Path("libfoo.so"))
        assert _is_link_input(Path("libfoo.dylib"))
        assert _is_link_input(Path("foo.dll"))
        assert _is_link_input(Path("libfoo.tbd"))

    def test_versioned_shared_libs(self):
        # Versioned shared libs use multiple suffixes; the last suffix
        # is just the version number.
        assert _is_link_input(Path("libfoo.so.1"))
        assert _is_link_input(Path("libfoo.so.1.2.3"))
        assert _is_link_input(Path("libfoo.1.dylib"))

    def test_object_files(self):
        assert _is_link_input(Path("foo.o"))
        assert _is_link_input(Path("foo.obj"))

    def test_headers_are_not_link_inputs(self):
        assert not _is_link_input(Path("foo.h"))
        assert not _is_link_input(Path("foo.hpp"))
        assert not _is_link_input(Path("foo.hxx"))

    def test_misc_artifacts_are_not_link_inputs(self):
        assert not _is_link_input(Path("foo.txt"))
        assert not _is_link_input(Path("manifest.json"))
        assert not _is_link_input(Path("script.py"))


class TestDeclaredSourceDeps:
    """A source file's declared deps belong on its object; a *generated*
    source's do not.

    On a generated source, `explicit_deps` are the inputs of the edge that
    produces it. Copying those onto the consuming object rebuilds every
    consumer whenever the generator's input changes — even when the generated
    file came back byte-identical, which defeats restat/write_if_different.
    """

    def test_source_header_dep_propagates(self):
        source = FileNode(Path("src/main.c"))
        header = FileNode(Path("build/gen/config.h"))
        source.depends(header)
        obj = FileNode(Path("build/obj/main.c.o"))

        _propagate_declared_deps(source, obj)

        assert header in obj.implicit_deps

    def test_generated_source_producer_inputs_do_not_propagate(self):
        generated = FileNode(Path("build/gen/S_blur.c"))
        manifest = FileNode(Path("build/gen/plugins-list.txt"))
        generated.depends(manifest)
        generated._build_info = {"tool": "command"}
        obj = FileNode(Path("build/obj/S_blur.c.o"))

        _propagate_declared_deps(generated, obj)

        assert obj.implicit_deps == []


class TestObjectIdentity:
    """One object per (source, environment).

    Effective requirements deliberately exclude ``env.<tool>.flags``, so an
    environment carrying a define, an arch, or any other per-target flag is
    invisible to them. Keying objects on requirements alone made two targets
    share one object file, and the second link silently consumed the first's.
    """

    def test_different_environments_get_different_objects(self, tmp_path):
        from pcons.core.project import Project

        (tmp_path / "shared.c").write_text("int f(void){return 0;}\n")
        project = Project("ids", root_dir=tmp_path, build_dir="build")

        first = project.Environment(toolchain="c")
        first.cc.defines.append("VARIANT=1")
        second = project.Environment(toolchain="c")
        second.cc.defines.append("VARIANT=2")

        one = project.StaticLibrary("one", first, sources=["shared.c"])
        two = project.StaticLibrary("two", second, sources=["shared.c"])
        project.resolve()

        assert one.intermediate_nodes[0] is not two.intermediate_nodes[0]
        assert one.intermediate_nodes[0].path != two.intermediate_nodes[0].path

    def test_one_environment_still_shares_objects(self, tmp_path):
        """The sharing this cache exists for must survive the fix."""
        from pcons.core.project import Project

        (tmp_path / "shared.c").write_text("int f(void){return 0;}\n")
        project = Project("share", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c")

        one = project.StaticLibrary("one", env, sources=["shared.c"])
        two = project.StaticLibrary("two", env, sources=["shared.c"])
        project.resolve()

        assert one.intermediate_nodes[0] is two.intermediate_nodes[0]


class TestUnhandledSource:
    """A source no toolchain compiles must be an error, not a target (G22).

    Left alone, the source itself became one of the target's output nodes:
    ninja then reported a file sitting in the source tree as "missing and no
    known rule to make it", which points away from the real problem.
    """

    def test_unhandled_extension_raises(self, tmp_path):
        from pcons.core.errors import PconsError
        from pcons.core.project import Project

        (tmp_path / "k.cu").write_text("int k(void){return 0;}\n")
        project = Project("cuda", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c++")
        project.ObjectLibrary("k", env, sources=["k.cu"])

        with pytest.raises(PconsError) as excinfo:
            project.resolve()

        message = str(excinfo.value)
        assert "'.cu'" in message
        assert "k.cu" in message
        # Names the toolchain, what it does compile, and the escape hatch.
        assert ".cpp" in message
        assert ".Object(" in message

    def test_unhandled_extension_names_the_target_location(self, tmp_path):
        from pcons.core.errors import PconsError
        from pcons.core.project import Project

        (tmp_path / "k.cu").write_text("int k(void){return 0;}\n")
        project = Project("cuda", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c++")
        project.ObjectLibrary("k", env, sources=["k.cu"])

        with pytest.raises(PconsError) as excinfo:
            project.resolve()

        assert excinfo.value.location is not None
        assert "test_compile_link.py" in str(excinfo.value.location)

    def test_missing_tool_for_known_extension_raises(self, tmp_path):
        """A known extension whose tool isn't in the env used to only warn."""
        from pcons.core.errors import PconsError
        from pcons.core.project import Project

        (tmp_path / "a.cpp").write_text("int f(void){return 0;}\n")
        project = Project("notool", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c++")
        del env._tools["cxx"]
        project.ObjectLibrary("a", env, sources=["a.cpp"])

        with pytest.raises(PconsError) as excinfo:
            project.resolve()

        message = str(excinfo.value)
        assert "'cxx' tool" in message
        assert "add_tool" in message

    def test_handled_extension_still_compiles(self, tmp_path):
        from pcons.core.project import Project

        (tmp_path / "a.c").write_text("int f(void){return 0;}\n")
        project = Project("ok", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c")
        lib = project.ObjectLibrary("a", env, sources=["a.c"])
        project.resolve()

        assert len(lib.output_nodes) == 1
        assert lib.output_nodes[0].path.suffix in (".o", ".obj")

    def test_prebuilt_objects_and_linker_scripts_pass_through(self, tmp_path):
        """The passthrough the error must not break: link inputs."""
        from pcons.core.node import FileNode as FN
        from pcons.core.project import Project

        (tmp_path / "a.c").write_text("int main(void){return 0;}\n")
        project = Project("passthru", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c")
        prog = project.Program("app", env, sources=["a.c"])
        prog.add_sources([FN(tmp_path / "prebuilt.o"), FN(tmp_path / "custom.ld")])
        project.resolve()

        paths = [n.path for n in prog.intermediate_nodes]
        assert tmp_path / "prebuilt.o" in paths
        assert tmp_path / "custom.ld" in paths

    def test_escape_hatch_compiles_the_unhandled_source(self, tmp_path):
        """What the error tells you to do has to actually work."""
        from pcons.core.project import Project

        (tmp_path / "k.cu").write_text("int k(void){return 0;}\n")
        project = Project("hatch", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain="c++")

        obj = env.cxx.Object("k.cu")
        lib = project.ObjectLibrary("k", env, sources=[obj[0]])
        project.resolve()

        assert lib.output_nodes == [obj[0]]
        assert obj[0]._build_info["tool"] == "cxx"
