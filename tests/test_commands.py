# SPDX-License-Identifier: MIT
"""Tests for the user-declared CLI command registry (`pcons.commands`)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

import pcons
from pcons import commands
from pcons.core.errors import PconsError
from pcons.core.target import Target


def as_module(name: str, func: Callable[..., Any]) -> Callable[..., Any]:
    """Attribute *func* to an add-on module, as `pcons.modules` loading would.

    The registry reads `__module__`, so this is how a test stands in for two
    different add-ons without writing files for them.
    """
    func.__module__ = f"pcons.modules.{name}"
    return func


def as_script_body(func: Callable[..., Any]) -> Callable[..., Any]:
    """Attribute *func* to the build script's own body.

    `run_script` execs the script under `RUN_NAME`, and the registry uses that
    to tell what a re-run will re-declare from what it will not. A function
    defined in this test file is neither, so a test that means "the script
    declared this" has to say so.
    """
    func.__module__ = commands.SCRIPT_BODY
    return func


class TestDeclaring:
    """What the decorators return and register."""

    def test_cli_command_returns_a_click_command(self) -> None:
        @pcons.cli_command()
        def flash() -> None:
            """Flash the board."""

        assert isinstance(flash, click.Command)
        assert commands.lookup("flash") is flash

    def test_cli_group_returns_a_click_group(self) -> None:
        @pcons.cli_group()
        def docs() -> None:
            """Documentation tasks."""

        assert isinstance(docs, click.Group)
        assert commands.lookup("docs") is docs

    def test_a_user_command_is_plain_click(self) -> None:
        """Plain click below `_DeclaresDependencies`, so a command owns its options.

        `MergingCommand` adopts a same-named option from the group above and
        reads `--debug`/`--verbose` as pcons means them, so a command declaring
        a `--debug` of its own would have its value validated as pcons
        subsystems and its `--build-dir` silently replaced by the run group's.
        """
        from pcons._cli_click import (
            MergingCommand,
            MergingGroup,
            _DeclaresDependencies,
        )

        @pcons.cli_command()
        def one() -> None:
            """One."""

        @pcons.cli_group()
        def two() -> None:
            """Two."""

        assert not isinstance(one, MergingCommand)
        assert not isinstance(two, MergingGroup)
        assert type(one).__bases__ == (_DeclaresDependencies, click.Command)
        assert type(two).__bases__ == (_DeclaresDependencies, click.Group)

    def test_the_default_class_is_the_one_carrying_depends(self) -> None:
        from pcons._cli_click import UserCommand, UserGroup

        @pcons.cli_command()
        def one() -> None:
            """One."""

        @pcons.cli_group()
        def two() -> None:
            """Two."""

        assert isinstance(one, UserCommand)
        assert isinstance(two, UserGroup)

    def test_cls_can_be_overridden(self) -> None:
        class Mine(click.Command):
            pass

        @pcons.cli_command(cls=Mine)
        def one() -> None:
            """One."""

        assert isinstance(one, Mine)

    def test_name_derivation_is_clicks(self) -> None:
        """click 8.2+ turns an underscore into a hyphen; pcons has no opinion."""

        @pcons.cli_command()
        def build_docs() -> None:
            """Build the docs."""

        assert build_docs.name == "build-docs"
        assert set(commands.declared()) == {"build-docs"}

    def test_explicit_name_wins(self) -> None:
        @pcons.cli_command("zap")
        def whatever() -> None:
            """Zap it."""

        assert set(commands.declared()) == {"zap"}

    def test_short_help_comes_from_the_docstring(self) -> None:
        @pcons.cli_command()
        def flash() -> None:
            """Flash the board."""

        assert flash.get_short_help_str() == "Flash the board."

    def test_options_apply_to_the_declared_command(self) -> None:
        @pcons.cli_command()
        @click.option("--baud", default=115200)
        def flash(baud: int) -> None:
            """Flash the board."""

        result = CliRunner().invoke(flash, ["--help"])
        assert result.exit_code == 0
        assert "--baud" in result.output

    def test_declaration_order_is_kept(self) -> None:
        for name in ("c", "a", "b"):
            pcons.cli_command(name)(lambda: None)

        assert list(commands.declared()) == ["c", "a", "b"]

    def test_group_subcommands_are_not_registered(self) -> None:
        @pcons.cli_group()
        def docs() -> None:
            """Documentation tasks."""

        @docs.command()
        def build() -> None:
            """Build them."""

        assert docs.commands == {"build": build}
        assert set(commands.declared()) == {"docs"}
        assert commands.lookup("build") is None


class TestOrigins:
    """Script scope, module attribution, and what survives a re-run."""

    def test_outside_a_scope_a_declaration_is_a_modules(self) -> None:
        pcons.cli_command("one")(as_module("mine", lambda: None))

        assert commands.declared()["one"][0].origin == "module:mine"

    def test_inside_a_scope_an_add_on_is_still_the_modules(self) -> None:
        """A module loaded *during* a script run is the module's, not the
        script's: attributed to the script it would be written into the
        persisted listing and dropped on the next run's way in."""
        with commands.script_scope():
            pcons.cli_command("one")(as_module("mine", lambda: None))

        assert commands.declared()["one"][0].origin == "module:mine"

    def test_inside_a_scope_the_script_body_is_the_scripts(self) -> None:
        with commands.script_scope():
            pcons.cli_command("one")(as_script_body(lambda: None))

        assert commands.declared()["one"][0].origin == "script"

    def test_script_scope_drops_the_previous_runs_declarations(self) -> None:
        """What the script's own body declared, and only that: a re-exec
        re-declares it, so the previous run's copy is stale."""
        with commands.script_scope():
            pcons.cli_command("gone")(as_script_body(lambda: None))
        with commands.script_scope():
            pcons.cli_command("here")(as_script_body(lambda: None))

        assert set(commands.declared()) == {"here"}

    def test_a_helper_modules_declaration_survives_a_re_run(self) -> None:
        """A module the script imports is already in `sys.modules` on the second
        run: its body does not run again and its decorator never fires again, so
        dropping it here would lose it for the rest of the process."""
        helper = as_module("tasks", lambda: None)
        helper.__module__ = "tasks"  # imported by the script, not an add-on

        with commands.script_scope():
            pcons.cli_command("from-helper")(helper)
            pcons.cli_command("from-body")(as_script_body(lambda: None))
        with commands.script_scope():
            pcons.cli_command("from-body")(as_script_body(lambda: None))

        assert set(commands.declared()) == {"from-helper", "from-body"}

    def test_script_scope_keeps_module_declarations(self) -> None:
        pcons.cli_command("from-module")(as_module("mine", lambda: None))

        with commands.script_scope():
            pcons.cli_command("from-script")(lambda: None)
        with commands.script_scope():
            # Nested: re-entering must not cost the module declaration either.
            with commands.script_scope():
                pcons.cli_command("from-script")(lambda: None)

        assert set(commands.declared()) == {"from-module", "from-script"}
        assert commands.declared()["from-module"][0].origin == "module:mine"

    def test_a_nested_scope_keeps_the_enclosing_scripts_declarations(self) -> None:
        """Only the outermost scope means "a fresh script"."""
        with commands.script_scope():
            pcons.cli_command("outer")(lambda: None)
            with commands.script_scope():
                pcons.cli_command("inner")(lambda: None)

            assert set(commands.declared()) == {"outer", "inner"}

    def test_a_sub_scripts_declaration_is_a_script_bodys(self) -> None:
        """`add_subdirectory` runs a sub-script through `runpy.run_path` under
        the same `RUN_NAME` as the top-level script. It is re-read on every run
        just like that one, so it must be dropped like one -- otherwise a
        command deleted from a subdirectory never goes away."""
        sub = as_script_body(lambda: None)

        with commands.script_scope():
            pcons.cli_command("from-sub")(sub)
            pcons.cli_command("from-top")(as_script_body(lambda: None))
        with commands.script_scope():
            pcons.cli_command("from-top")(as_script_body(lambda: None))

        assert set(commands.declared()) == {"from-top"}

    def test_the_declaring_module_is_recorded(self) -> None:
        """`declared_in` is what tells a re-declaration from a clash, so its
        value has to be the module that ran the decorator."""
        pcons.cli_command("from-add-on")(as_module("deploy", lambda: None))
        with commands.script_scope():
            pcons.cli_command("from-body")(as_script_body(lambda: None))
            helper = lambda: None  # noqa: E731
            helper.__module__ = "tasks"
            pcons.cli_command("from-helper")(helper)

            entries = commands.declared()

        assert entries["from-add-on"][0].declared_in == "pcons.modules.deploy"
        assert entries["from-body"][0].declared_in == commands.SCRIPT_BODY
        assert entries["from-helper"][0].declared_in == "tasks"

    def test_a_module_and_the_script_may_share_a_name(self) -> None:
        pcons.cli_command("flash")(as_module("mine", lambda: None))
        with commands.script_scope():
            pcons.cli_command("flash")(lambda: None)

            origins = [entry.origin for entry in commands.declared()["flash"]]

        assert origins == ["module:mine", "script"]


