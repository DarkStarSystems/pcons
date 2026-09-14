# SPDX-License-Identifier: MIT
"""Tests for build tiers: which invocation reaches which target.

The decision (`pcons.core.tiers.decide_build_tiers`) is made once at generate
from the final state, so these cover the precedence table row by row, that the
order of the calls never matters, the contradiction, the report both `explain`
and `-v` print, the deprecated `build_by_default` alias, and that the two build
generators write the same sets.
"""

from __future__ import annotations

import sys

import pytest

from pcons.core.errors import PconsError
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.core.tiers import decide_build_tiers
from pcons.generators.generator import BaseGenerator
from pcons.generators.makefile import MakefileGenerator
from pcons.generators.ninja import NinjaGenerator

COPY = [
    sys.executable.replace("\\", "/"),
    "-c",
    "import shutil,sys; shutil.copy(sys.argv[1], sys.argv[2])",
]


def command(env, name: str, target: str):
    """A product-making command target, cheap enough for any test here."""
    return env.Command(
        target=target,
        source="in.txt",
        command=[*COPY, "$SOURCE", "$TARGET"],
        name=name,
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with one product (a command) and one step (an install)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "in.txt").write_text("x\n")
    project = Project("tiers", root_dir=tmp_path, build_dir=tmp_path / "build")
    project.Environment()
    return project


@pytest.fixture
def env(project):
    return project.environments[0]


def tiers_of(project) -> dict[str, str]:
    """Every target's decided tier, by target name."""
    return {d.target.name: d.tier for d in decide_build_tiers(project)}


def reasons_of(project) -> dict[str, str]:
    return {d.target.name: d.reason for d in decide_build_tiers(project)}


class TestBuilderPlacement:
    """Rule 3: the builder that creates a target places it."""

    def test_a_command_is_a_product(self, project, env):
        command(env, "note", "note.txt")
        assert tiers_of(project)["note"] == "default"
        assert reasons_of(project)["note"] == "product (Command)"

    def test_an_install_is_a_step(self, project, env):
        product = command(env, "note", "note.txt")
        project.Install("dist", [product])

        decided = tiers_of(project)
        assert decided["note"] == "default"
        assert decided["install_dist"] == "all"
        assert reasons_of(project)["install_dist"] == "step (Install)"

    def test_a_plain_target_is_a_product(self, project):
        Target("bare", project=project)
        assert tiers_of(project)["bare"] == "default"
        assert reasons_of(project)["bare"] == "product"

    def test_an_archive_is_a_step(self, project, env):
        archive = project.Tarfile(env, output="bundle.tar", sources=["in.txt"])
        assert tiers_of(project)[archive.name] == "all"

    def test_a_custom_builder_declares_its_own_tier(self, project, env):
        from pcons.core.builder_registry import BuilderRegistry, builder

        @builder("SlowReport", target_type="command", build_tier="manual")
        class SlowReportBuilder:
            @staticmethod
            def create_target(project, name):
                return Target(name, target_type="command", project=project)

        try:
            project.SlowReport("audit")
            assert tiers_of(project)["audit"] == "manual"
            assert reasons_of(project)["audit"] == "placed by SlowReport"
        finally:
            BuilderRegistry.unregister("SlowReport")

    def test_an_unknown_tier_is_refused_at_registration(self):
        from pcons.core.builder_registry import BuilderRegistry, builder

        with pytest.raises(PconsError, match="build_tier must be one of"):

            @builder("Bogus", target_type="command", build_tier="sometimes")
            class BogusBuilder:
                @staticmethod
                def create_target(project, name):
                    return Target(name, project=project)

        BuilderRegistry.unregister("Bogus")


