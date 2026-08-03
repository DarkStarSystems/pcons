# SPDX-License-Identifier: MIT
"""Unit tests for pcons.tools.compile_link helpers."""

from __future__ import annotations

from pathlib import Path

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