class TestConflicts:
    """A duplicate within one origin fails now; across origins, on use."""

    def test_one_origin_declaring_a_name_twice_raises(self) -> None:
        with commands.script_scope():
            pcons.cli_command("flash")(lambda: None)

            with pytest.raises(PconsError, match="flash"):
                pcons.cli_command("flash")(lambda: None)

    def test_one_module_declaring_a_name_twice_raises(self) -> None:
        pcons.cli_command("flash")(as_module("mine", lambda: None))

        with pytest.raises(PconsError, match="flash"):
            pcons.cli_command("flash")(as_module("mine", lambda: None))

    def test_a_module_re_declaring_after_a_script_ran_still_raises(self) -> None:
        """Entering a script scope clears the duplicate tracking, but a module's
        declarations outlive it: they are not what a re-exec re-declares. The
        second one is still the same module declaring one name twice, and the
        check that catches it is not the one the test above exercises."""
        pcons.cli_command("flash")(as_module("mine", lambda: None))
        with commands.script_scope():
            pass

        with pytest.raises(PconsError, match="flash"):
            pcons.cli_command("flash")(as_module("mine", lambda: None))

    def test_an_empty_name_is_refused(self) -> None:
        """click fills a missing name in from the function, so the reachable
        mistake is the empty spelling, not the absent one. Registering it would
        put a command in the listing that no `pcons run` line can name."""
        with pytest.raises(PconsError, match="must have a name"):
            pcons.cli_command("")(lambda: None)

    def test_a_helper_and_the_script_body_clashing_raises_on_every_run(self) -> None:
        """The helper's decorator fires once and is then cached in sys.modules,
        while the body re-declares every run. Reported on the first run and
        silently resolved afterwards would be the worst of both."""
        helper = lambda: None  # noqa: E731
        helper.__module__ = "tasks"

        for run in range(3):
            with commands.script_scope():
                if run == 0:  # only the first import runs the helper's body
                    pcons.cli_command("foo")(helper)

                with pytest.raises(PconsError, match=f"tasks.*{commands.SCRIPT_BODY}"):
                    pcons.cli_command("foo")(as_script_body(lambda: None))

        # The helper's is the one still standing, on every run.
        (entry,) = commands.declared()["foo"]
        assert entry.declared_in == "tasks"

    def test_the_same_module_re_declaring_replaces_rather_than_raises(self) -> None:
        """A helper deliberately reloaded re-runs its decorator. That is a
        re-run, not a clash, and failing the build script over it would be
        wrong."""
        first = lambda: None  # noqa: E731
        first.__module__ = "tasks"
        second = lambda: None  # noqa: E731
        second.__module__ = "tasks"

        with commands.script_scope():
            command = pcons.cli_command("foo")(first)
        with commands.script_scope():
            replacement = pcons.cli_command("foo")(second)

        (entry,) = commands.declared()["foo"]
        assert entry.command is replacement
        assert entry.command is not command

    def test_two_modules_declaring_a_name_is_reported_on_use(self) -> None:
        pcons.cli_command("flash")(as_module("one", lambda: None))
        pcons.cli_command("flash")(as_module("two", lambda: None))

        with pytest.raises(PconsError, match="module:one.*module:two"):
            commands.lookup("flash")

    def test_a_conflict_names_both_origins(self) -> None:
        pcons.cli_command("flash")(as_module("mine", lambda: None))
        with commands.script_scope():
            pcons.cli_command("flash")(lambda: None)
            pcons.cli_command("other")(lambda: None)

            with pytest.raises(PconsError) as excinfo:
                commands.lookup("flash")

            # Every other name still resolves: one clash does not poison the rest.
            assert commands.lookup("other") is not None

        message = str(excinfo.value)
        assert "module:mine" in message
        assert "script" in message

    def test_an_unknown_name_is_not_an_error(self) -> None:
        """`pcons run nope` is click's "No such command", not a pcons failure."""
        assert commands.lookup("nope") is None