class TestScriptChoice:
    """Rule 1: what the script sets wins, and says where it was set."""

    def test_a_product_moved_out_of_the_default_build(self, project, env):
        bench = command(env, "bench", "bench.txt")
        bench.build_tier = "all"

        assert tiers_of(project)["bench"] == "all"
        assert reasons_of(project)["bench"] == 'build_tier = "all"'

    def test_a_step_moved_into_the_default_build(self, project, env):
        product = command(env, "note", "note.txt")
        installed = project.Install("dist", [product])
        installed.build_tier = "default"

        assert tiers_of(project)["install_dist"] == "default"

    def test_a_product_moved_out_of_all(self, project, env):
        rewriter = command(env, "rewrite", "rewrite.txt")
        rewriter.build_tier = "manual"

        decided = decide_build_tiers(project)
        assert decided[rewriter].tier == "manual"
        assert rewriter not in decided.all_targets

    def test_the_choice_records_the_build_script_line(self, project, env):
        bench = command(env, "bench", "bench.txt")
        bench.build_tier = "all"

        location = decide_build_tiers(project)[bench].location
        assert location is not None
        assert location.filename == __file__

    def test_an_unknown_tier_is_refused(self, project, env):
        note = command(env, "note", "note.txt")
        with pytest.raises(PconsError, match="build_tier must be one of"):
            note.build_tier = "sometimes"

    def test_a_script_choice_beats_the_builder(self, project, env):
        """A script assignment survives a builder placing the same target."""
        product = command(env, "note", "note.txt")
        product.build_tier = "all"
        product.place_in_tier("default", by="SomeWrapper")

        assert tiers_of(project)["note"] == "all"


class TestDefaultCall:
    """Rule 2: Default() names the default tier outright."""

    def test_a_named_product_stays_default(self, project, env):
        app = command(env, "app", "app.txt")
        command(env, "note", "note.txt")
        project.Default(app)

        decided = tiers_of(project)
        assert decided["app"] == "default"
        assert reasons_of(project)["app"] == "named in Default()"

    def test_an_unnamed_product_is_demoted(self, project, env):
        app = command(env, "app", "app.txt")
        command(env, "note", "note.txt")
        project.Default(app)

        assert tiers_of(project)["note"] == "all"
        assert reasons_of(project)["note"] == "not named in Default()"

    def test_a_named_step_is_promoted(self, project, env):
        product = command(env, "note", "note.txt")
        installed = project.Install("dist", [product])
        project.Default(installed)

        assert tiers_of(project)["install_dist"] == "default"

    def test_an_unnamed_step_is_untouched(self, project, env):
        app = command(env, "app", "app.txt")
        product = command(env, "note", "note.txt")
        project.Install("dist", [product])
        project.Default(app)

        assert tiers_of(project)["install_dist"] == "all"

    def test_an_unnamed_manual_target_is_untouched(self, project, env):
        app = command(env, "app", "app.txt")
        rewriter = command(env, "rewrite", "rewrite.txt")
        rewriter.place_in_tier("manual", by="Rewriter")
        project.Default(app)

        assert tiers_of(project)["rewrite"] == "manual"

    def test_the_demotion_names_the_default_call(self, project, env):
        app = command(env, "app", "app.txt")
        note = command(env, "note", "note.txt")
        project.Default(app)

        decided = decide_build_tiers(project)
        assert decided[note].location == decided[app].location

    def test_several_calls_append(self, project, env):
        app = command(env, "app", "app.txt")
        tool = command(env, "tool", "tool.txt")
        command(env, "note", "note.txt")
        project.Default(app)
        project.Default(tool)

        decided = tiers_of(project)
        assert decided["app"] == "default"
        assert decided["tool"] == "default"
        assert decided["note"] == "all"


class TestOrderIndependence:
    """Nothing is decided at the call, so call order cannot matter."""

    def test_default_before_the_other_targets_exist(self, project, env):
        app = command(env, "app", "app.txt")
        project.Default(app)
        # Created after the Default() call: still demoted, as if it were first.
        command(env, "note", "note.txt")

        assert tiers_of(project) == {"app": "default", "note": "all"}

    def test_default_after_the_other_targets_exist(self, project, env):
        app = command(env, "app", "app.txt")
        command(env, "note", "note.txt")
        project.Default(app)

        assert tiers_of(project) == {"app": "default", "note": "all"}

    def test_build_tier_set_before_and_after_default(self, project, env):
        early = command(env, "early", "early.txt")
        early.build_tier = "manual"
        app = command(env, "app", "app.txt")
        project.Default(app)
        late = command(env, "late", "late.txt")
        late.build_tier = "manual"

        decided = tiers_of(project)
        assert decided["early"] == "manual"
        assert decided["late"] == "manual"

    def test_a_subdirectory_default_governs_the_whole_tree(self, project, env):
        """Location never matters: a child's Default() demotes the parent's
        products exactly as a top-level call would."""
        app = command(env, "app", "app.txt")
        with project._enter_subdir("sub"):
            child = Project("sub", root_dir=project.root_dir / "sub")
            child_env = child.Environment()
            tool = command(child_env, "tool", "tool.txt")
            child.Default(tool)

        decided = tiers_of(project)
        assert decided["tool"] == "default"
        assert decided["app"] == "all"
        assert decide_build_tiers(project)[app].reason == "not named in Default()"


