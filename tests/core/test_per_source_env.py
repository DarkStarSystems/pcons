# SPDX-License-Identifier: MIT
"""Per-source environments: add_sources(..., env=...)."""

import pytest

from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator


def _make_sources(tmp_path, *names: str) -> None:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for name in names:
        (src / name).write_text("int f(void){return 0;}\n")


def _rules_for(tmp_path, project) -> str:
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (tmp_path / "build" / "build.ninja").read_text()


class TestPerSourceEnvironment:
    def test_one_source_compiles_with_its_own_flags(self, tmp_path, gcc_toolchain):
        _make_sources(tmp_path, "a.c", "slow.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.cc.flags.append("-O2")

        lib = project.StaticLibrary("core", env, sources=["src/a.c"])
        with env.override() as careful:
            careful.cc.flags.append("-O1")
            lib.add_sources(["src/slow.c"], env=careful)

        content = _rules_for(tmp_path, project)
        commands = [
            line
            for line in content.splitlines()
            if line.strip().startswith("command =")
        ]
        assert any("-O2 -O1" in c for c in commands)
        assert any("-O2" in c and "-O1" not in c for c in commands)

    def test_the_targets_usage_requirements_still_apply(self, tmp_path, gcc_toolchain):
        """The whole point: the source stays inside the target, so it keeps
        the target's include dirs and defines instead of needing them
        re-stated on a second target."""
        _make_sources(tmp_path, "a.c", "slow.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)

        lib = project.StaticLibrary("core", env, sources=["src/a.c"])
        lib.private.include_dirs.append("include")
        lib.private.defines.append("CORE_BUILD=1")
        with env.override() as careful:
            careful.cc.flags.append("-O1")
            lib.add_sources(["src/slow.c"], env=careful)

        content = _rules_for(tmp_path, project)
        override_rule = next(
            line
            for line in content.splitlines()
            if "command =" in line and "-O1" in line
        )
        assert "-I$topdir/include" in override_rule
        assert "-DCORE_BUILD=1" in override_rule

    def test_dependency_requirements_reach_the_override_source(
        self, tmp_path, gcc_toolchain
    ):
        _make_sources(tmp_path, "slow.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)

        headers = project.HeaderOnlyLibrary("headers", include_dirs=["vendor"])
        lib = project.StaticLibrary("core", env, sources=[])
        lib.link(headers)
        with env.override() as careful:
            careful.cc.flags.append("-O1")
            lib.add_sources(["src/slow.c"], env=careful)

        content = _rules_for(tmp_path, project)
        assert "-I$topdir/vendor" in content

    def test_all_objects_land_in_the_one_target(self, tmp_path, gcc_toolchain):
        _make_sources(tmp_path, "a.c", "slow.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)

        lib = project.StaticLibrary("core", env, sources=["src/a.c"])
        with env.override() as careful:
            careful.cc.flags.append("-O1")
            lib.add_sources(["src/slow.c"], env=careful)

        project.resolve()

        objects = {node.path.name for node in lib.intermediate_nodes}
        assert objects == {"a.c.o", "slow.c.o"}

    def test_sources_without_an_override_are_untouched(self, tmp_path, gcc_toolchain):
        """Adding an override must not perturb the other sources' command —
        they should still share one rule."""
        _make_sources(tmp_path, "a.c", "b.c", "slow.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)

        lib = project.StaticLibrary("core", env, sources=["src/a.c", "src/b.c"])
        with env.override() as careful:
            careful.cc.flags.append("-O1")
            lib.add_sources(["src/slow.c"], env=careful)

        content = _rules_for(tmp_path, project)
        rules = {
            line.split()[2]  # "build <output>: <rule> <inputs>"
            for line in content.splitlines()
            if line.startswith("build obj.core/") and ".c.o:" in line
        }
        assert len(rules) == 2  # a.c and b.c share one rule, slow.c has its own

    def test_same_source_twice_with_different_envs_is_an_error(
        self, tmp_path, gcc_toolchain
    ):
        _make_sources(tmp_path, "a.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        lib = project.StaticLibrary("core", env, sources=[])

        with env.override() as first:
            lib.add_sources(["src/a.c"], env=first)
        with env.override() as second, pytest.raises(ValueError, match="twice"):
            lib.add_sources(["src/a.c"], env=second)

    def test_env_on_a_source_already_present_sets_it_in_place(
        self, tmp_path, gcc_toolchain
    ):
        """The natural spelling: leave the file in the main source list and
        name it again with env= to change how that one file compiles."""
        _make_sources(tmp_path, "a.c", "slow.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)
        env.cc.flags.append("-O2")

        lib = project.StaticLibrary("core", env, sources=["src/a.c", "src/slow.c"])
        with env.override() as careful:
            careful.cc.flags.append("-O1")
            lib.add_sources(["src/slow.c"], env=careful)

        assert [n.name for n in lib.sources] == ["src/a.c", "src/slow.c"]
        content = _rules_for(tmp_path, project)
        assert content.count("build obj.core/src/slow.c.o:") == 1
        assert "-O2 -O1" in content

    def test_add_source_takes_an_env_too(self, tmp_path, gcc_toolchain):
        _make_sources(tmp_path, "slow.c")
        project = Project("p", root_dir=tmp_path, build_dir="build")
        env = project.Environment(toolchain=gcc_toolchain)

        lib = project.StaticLibrary("core", env, sources=[])
        with env.override() as careful:
            careful.cc.flags.append("-O1")
            lib.add_source("src/slow.c", env=careful)

        assert "-O1" in _rules_for(tmp_path, project)