class TestUnloadingModules:
    """Unloading a module drops what it declared, or reloading it fails."""

    def test_clear_module_declarations_keeps_the_scripts(self) -> None:
        pcons.cli_command("from-module")(as_module("mine", lambda: None))
        with commands.script_scope():
            pcons.cli_command("from-script")(lambda: None)

            commands.clear_module_declarations()

            assert set(commands.declared()) == {"from-script"}

    def test_reloading_a_module_redeclares_cleanly(self, tmp_path: Path) -> None:
        """The real shape of it: `load_modules` swallows a PconsError from
        `register()`, so a leftover declaration would abandon the rest of that
        register() and keep the command object from the first load."""
        from pcons import modules

        module_dir = tmp_path / "mods"
        module_dir.mkdir()
        (module_dir / "deploy.py").write_text(
            "import pcons\n\n\ndef register():\n"
            "    @pcons.cli_command()\n"
            "    def deploy():\n"
            '        "Deploy it."\n'
        )

        try:
            modules.load_modules([module_dir])
            first = commands.lookup("deploy")
            modules.clear_modules()

            assert commands.lookup("deploy") is None

            modules.load_modules([module_dir])
            second = commands.lookup("deploy")

            assert second is not None
            assert second is not first
        finally:
            modules.clear_modules()