class TestContradiction:
    def test_named_in_default_and_set_to_all(self, project, env):
        bench = command(env, "bench", "bench.txt")
        bench.build_tier = "all"
        tier_line = sys._getframe().f_lineno - 1
        project.Default(bench)
        default_line = sys._getframe().f_lineno - 1

        with pytest.raises(PconsError) as excinfo:
            decide_build_tiers(project)
        message = str(excinfo.value)
        assert "bench" in message
        # Both lines are named: the build_tier assignment and the
        # Default() call, two lines apart in this test.
        assert "named in Default()" in message
        assert 'build_tier = "all"' in message
        assigned = message.count(f"{__file__}:{tier_line}")
        called = message.count(f"{__file__}:{default_line}")
        assert assigned and called

    def test_named_in_default_and_set_to_manual(self, project, env):
        rewriter = command(env, "rewrite", "rewrite.txt")
        rewriter.build_tier = "manual"
        project.Default(rewriter)

        with pytest.raises(PconsError, match='build_tier = "manual"'):
            decide_build_tiers(project)

    def test_named_in_default_and_set_to_default_is_fine(self, project, env):
        app = command(env, "app", "app.txt")
        app.build_tier = "default"
        project.Default(app)

        assert tiers_of(project)["app"] == "default"

    def test_a_builder_placement_is_no_contradiction(self, project, env):
        """Default() on an install is how an install joins the ordinary
        build; only a script's own choice can contradict."""
        product = command(env, "note", "note.txt")
        installed = project.Install("dist", [product])
        project.Default(installed)

        assert tiers_of(project)["install_dist"] == "default"


class TestBuildByDefaultAlias:
    """The deprecated boolean, kept one release."""

    def test_true_reads_default(self, project, env):
        note = command(env, "note", "note.txt")
        assert note.build_by_default is True
        assert note.build_tier == "default"

    def test_false_writes_manual(self, project, env):
        """False kept a target out of `all` as well as the default build,
        which is what manual means; mapping it to `all` would put a
        source-rewriting target on `ninja all` with nothing said."""
        note = command(env, "note", "note.txt")
        note.build_by_default = False
        assert note.build_tier == "manual"
        assert note.build_by_default is False

    def test_true_writes_default(self, project, env):
        product = command(env, "note", "note.txt")
        installed = project.Install("dist", [product])
        installed.build_by_default = True
        assert installed.build_tier == "default"

    def test_a_manual_target_reads_false(self, project, env):
        note = command(env, "note", "note.txt")
        note.build_tier = "manual"
        assert note.build_by_default is False

    def test_writing_the_alias_is_a_script_choice(self, project, env):
        """It goes through build_tier, so it wins over the placement and
        records its line."""
        note = command(env, "note", "note.txt")
        note.build_by_default = False
        note.place_in_tier("default", by="Command")

        decided = decide_build_tiers(project)[note]
        assert decided.tier == "manual"
        assert decided.location is not None


