# SPDX-License-Identifier: MIT
"""depends() on an alias means depends() on everything the alias groups.

An alias is the name a build script gives a group of targets, and the way
to refer to an anonymous one. Depending on the name has to reach the build
file as a dependency on each member, including members added to the alias
after the depends() call, and including the members' usage requirements.
"""

from __future__ import annotations

from pathlib import Path

from pcons import Generator, Project
from pcons.core.environment import Environment
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator

from ._command_test_utils import built_path


def _project(tmp_path: Path) -> Project:
    for name in ("in.txt", "icons.svg", "fonts.svg"):
        (tmp_path / name).write_text("")
    return Project("demo", root_dir=tmp_path, build_dir="build")


def _command(project: Project, env: Environment, output: str) -> Target:
    return env.Command(
        target=project.build_dir / output,
        source=[Path(output).stem + ".svg"],
        command=["cp", "$SOURCE", "$TARGET"],
    )


def _app(project: Project, env: Environment) -> Target:
    return env.Command(
        target=project.build_dir / "app.txt",
        source=["in.txt"],
        command=["cp", "$SOURCE", "$TARGET"],
    )


def _generate(project: Project, kind: str, name: str) -> str:
    Generator(kind).generate(project)
    BaseGenerator._generate_pending(project)
    return (Path(project.root_dir) / "build" / name).read_text(encoding="utf-8")


def _rule(text: str, output: str) -> str:
    """The build statement (ninja) or rule line (make) that builds *output*."""
    return next(
        line for line in text.splitlines() if line.split(":")[0].endswith(output)
    )


def _rule_command(text: str, build_statement: str) -> str:
    """The ``command =`` line of the ninja rule *build_statement* uses."""
    rule_name = build_statement.split(":")[1].split()[0]
    lines = text.splitlines()
    start = lines.index(f"rule {rule_name}")
    return lines[start + 1]


def test_an_alias_dependency_is_an_implicit_dependency_in_ninja(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    env = project.Environment()
    icons = _command(project, env, "gen/icons.bin")
    project.Alias("icons", icons)
    _app(project, env).depends(project.aliases["icons"])

    line = _rule(_generate(project, "ninja", "build.ninja"), "app.txt")

    assert line.split("|")[1].split() == [built_path(project, icons)]


def test_an_alias_dependency_is_a_prerequisite_in_make(tmp_path: Path) -> None:
    """The member file, not the alias: a .PHONY prerequisite would make the
    dependent out of date on every run."""
    project = _project(tmp_path)
    env = project.Environment()
    icons = _command(project, env, "gen/icons.bin")
    project.Alias("icons", icons)
    _app(project, env).depends(project.aliases["icons"])

    line = _rule(_generate(project, "makefile", "Makefile"), "app.txt")

    prerequisites = line.split(":", 1)[1].split("|")[0].split()
    assert built_path(project, icons) in prerequisites
    assert "icons" not in prerequisites


def test_a_member_added_after_the_depends_call_counts(tmp_path: Path) -> None:
    """Alias() may be called again, from any script in the tree, so the
    members are read at resolve time."""
    project = _project(tmp_path)
    env = project.Environment()
    icons = _command(project, env, "gen/icons.bin")
    project.Alias("assets", icons)
    _app(project, env).depends(project.aliases["assets"])
    fonts = _command(project, env, "gen/fonts.bin")
    project.Alias("assets", fonts)

    line = _rule(_generate(project, "ninja", "build.ninja"), "app.txt")

    assert sorted(line.split("|")[1].split()) == sorted(
        [built_path(project, icons), built_path(project, fonts)]
    )


def test_a_nested_alias_contributes_its_own_members(tmp_path: Path) -> None:
    project = _project(tmp_path)
    env = project.Environment()
    icons = _command(project, env, "gen/icons.bin")
    fonts = _command(project, env, "gen/fonts.bin")
    project.Alias("assets", project.Alias("icons", icons))
    project.Alias("assets", fonts)
    _app(project, env).depends(project.aliases["assets"])

    line = _rule(_generate(project, "ninja", "build.ninja"), "app.txt")

    assert sorted(line.split("|")[1].split()) == sorted(
        [built_path(project, icons), built_path(project, fonts)]
    )


def test_a_plain_node_member_is_a_dependency_too(tmp_path: Path) -> None:
    project = _project(tmp_path)
    env = project.Environment()
    project.Alias("inputs", project.node(tmp_path / "icons.svg"))
    _app(project, env).depends(project.aliases["inputs"])

    line = _rule(_generate(project, "ninja", "build.ninja"), "app.txt")

    assert line.split("|")[1].strip().endswith("icons.svg")


def _library_behind_an_alias(
    tmp_path: Path, gcc_toolchain
) -> tuple[Project, Target, Target]:
    """A program depending on an alias that groups a library with a public
    include dir, which a direct depends(lib) would pass on."""
    (tmp_path / "lib.c").write_text("int f(void) { return 1; }\n")
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n")
    project = Project("demo", root_dir=tmp_path, build_dir="build")
    env = project.Environment(toolchain=gcc_toolchain)
    lib = project.StaticLibrary("helper", env, sources=["lib.c"])
    lib.public.include_dirs = ["include"]
    project.Alias("helpers", lib)
    app = project.Program("app", env, sources=["main.c"])
    app.depends(project.aliases["helpers"])
    return project, lib, app


def test_a_member_targets_public_requirements_reach_the_dependent(
    tmp_path: Path, gcc_toolchain
) -> None:
    project, lib, app = _library_behind_an_alias(tmp_path, gcc_toolchain)

    text = _generate(project, "ninja", "build.ninja")

    assert lib in app.transitive_dependencies()
    assert "include" in app.collect_usage_requirements().include_dirs
    assert "include" in _rule_command(text, _rule(text, "main.c.o"))


def test_explain_names_the_member_behind_the_alias(
    tmp_path: Path, gcc_toolchain
) -> None:
    from pcons import _cli_explain

    project, _lib, app = _library_behind_an_alias(tmp_path, gcc_toolchain)
    project.resolve()

    report = "\n".join(
        _cli_explain.render_explanation(
            project, [app], explicit_targets=True, color=False, width=0
        )
    )

    assert "include  <- helper (public)" in report


def test_an_alias_holding_the_dependent_itself_is_not_a_cycle(tmp_path: Path) -> None:
    """Alias("all", app) groups app; app.depends(that) adds nothing."""
    project = _project(tmp_path)
    env = project.Environment()
    app = _app(project, env)
    icons = _command(project, env, "gen/icons.bin")
    project.Alias("all-assets", app, icons)
    app.depends(project.aliases["all-assets"])

    line = _rule(_generate(project, "ninja", "build.ninja"), "app.txt")

    assert line.split("|")[1].split() == [built_path(project, icons)]