class TestRegistryHousekeeping:
    def test_clear_forgets_what_the_current_run_declared(self) -> None:
        """`clear()` has to reset the duplicate tracking too, or the next
        declaration of a name this run already saw is refused."""
        with commands.script_scope():
            pcons.cli_command("flash")(as_script_body(lambda: None))
            commands.clear()

            pcons.cli_command("flash")(as_script_body(lambda: None))

            assert set(commands.declared()) == {"flash"}

    def test_clear_empties_the_registry(self) -> None:
        pcons.cli_command("one")(lambda: None)
        commands.clear()

        assert commands.declared() == {}

    def test_declared_hands_back_a_copy(self) -> None:
        pcons.cli_command("one")(lambda: None)

        snapshot = commands.declared()
        snapshot.clear()

        assert set(commands.declared()) == {"one"}


class TestProjectSugar:
    """`project.cli_command` is the same registry, reached differently."""

    def test_project_cli_command_registers(self, tmp_path: Path) -> None:
        project = pcons.Project("app", root_dir=tmp_path)

        @project.cli_command()
        def flash() -> None:
            """Flash the board."""

        assert commands.lookup("flash") is flash

    def test_project_cli_group_registers(self, tmp_path: Path) -> None:
        project = pcons.Project("app", root_dir=tmp_path)

        @project.cli_group()
        def docs() -> None:
            """Documentation tasks."""

        assert isinstance(docs, click.Group)
        assert commands.lookup("docs") is docs

    def test_the_registry_records_no_project(self, tmp_path: Path) -> None:
        """The callback reaches its project by closing over it, not through us.

        The entry holds the command and an origin, and nothing else: adding a
        project would mean a second registry for the module commands that have
        none.
        """
        project = pcons.Project("app", root_dir=tmp_path)

        @project.cli_command()
        def flash() -> None:
            """Flash the board."""

        (entry,) = commands.declared()["flash"]
        assert entry.command is flash
        # Where it came from, and nothing about which project was in scope.
        assert [f.name for f in dataclasses.fields(entry)] == [
            "command",
            "origin",
            "declared_in",
        ]