class TestReport:
    def test_every_target_gets_a_line(self, project, env):
        app = command(env, "app", "app.txt")
        product = command(env, "note", "note.txt")
        project.Install("dist", [product])
        bench = command(env, "bench", "bench.txt")
        bench.build_tier = "all"
        project.Default(app)

        lines = decide_build_tiers(project).report_lines()
        assert lines[0] == "build tiers:"
        rows = {line.split()[0]: line for line in lines[1:]}
        assert "default" in rows["app"]
        assert "named in Default()" in rows["app"]
        assert "not named in Default()" in rows["note"]
        assert "step (Install)" in rows["install_dist"]
        assert 'build_tier = "all"' in rows["bench"]
        # A script line is named, spelled as the reader would write it.
        assert "test_build_tiers.py:" in rows["bench"]
        assert "test_build_tiers.py:" in rows["app"]
        # A builder placement has no script line to name.
        assert ":" not in rows["install_dist"]

    def test_a_subset_reports_only_those_targets(self, project, env):
        app = command(env, "app", "app.txt")
        command(env, "note", "note.txt")

        lines = decide_build_tiers(project).report_lines([app])
        assert len(lines) == 2
        assert lines[1].split()[0] == "app"

    def test_no_targets_no_report(self, project):
        assert decide_build_tiers(project).report_lines() == []

    def test_explain_shows_the_section(self, project, env, capsys):
        from pcons import _cli_explain

        app = command(env, "app", "app.txt")
        product = command(env, "note", "note.txt")
        project.Install("dist", [product])
        project.Default(app)
        project.resolve()

        report = "\n".join(
            _cli_explain.render_explanation(
                project,
                list(project.targets),
                explicit_targets=False,
                color=False,
                width=0,
            )
        )
        assert "build tiers:" in report
        section = report.split("build tiers:")[1]
        assert "named in Default()" in section
        assert "step (Install)" in section

    def test_generate_logs_the_report_when_verbose(self, project, env, caplog):
        import logging

        command(env, "note", "note.txt")
        with caplog.at_level(logging.INFO, logger="pcons"):
            _generate(project, NinjaGenerator())

        logged = [record.getMessage() for record in caplog.records]
        assert "build tiers:" in logged
        assert any("product (Command)" in line for line in logged)

    def test_generate_is_quiet_without_verbose(self, project, env, caplog):
        import logging

        command(env, "note", "note.txt")
        with caplog.at_level(logging.WARNING, logger="pcons"):
            _generate(project, NinjaGenerator())

        logged = [record.getMessage() for record in caplog.records]
        assert "build tiers:" not in logged


def _generate(project, generator) -> str:
    generator.generate(project)
    BaseGenerator._generate_pending(project)
    name = "build.ninja" if isinstance(generator, NinjaGenerator) else "Makefile"
    return (project.build_dir / name).read_text().replace("\\", "/")


def _basenames(paths: list[str]) -> set[str]:
    """Just the filenames: the two generators spell an install destination
    differently (`$topdir/...` vs absolute), and this test is about which
    outputs are in which set, not how each writes a path."""
    return {path.rsplit("/", 1)[-1] for path in paths}


def _ninja_sets(content: str) -> tuple[set[str], set[str]]:
    """The outputs `ninja` and `ninja all` build, from build.ninja."""
    default: set[str] = set()
    every: set[str] = set()
    for line in content.splitlines():
        if line.startswith("default "):
            default = _basenames(line.removeprefix("default ").split())
        elif line.startswith("build all: phony "):
            every = _basenames(line.removeprefix("build all: phony ").split())
    return default, every


def _make_sets(content: str) -> tuple[set[str], set[str]]:
    """The same two sets, from the Makefile."""
    default: set[str] = set()
    every: set[str] = set()
    for line in content.splitlines():
        if line.startswith("default: "):
            default = _basenames(line.removeprefix("default: ").split())
        elif line.startswith("all: "):
            every = _basenames(line.removeprefix("all: ").split())
    return default, every


