# SPDX-License-Identifier: MIT
"""A compiled target's objects go in a directory only it writes (#197).

The directory used to be keyed on the target's name alone, which accounts
for neither the kind of target nor the environment. Two targets landing on
one object path merge, because node deduplication maps a path to one node:
the second compile replaces the first and both link the result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pcons.core.errors import OutputCollisionError
from pcons.core.project import Project
from pcons.tools.compile_link import object_dir_name, target_kind_suffix


@pytest.fixture
def sources(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text("int a(void) { return 1; }\n")
    (src / "b.c").write_text("int b(void) { return 2; }\n")
    return src


def _object_dirs(target) -> set[str]:
    return {node.path.parent.as_posix() for node in target.intermediate_nodes}


class TestTheKindIsPartOfTheDirectory:
    """One rule: a program keeps obj.<name>/, every other kind adds a word."""

    @pytest.mark.parametrize(
        ("target_type", "expected"),
        [
            ("program", ""),
            ("static_library", ".static"),
            ("shared_library", ".shared"),
            ("object", ".object"),
            ("metal_library", ".metal"),
            (None, ""),
        ],
    )
    def test_the_suffix_comes_from_the_target_type(self, target_type, expected):
        assert target_kind_suffix(target_type) == expected

    def test_each_builder_writes_where_the_rule_says(
        self, tmp_path, sources, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        built = {
            "obj.one": project.Program("one", env, sources=["src/a.c"]),
            "obj.two.static": project.StaticLibrary("two", env, sources=["src/a.c"]),
            "obj.three.shared": project.SharedLibrary(
                "three", env, sources=["src/a.c"]
            ),
            "obj.four.object": project.ObjectLibrary("four", env, sources=["src/a.c"]),
        }
        project.resolve()

        for directory, target in built.items():
            assert object_dir_name(target) == directory
            assert _object_dirs(target) == {f"build/{directory}/src"}


class TestOneNameForTwoKinds:
    """`Program("foo")` and `SharedLibrary("foo")` write foo and libfoo.so."""

    def test_both_are_registered(self, tmp_path, sources, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        program = project.Program("foo", env, sources=["src/a.c"])
        library = project.SharedLibrary("foo", env, sources=["src/a.c"])

        assert project.targets == [program, library]

    def test_they_compile_into_their_own_directories_and_both_link(
        self, tmp_path, sources, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        program = project.Program("foo", env, sources=["src/a.c"])
        library = project.SharedLibrary("foo", env, sources=["src/a.c"])
        project.resolve()

        assert _object_dirs(program) == {"build/obj.foo/src"}
        assert _object_dirs(library) == {"build/obj.foo.shared/src"}
        assert [n.path.name for n in program.output_nodes] == [
            "foo" + env.target.exe_suffix
        ]
        assert [n.path.name for n in library.output_nodes] == [
            env.target.shared_lib_prefix + "foo" + env.target.shared_lib_suffix
        ]

    def test_one_name_one_type_one_environment_is_still_refused(
        self, tmp_path, sources, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        project.Program("foo", env, sources=["src/a.c"])

        with pytest.raises(ValueError, match="already exists") as excinfo:
            project.Program("foo", env, sources=["src/b.c"])

        message = str(excinfo.value)
        assert "(program)" in message
        assert "Two targets of one type" in message

    def test_the_name_no_longer_selects_one(self, tmp_path, sources, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        project.Program("foo", env, sources=["src/a.c"])
        project.SharedLibrary("foo", env, sources=["src/a.c"])

        with pytest.raises(KeyError) as excinfo:
            project.get_target("foo")

        message = str(excinfo.value)
        assert "Multiple targets named 'foo'" in message
        assert "a program and a shared_library" in message
        assert project.has_target("foo") is True

    def test_default_by_name_refuses_it_too(self, tmp_path, sources, gcc_toolchain):
        """One lookup serves them all, so Default() says the same thing."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        project.Program("foo", env, sources=["src/a.c"])
        project.SharedLibrary("foo", env, sources=["src/a.c"])

        with pytest.raises(KeyError, match="Multiple targets named 'foo'"):
            project.Default("foo")

    def test_pcons_build_refuses_it_too(self, tmp_path, sources, gcc_toolchain):
        """`pcons build foo` translates the token through the same lookup,
        and a build that skips generate reads it back from the cache: neither
        may pick one of the two."""
        from pcons.cli import (
            _cached_target_lookup,
            _named_target_paths,
            _project_target_lookup,
        )
        from pcons.core.cache import BuildCache

        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        project.Program("foo", env, sources=["src/a.c"])
        project.SharedLibrary("foo", env, sources=["src/a.c"])
        project.resolve()

        with pytest.raises(KeyError, match="Multiple targets named 'foo'"):
            _project_target_lookup(project)("foo")

        recorded = _named_target_paths(project)
        assert recorded == {"foo": []}
        build_dir = tmp_path / "build"
        build_dir.mkdir(exist_ok=True)
        BuildCache(build_dir).update({"target_paths": recorded})
        with pytest.raises(KeyError, match="no spelling tells them apart"):
            _cached_target_lookup(build_dir)("foo")

    @pytest.mark.skipif(
        sys.platform == "win32", reason="the exports list is a Unix linker input"
    )
    def test_the_link_input_file_lands_in_the_object_directory(
        self, tmp_path, sources, gcc_toolchain
    ):
        """The exported-symbols list is per target, so it goes where the
        objects go rather than beside the artifacts."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        library = project.SharedLibrary("foo", env, sources=["src/a.c"])
        library.set_option("exported_symbols", ["a"])
        project.resolve()

        written = {p.as_posix() for p in project._nodes}
        # macOS takes a symbol list, GNU ld a version script.
        assert written & {
            "build/obj.foo.shared/foo.exports",
            "build/obj.foo.shared/foo.version",
        }


class TestTwoEnvironmentsOneName:
    """The #197 report: named environments, no build_prefix, objects merged."""

    def _clashing_project(self, tmp_path, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env_a = project.Environment(toolchain=gcc_toolchain, name="a")
        env_b = project.Environment(toolchain=gcc_toolchain, name="b")
        env_a.cc.defines.append("N=1")
        env_b.cc.defines.append("N=2")
        one = project.SharedLibrary("foo", env_a, sources=["src/a.c"])
        one.output_suffix = ".one"  # so the artifacts do not collide
        two = project.SharedLibrary("foo", env_b, sources=["src/a.c"])
        two.output_suffix = ".two"
        return project

    def test_the_shared_object_is_refused(self, tmp_path, sources, gcc_toolchain):
        project = self._clashing_project(tmp_path, gcc_toolchain)

        with pytest.raises(OutputCollisionError) as excinfo:
            project.resolve()

        message = str(excinfo.value)
        assert f"both build {Path('build/obj.foo.shared/src/a.c.o')}" in message
        assert "p::foo@a" in message and "p::foo@b" in message
        assert 'env.build_prefix = "a"' in message

    def test_a_build_prefix_keeps_them_apart(self, tmp_path, sources, gcc_toolchain):
        project = Project("p", root_dir=tmp_path)
        env_a = project.Environment(toolchain=gcc_toolchain, name="a")
        env_a.build_prefix = "a"
        env_b = project.Environment(toolchain=gcc_toolchain, name="b")
        env_b.build_prefix = "b"
        one = project.SharedLibrary("foo", env_a, sources=["src/a.c"])
        two = project.SharedLibrary("foo", env_b, sources=["src/a.c"])
        project.resolve()

        assert _object_dirs(one) == {"build/a/obj.foo.shared/src"}
        assert _object_dirs(two) == {"build/b/obj.foo.shared/src"}


class TestObjectsSharedByDesign:
    """An object an ObjectLibrary produced is its own; a dependent adopting
    it is not a second producer."""

    def test_a_dependent_linking_an_object_library_resolves(
        self, tmp_path, sources, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        core = project.ObjectLibrary("core", env, sources=["src/a.c"])
        app = project.Program("app", env, sources=["src/b.c"])
        app.link(core)
        project.resolve()

        objects = {node.path.as_posix() for node in core.output_nodes}
        assert objects == {"build/obj.core.object/src/a.c.o"}
        link_inputs = {
            node.path.as_posix() for node in app.output_nodes[0].explicit_deps
        }
        assert objects <= link_inputs

    def test_an_object_library_passed_as_a_source_resolves(
        self, tmp_path, sources, gcc_toolchain
    ):
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        core = project.ObjectLibrary("core", env, sources=["src/a.c"])
        app = project.Program("app", env, sources=["src/b.c", core])
        project.resolve()

        adopted = {node.path.as_posix() for node in app.intermediate_nodes}
        assert "build/obj.core.object/src/a.c.o" in adopted
        assert "build/obj.app/src/b.c.o" in adopted

    def test_two_targets_compiling_a_source_identically_share_the_object(
        self, tmp_path, sources, gcc_toolchain
    ):
        """Long-standing behavior, and not a collision: one compile, one
        object, linked by both."""
        project = Project("p", root_dir=tmp_path)
        env = project.Environment(toolchain=gcc_toolchain)
        one = project.Program("one", env, sources=["src/a.c"])
        two = project.Program("two", env, sources=["src/a.c"])
        project.resolve()

        assert one.intermediate_nodes == two.intermediate_nodes