class TestDeclaringDependencies:
    """`depends`: targets to build before `pcons run <name>` dispatches.

    Recording only. What reads it is `RunGroup`, covered in tests/test_cli.py.
    """

    @pytest.fixture(autouse=True)
    def _project(self, tmp_path: Path) -> Iterator[None]:
        """`Target` reads the current project at construction."""
        pcons.Project("app", root_dir=tmp_path)
        yield

    @staticmethod
    def _target(name: str) -> Target:
        return Target(name)

    def test_a_declared_target_is_recorded(self) -> None:
        firmware = self._target("firmware")

        @pcons.cli_command()
        def flash() -> None:
            """Flash it."""

        flash.depends(firmware)

        assert flash.declared_dependencies() == [firmware]

    def test_declaring_nothing_gives_an_empty_list(self) -> None:
        @pcons.cli_command()
        def flash() -> None:
            """Flash it."""

        assert flash.declared_dependencies() == []

    def test_several_in_one_call_keep_their_order(self) -> None:
        first, second = self._target("a"), self._target("b")

        @pcons.cli_command()
        def flash() -> None:
            """Flash it."""

        flash.depends(first, second)

        assert flash.declared_dependencies() == [first, second]

    def test_a_second_call_adds_rather_than_replaces(self) -> None:
        first, second = self._target("a"), self._target("b")

        @pcons.cli_command()
        def flash() -> None:
            """Flash it."""

        flash.depends(first)
        flash.depends(second)

        assert flash.declared_dependencies() == [first, second]

    def test_the_list_is_a_copy(self) -> None:
        """Mutating what a caller was handed must not reach the command."""
        firmware = self._target("firmware")

        @pcons.cli_command()
        def flash() -> None:
            """Flash it."""

        flash.depends(firmware)
        flash.declared_dependencies().clear()

        assert flash.declared_dependencies() == [firmware]

    def test_a_group_declares_them_too(self) -> None:
        firmware = self._target("firmware")

        @pcons.cli_group()
        def board() -> None:
            """Board tasks."""

        board.depends(firmware)

        assert board.declared_dependencies() == [firmware]

    def test_a_groups_verb_is_a_plain_click_command(self) -> None:
        """A verb declares nothing of its own; the group's cover it.

        Pinned so that giving verbs their own dependencies is a deliberate
        change rather than an accident.
        """

        @pcons.cli_group()
        def board() -> None:
            """Board tasks."""

        @board.command("flash")
        def board_flash() -> None:
            """Flash it."""

        assert not hasattr(board_flash, "depends")

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("firmware", id="a-name"),
            pytest.param(Path("build/firmware.elf"), id="a-path"),
            pytest.param(None, id="none"),
        ],
    )
    def test_anything_but_a_target_raises(self, bad: object) -> None:
        """A Target and nothing else: a name or a path would need resolving
        against a project this registry deliberately does not hold."""

        @pcons.cli_command()
        def flash() -> None:
            """Flash it."""

        with pytest.raises(PconsError, match="takes a Target"):
            flash.depends(bad)  # type: ignore[arg-type]

    def test_a_rejected_argument_records_nothing(self) -> None:
        good = self._target("firmware")

        @pcons.cli_command()
        def flash() -> None:
            """Flash it."""

        with pytest.raises(PconsError):
            flash.depends(good, "bad")  # type: ignore[arg-type]

        assert flash.declared_dependencies() == []

    def test_an_overridden_cls_simply_has_none(self) -> None:
        """`cls=` is an escape hatch, not a supported way to get `depends`."""

        class Mine(click.Command):
            pass

        @pcons.cli_command(cls=Mine)
        def flash() -> None:
            """Flash it."""

        assert not hasattr(flash, "depends")


def test_the_public_spelling_is_re_exported() -> None:
    """A module's `register()` has no project, so this spelling must exist."""
    assert pcons.cli_command is commands.cli_command
    assert pcons.cli_group is commands.cli_group
    assert "cli_command" in pcons.__all__
    assert "cli_group" in pcons.__all__