class TestGenerators:
    """Both build generators write the tiers, and write the same ones."""

    @pytest.fixture
    def mixed(self, project, env):
        """One product, one step, one manual target, one demoted product."""
        app = command(env, "app", "app.txt")
        product = command(env, "note", "note.txt")
        project.InstallAs("dist/renamed.txt", product)
        rewriter = command(env, "rewrite", "rewrite.txt")
        rewriter.build_tier = "manual"
        return project, app

    def test_ninja_default_is_every_product(self, mixed):
        project, _app = mixed
        default, every = _ninja_sets(_generate(project, NinjaGenerator()))
        assert default == {"app.txt", "note.txt"}
        assert "rewrite.txt" not in every
        assert {"app.txt", "note.txt", "renamed.txt"} <= every

    def test_ninja_honours_default(self, mixed):
        project, app = mixed
        project.Default(app)
        default, _every = _ninja_sets(_generate(project, NinjaGenerator()))
        assert default == {"app.txt"}

    def test_a_manual_target_is_still_buildable(self, mixed):
        project, _app = mixed
        content = _generate(project, NinjaGenerator())
        assert "build rewrite.txt:" in content

    def test_both_generators_agree(self, mixed):
        project, _app = mixed
        ninja_sets = _ninja_sets(_generate(project, NinjaGenerator()))
        make_sets = _make_sets(_generate(project, MakefileGenerator()))
        assert ninja_sets == make_sets

    def test_both_generators_agree_with_default(self, mixed):
        project, app = mixed
        project.Default(app)
        ninja_sets = _ninja_sets(_generate(project, NinjaGenerator()))
        make_sets = _make_sets(_generate(project, MakefileGenerator()))
        assert ninja_sets == make_sets


class TestPlacementsWithNoOutputs:
    def test_a_test_target_is_manual(self, tmp_path, monkeypatch, gcc_toolchain):
        """A Test runs through its own phony and has no output `all` could
        name, so its honest tier is manual: `ninja test` runs it."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "t.c").write_text("int main(void) { return 0; }\n")
        project = Project("t", root_dir=tmp_path, build_dir=tmp_path / "build")
        env = project.Environment(toolchain=gcc_toolchain)
        prog = project.Program("prog", env, sources=["t.c"])
        test = project.Test("smoke", prog)
        project.resolve()

        assert decide_build_tiers(project)[test].tier == "manual"


class TestMakefileManualOnlyProject:
    def test_a_manual_only_project_builds_nothing_unasked(self, project, env):
        """The orphan-node fallback is for builds that register no target;
        a project whose every target is manual has said what it wants."""
        stamp = env.Command(
            target="stamp.txt",
            source=["in.txt"],
            command="cp $SOURCE $TARGET",
            name="stamp",
        )
        stamp.build_tier = "manual"
        project.resolve()

        content = _generate(project, MakefileGenerator())

        assert "\ndefault:\n" in content  # an explicit nothing
        assert "\nall:" not in content


class TestAnEmptyDefaultTier:
    """When nothing is in the default tier, a plain build builds nothing:
    not the steps in `all`, and not the build tool's own choice of the first
    edge, which could be a manual target."""

    @pytest.fixture
    def steps_only(self, project, env):
        note = command(env, "note", "note.txt")
        note.build_tier = "manual"
        project.Install("dist", [note])
        project.resolve()
        return project

    def test_ninja_names_an_explicit_nothing(self, steps_only):
        content = _generate(steps_only, NinjaGenerator())
        assert "default pcons-nothing\n" in content
        assert "default all" not in content
        assert "build all: phony" in content  # the install is still one word away

    def test_make_names_an_explicit_nothing(self, steps_only):
        content = _generate(steps_only, MakefileGenerator())
        assert "\ndefault:\n" in content
        assert ".DEFAULT_GOAL := default" in content
        assert "\nall: " in content


class TestRegisteredHelpersDeclareTheirTier:
    """A registered builder's declaration is what places the target it
    returns, so a helper that wraps a Command or an Install must declare
    its own tier or the wrapped builder's placement is overwritten."""

    @pytest.mark.parametrize("name", ["Pkg", "ComponentPkg", "Dmg", "Msix", "Appx"])
    def test_an_installer_is_a_step(self, name):
        from pcons.core.builder_registry import BuilderRegistry

        registration = BuilderRegistry.get(name)
        assert registration is not None
        assert registration.build_tier == "all"

    @pytest.mark.parametrize("name", ["MacosBundle", "FlatBundle"])
    def test_a_bundle_is_a_product(self, name):
        from pcons.core.builder_registry import BuilderRegistry

        registration = BuilderRegistry.get(name)
        assert registration is not None
        assert registration.build_tier == "default"
