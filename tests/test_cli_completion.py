# SPDX-License-Identifier: MIT
"""Tests for `pcons completion`, the shell completion commands."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.shell_completion import ShellComplete
from click.testing import CliRunner, Result

from pcons._cli_click import _cached_names, _debug_help, _declared_build_dir
from pcons._cli_completion import (
    COMPLETE_VAR,
    PROG_NAME,
    SHELLS,
    add_block,
    layout,
    remove_block,
)
from pcons.cli import cli
from pcons.core.debug import SUBSYSTEM_DESCRIPTIONS


@pytest.fixture(autouse=True)
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every install location at a scratch home.

    `Path.home` rather than ``HOME``, because the variable read differs per
    platform and these tests must never touch the real one.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _invoke(*argv: str, stdin: str = "") -> Result:
    return CliRunner().invoke(cli, list(argv), input=stdin, catch_exceptions=False)


def _completions(args: list[str], incomplete: str) -> list[str]:
    """What the shell would be offered for a partly typed command line."""
    complete = ShellComplete(cli, {}, PROG_NAME, COMPLETE_VAR)
    return [item.value for item in complete.get_completions(args, incomplete)]


def _completion_types(args: list[str], incomplete: str) -> list[str]:
    """The kinds offered, rather than the values.

    A path candidate is a directive rather than a name: click emits
    ``CompletionItem(incomplete, type="dir")`` and every shell script turns
    that into its own path completion, ignoring the value. So `_completions`
    sees nothing for these and only the type says what happened.
    """
    complete = ShellComplete(cli, {}, PROG_NAME, COMPLETE_VAR)
    return [item.type for item in complete.get_completions(args, incomplete)]


class TestCompletionShow:
    """`pcons completion show` prints a script and writes nothing."""

    @pytest.mark.parametrize(
        ("shell", "marker"),
        [
            ("bash", "complete -o nosort -F _pcons_completion pcons"),
            ("zsh", "compdef _pcons_completion pcons"),
            ("fish", "complete --no-files --command pcons"),
        ],
    )
    def test_prints_the_script_for_a_named_shell(self, shell: str, marker: str) -> None:
        result = _invoke("completion", "show", shell)
        assert result.exit_code == 0
        assert marker in result.output
        assert COMPLETE_VAR in result.output

    def test_writes_nothing(self, fake_home: Path) -> None:
        assert _invoke("completion", "show", "zsh").exit_code == 0
        assert list(fake_home.iterdir()) == []

    def test_detects_the_running_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
        result = _invoke("completion", "show")
        assert result.exit_code == 0
        assert "complete --no-files --command pcons" in result.output

    def test_undetectable_shell_fails_without_guessing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SHELL", raising=False)
        result = _invoke("completion", "show")
        # 1, not click's 2: nothing was mistyped, so no usage line either.
        assert result.exit_code == 1
        assert "SHELL is not set" in result.output
        assert "Usage:" not in result.output

    def test_unsupported_running_shell_names_the_supported_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SHELL", "/bin/dash")
        result = _invoke("completion", "show")
        assert result.exit_code == 1
        assert "no completion support for dash" in result.output
        for shell in SHELLS:
            assert shell in result.output

    def test_unsupported_named_shell_is_a_usage_error(self) -> None:
        result = _invoke("completion", "show", "tcsh")
        assert result.exit_code == 2
        assert "'tcsh' is not one of" in result.output


class TestCompletionInstall:
    """`pcons completion install` writes the script and wires it up."""

    def test_fish_needs_no_startup_file(self, fake_home: Path) -> None:
        result = _invoke("completion", "install", "fish", "--yes")
        assert result.exit_code == 0
        script = fake_home / ".config" / "fish" / "completions" / "pcons.fish"
        assert "complete --no-files --command pcons" in script.read_text()
        # No rc line is mentioned because fish reads the directory itself.
        assert "add these lines" not in result.output

    def test_zsh_keeps_what_the_rc_file_already_had(self, fake_home: Path) -> None:
        rc = fake_home / ".zshrc"
        rc.write_text("export FOO=1\n")
        assert _invoke("completion", "install", "zsh", "-y").exit_code == 0
        assert (fake_home / ".zfunc" / "_pcons").is_file()
        content = rc.read_text()
        assert content.startswith("export FOO=1\n")
        assert 'fpath=("$HOME/.zfunc" $fpath)' in content
        assert "compinit" in content

    def test_installing_twice_changes_the_rc_file_once(self, fake_home: Path) -> None:
        rc = fake_home / ".zshrc"
        rc.write_text("export FOO=1\n")
        _invoke("completion", "install", "zsh", "-y")
        after_first = rc.read_text()
        result = _invoke("completion", "install", "zsh", "-y")
        assert rc.read_text() == after_first
        assert "Already wired up" in result.output

    def test_an_rc_file_without_a_trailing_newline_gains_one(
        self, fake_home: Path
    ) -> None:
        rc = fake_home / ".bashrc"
        rc.write_text("export FOO=1")
        _invoke("completion", "install", "bash", "-y")
        assert rc.read_text().startswith("export FOO=1\n#")

    def test_a_missing_rc_file_is_created(self, fake_home: Path) -> None:
        _invoke("completion", "install", "bash", "-y")
        rc = fake_home / ".bashrc"
        assert 'source "$HOME/.bash_completions/pcons.sh"' in rc.read_text()

    def test_it_says_what_it_will_write_before_asking(self, fake_home: Path) -> None:
        result = _invoke("completion", "install", "bash", stdin="y\n")
        assert result.exit_code == 0
        target = fake_home / ".bash_completions" / "pcons.sh"
        rc = fake_home / ".bashrc"
        # Both paths and the exact lines, before the prompt.
        plan, _, _ = result.output.partition("Continue?")
        assert str(target) in plan
        assert str(rc) in plan
        assert 'source "$HOME/.bash_completions/pcons.sh"' in plan

    def test_declining_writes_nothing(self, fake_home: Path) -> None:
        result = _invoke("completion", "install", "bash", stdin="n\n")
        assert result.exit_code == 1
        assert "Nothing was installed." in result.output
        assert list(fake_home.iterdir()) == []


class TestCompletionUninstall:
    """`pcons completion uninstall` takes back exactly what install wrote."""

    def test_it_removes_the_script_and_the_startup_lines(self, fake_home: Path) -> None:
        rc = fake_home / ".zshrc"
        rc.write_text("export FOO=1\n")
        _invoke("completion", "install", "zsh", "-y")
        result = _invoke("completion", "uninstall", "zsh")
        assert result.exit_code == 0
        assert not (fake_home / ".zfunc" / "_pcons").exists()
        assert rc.read_text() == "export FOO=1\n"

    def test_it_keeps_what_the_user_added_after_the_block(
        self, fake_home: Path
    ) -> None:
        rc = fake_home / ".bashrc"
        rc.write_text("before\n")
        _invoke("completion", "install", "bash", "-y")
        rc.write_text(rc.read_text() + "after\n")
        _invoke("completion", "uninstall", "bash")
        assert rc.read_text() == "before\nafter\n"

    def test_uninstalling_nothing_says_so(self) -> None:
        result = _invoke("completion", "uninstall", "fish")
        assert result.exit_code == 0
        assert "No fish completion was installed." in result.output

    def test_an_rc_file_without_a_block_is_left_alone(self, fake_home: Path) -> None:
        rc = fake_home / ".bashrc"
        rc.write_text("mine\n")
        result = _invoke("completion", "uninstall", "bash")
        assert rc.read_text() == "mine\n"
        assert "No bash completion was installed." in result.output


class TestRcBlock:
    """The rc edit is idempotent, replaceable and reversible."""

    def test_a_stale_block_is_replaced_rather_than_repeated(self) -> None:
        content, _ = add_block("keep\n", ("old line",))
        updated, changed = add_block(content, ("new line",))
        assert changed
        assert updated.count("# >>> pcons completion >>>") == 1
        assert "old line" not in updated
        assert updated.startswith("keep\n")

    def test_an_unchanged_block_rewrites_nothing(self) -> None:
        content, _ = add_block("keep\n", ("line",))
        updated, changed = add_block(content, ("line",))
        assert not changed
        assert updated == content

    def test_a_block_at_the_end_without_a_final_newline(self) -> None:
        content, _ = add_block("keep\n", ("line",))
        updated, changed = remove_block(content.rstrip("\n"))
        assert changed
        assert updated == "keep\n"

    def test_removing_a_block_that_is_not_there(self) -> None:
        updated, changed = remove_block("keep\n")
        assert not changed
        assert updated == "keep\n"

    def test_a_truncated_block_is_left_alone(self) -> None:
        # The end delimiter lost to a hand edit. Rewriting from the start
        # delimiter alone would eat the rest of the file.
        content = "keep\n# >>> pcons completion >>>\nfpath=(...)\nexport FOO=1\n"
        updated, changed = remove_block(content)
        assert not changed
        assert updated == content


class TestLineEndings:
    """An rc file keeps the endings it had, and scripts are written LF.

    Text mode writes `os.linesep`, so on Windows editing an LF `.bashrc` would
    return it entirely CRLF, and msys shells read the `\\r` as part of the
    command.
    """

    def test_a_crlf_rc_file_stays_crlf(self, fake_home: Path) -> None:
        rc = fake_home / ".bashrc"
        rc.write_bytes(b"export FOO=1\r\n")
        _invoke("completion", "install", "bash", "-y")
        content = rc.read_bytes()
        assert b"\r\n" in content
        assert b"\n" not in content.replace(b"\r\n", b"")

    def test_an_lf_rc_file_stays_lf(self, fake_home: Path) -> None:
        rc = fake_home / ".bashrc"
        rc.write_bytes(b"export FOO=1\n")
        _invoke("completion", "install", "bash", "-y")
        assert b"\r" not in rc.read_bytes()

    def test_uninstall_keeps_the_endings_install_found(self, fake_home: Path) -> None:
        rc = fake_home / ".zshrc"
        rc.write_bytes(b"export FOO=1\r\n")
        _invoke("completion", "install", "zsh", "-y")
        _invoke("completion", "uninstall", "zsh")
        assert rc.read_bytes() == b"export FOO=1\r\n"

    @pytest.mark.parametrize("shell", SHELLS)
    def test_the_script_is_written_lf(self, shell: str) -> None:
        _invoke("completion", "install", shell, "-y")
        assert b"\r" not in layout(shell).script.read_bytes()

    def test_a_new_rc_file_is_written_lf(self, fake_home: Path) -> None:
        _invoke("completion", "install", "bash", "-y")
        assert b"\r" not in (fake_home / ".bashrc").read_bytes()


class TestCompletionLayout:
    """Every shell gets a location, and only fish needs no startup file."""

    @pytest.mark.parametrize("shell", SHELLS)
    def test_the_script_lands_under_the_home_directory(
        self, shell: str, fake_home: Path
    ) -> None:
        target = layout(shell)
        assert target.shell == shell
        assert fake_home in target.script.parents
        assert (target.rc is None) == (shell == "fish")
        assert (target.rc_lines == ()) == (shell == "fish")


class TestWhatCompletes:
    """What the generated script offers, which is click reading the tree."""

    def test_the_command_names(self) -> None:
        names = _completions([], "")
        assert "generate" in names
        assert "completion" in names

    def test_the_catch_all_command_is_not_offered(self) -> None:
        # Its name is not part of the interface: `pcons _default` is a target.
        assert "_default" not in _completions([], "")
        assert _completions([], "_def") == []

    def test_a_prefix_filters_the_names(self) -> None:
        assert _completions([], "gen") == ["generate"]

    def test_the_generator_names(self) -> None:
        assert "ninja" in _completions(["generate", "-G"], "")

    def test_a_hidden_option_is_not_offered(self) -> None:
        options = _completions(["generate"], "--")
        assert "--reconfigure" in options
        assert "--no-cache" not in options

    def test_the_completion_verbs(self) -> None:
        assert _completions(["completion"], "") == ["show", "install", "uninstall"]


class TestPathCompletion:
    """An option naming a path hands the shell its own path completion."""

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["-C"], "dir"),
            (["--directory"], "dir"),
            (["-B"], "dir"),
            (["--build-dir"], "dir"),
            (["--modules-path"], "dir"),
            (["generate", "-b"], "file"),
            (["generate", "--build-script"], "file"),
            (["generate", "--graph"], "file"),
            (["generate", "--mermaid"], "file"),
        ],
    )
    def test_the_kind_offered(self, args: list[str], expected: str) -> None:
        assert _completion_types(args, "") == [expected]

    def test_a_build_dir_offers_no_files(self) -> None:
        # click.Path's own completion would say "file" here, because its
        # file_okay defaults to True.
        assert "file" not in _completion_types(["-B"], "")

    def test_after_a_command_name_too(self) -> None:
        assert _completion_types(["build", "-C"], "") == ["dir"]
        assert _completion_types(["build", "-B"], "") == ["dir"]

    def test_a_partly_typed_path_is_still_a_directive(self) -> None:
        # The shell completes the whole word itself, so the value is passed
        # through untouched rather than filtered.
        assert _completions(["-C"], "sub/dir") == ["sub/dir"]


class TestPathCompletionAfterADirectoryOption:
    """A `-C` moves the directory a later path option is read from.

    The shell would answer out of its own, so pcons lists the entries itself.
    `-C` is applied while completing, exactly as it is while running: click
    calls an eager callback under resilient parsing too.
    """

    @pytest.fixture
    def tree(self, tmp_path: Path) -> Iterator[str]:
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "sub").mkdir(parents=True)
        (elsewhere / "hidden_marker").mkdir()
        (elsewhere / ".dotted").mkdir()
        (elsewhere / "note.txt").write_text("")
        (tmp_path / "shell_cwd_only").mkdir()

        origin = Path.cwd()
        os.chdir(tmp_path)
        try:
            yield str(elsewhere)
        finally:
            os.chdir(origin)

    def test_the_directories_under_it(self, tree: str) -> None:
        assert _completions(["-C", tree, "-B"], "") == [
            "hidden_marker" + os.sep,
            "sub" + os.sep,
        ]

    def test_not_the_ones_the_shell_would_have_offered(self, tree: str) -> None:
        assert "shell_cwd_only" + os.sep not in _completions(["-C", tree, "-B"], "")

    def test_a_name_rather_than_a_directive(self, tree: str) -> None:
        assert _completion_types(["-C", tree, "-B"], "") == ["plain", "plain"]

    def test_a_file_option_offers_the_files(self, tree: str) -> None:
        assert _completions(["-C", tree, "generate", "--graph"], "") == ["note.txt"]

    def test_a_prefix_filters(self, tree: str) -> None:
        assert _completions(["-C", tree, "-B"], "s") == ["sub" + os.sep]

    def test_a_dotted_entry_needs_the_dot_typed(self, tree: str) -> None:
        assert _completions(["-C", tree, "-B"], "") == [
            "hidden_marker" + os.sep,
            "sub" + os.sep,
        ]
        assert _completions(["-C", tree, "-B"], ".") == [".dotted" + os.sep]

    def test_the_separated_list_too(self, tree: str) -> None:
        assert _completions(["-C", tree, "--modules-path"], "s") == ["sub" + os.sep]

    def test_a_missing_directory_offers_nothing(self, tree: str) -> None:
        assert _completions(["-C", tree, "-B"], "no_such_dir/") == []


class TestValueCompletion:
    """Options whose values pcons knows by name, rather than by type."""

    def test_every_debug_subsystem(self) -> None:
        names = _completions(["--debug"], "")
        assert set(names) == set(SUBSYSTEM_DESCRIPTIONS) | {"all", "help"}

    def test_a_debug_prefix_filters(self) -> None:
        assert _completions(["--debug"], "re") == ["resolve"]

    def test_a_debug_segment_keeps_what_was_typed_before_it(self) -> None:
        assert _completions(["--debug"], "env,su") == ["env,subst"]

    def test_a_debug_subsystem_carries_its_description(self) -> None:
        complete = ShellComplete(cli, {}, PROG_NAME, COMPLETE_VAR)
        offered = {i.value: i.help for i in complete.get_completions(["--debug"], "")}
        assert offered["resolve"] == SUBSYSTEM_DESCRIPTIONS["resolve"]

    def test_an_unknown_debug_subsystem_offers_nothing(self) -> None:
        assert _completions(["--debug"], "nonsense") == []

    def test_the_runner_names(self) -> None:
        assert _completions(["--ninja"], "") == ["ninja", "n2"]
        assert _completions(["--ninja"], "n2") == ["n2"]

    def test_no_files_are_offered_for_a_runner(self) -> None:
        assert "file" not in _completion_types(["--ninja"], "")

    @pytest.mark.parametrize("command", ["build", "generate"])
    def test_after_a_command_name_too(self, command: str) -> None:
        assert "resolve" in _completions([command, "--debug"], "")

    def test_the_help_text_still_names_every_accepted_value(self) -> None:
        # The help and the completer read one list, so neither can drift.
        assert (
            _debug_help() == "Enable debug tracing for subsystems (comma-separated): "
            "configure,resolve,generate,subst,env,deps,all,help"
        )


class TestTargetCompletion:
    """Target names come from what the last generate recorded, not from a run.

    The fixture generates a real project rather than hand-writing a cache: a
    hand-written one would pass even if pcons recorded the wrong names.
    """

    @pytest.fixture
    def project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[Path]:
        from pcons.cli import run_script
        from pcons.core.vars import _clear_cli_vars

        root = tmp_path / "project"
        root.mkdir()
        (root / "hello.c").write_text("int main(void) { return 0; }\n")
        script = root / "pcons-build.py"
        script.write_text(
            "from pcons import Project\n"
            "p = Project('demo')\n"
            "p.Program('hello', p.Environment(toolchain='c'), sources=['hello.c'])\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()
        assert run_script(script, root / "build")[0] == 0
        monkeypatch.chdir(root)
        yield root

    def test_after_a_command_name(self, project: Path) -> None:
        assert _completions(["build"], "") == ["all", "hello"]

    def test_a_prefix_filters(self, project: Path) -> None:
        assert _completions(["build"], "hel") == ["hello"]

    def test_explain_offers_them_too(self, project: Path) -> None:
        assert _completions(["explain"], "") == ["all", "hello"]

    def test_at_the_top_level(self, project: Path) -> None:
        """`pcons hello` builds a target, so the group offers the names itself."""
        assert _completions([], "hel") == ["hello"]

    def test_the_command_names_survive_at_the_top_level(self, project: Path) -> None:
        offered = _completions([], "")
        assert "build" in offered
        assert "hello" in offered

    def test_an_option_prefix_offers_no_targets(self, project: Path) -> None:
        offered = _completions([], "-")
        assert "--verbose" in offered
        assert "hello" not in offered

    @pytest.mark.parametrize(
        "args",
        [["build"], ["-B", "out", "build"], ["build", "-B", "out"]],
        ids=["default", "before-the-command", "after-the-command"],
    )
    def test_the_build_dir_is_resolved_however_it_was_spelled(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
    ) -> None:
        """`invoke` merges an option spelled before the command name, and
        completion never calls it, so the completer has to do it itself."""
        from pcons.cli import run_script
        from pcons.core.project import Project
        from pcons.core.vars import _clear_cli_vars

        (project / "pcons-build.py").write_text(
            "from pcons import Project\n"
            "p = Project('demo')\n"
            "p.Program('elsewhere', p.Environment(toolchain='c'), sources=['hello.c'])\n"
        )
        _clear_cli_vars()
        Project._clear_tree()
        assert run_script(project / "pcons-build.py", project / "out")[0] == 0

        wanted = "hello" if args == ["build"] else "elsewhere"
        assert _completions(args, "") == ["all", wanted]

    def test_the_build_dir_is_read_from_the_environment(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PCONS_BUILD_DIR", "nowhere")
        assert _completions(["build"], "") == []

    def test_a_build_dir_that_never_generated_offers_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        assert _completions(["build"], "") == []
        assert _completions([], "hel") == []

    @pytest.mark.parametrize(
        "content",
        ["not json at all", '{"targets": "hello"}', '{"targets": [1, 2]}'],
        ids=["corrupt", "not-a-list", "not-strings"],
    )
    def test_a_cache_it_cannot_use_offers_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
    ) -> None:
        """stdout is the candidate stream, so a bad cache must be silent."""
        from pcons.core.cache import CACHE_FILE

        root = tmp_path / "broken"
        (root / "build").mkdir(parents=True)
        (root / "build" / CACHE_FILE).write_text(content)
        monkeypatch.chdir(root)
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        assert _completions(["build"], "") == []

    def test_a_variant_completes_once_a_run_has_named_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pcons.cli import run_script
        from pcons.core.vars import _clear_cli_vars

        root = tmp_path / "variants"
        root.mkdir()
        (root / "hello.c").write_text("int main(void) { return 0; }\n")
        script = root / "pcons-build.py"
        script.write_text(
            "from pcons import Project\n"
            "p = Project('demo')\n"
            "for v in ('debug', 'release'):\n"
            "    e = p.Environment(toolchain='c')\n"
            "    e.set_variant(v)\n"
            "    prog = p.Program('demo_' + v, e, sources=['hello.c'])\n"
            "    prog.output_prefix = v + '/'\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()
        assert run_script(script, root / "build")[0] == 0
        monkeypatch.chdir(root)

        assert _completions(["--variant"], "") == ["debug", "release"]
        assert _completions(["--variant"], "d") == ["debug"]
        assert _completions(["build", "--variant"], "") == ["debug", "release"]

    def test_a_variant_offers_nothing_before_any_run_named_one(
        self, project: Path
    ) -> None:
        """The fixture's script never calls set_variant, so there is nothing."""
        assert _completions(["--variant"], "") == []

    def test_completing_does_not_run_the_build_script(self, project: Path) -> None:
        """A build script does configure checks, and completion fires per key."""
        marker = project / "ran"
        (project / "pcons-build.py").write_text(
            "from pathlib import Path\n"
            "Path('ran').write_text('yes')\n"
            "from pcons import Project\n"
            "Project('demo')\n"
        )
        assert _completions(["build"], "") == ["all", "hello"]
        assert _completions([], "hel") == ["hello"]
        assert not marker.exists()


class TestCompletionAfterADoubleDash:
    """After `--` every word names a target, so only target names are offered.

    `resolve_command` routes the rest to the catch-all, which hands it to the
    build tool, so offering an option or a command name there completes a word
    pcons will never parse as one.
    """

    @pytest.fixture
    def project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[Path]:
        from pcons.cli import run_script
        from pcons.core.vars import _clear_cli_vars

        root = tmp_path / "project"
        root.mkdir()
        (root / "hello.c").write_text("int main(void) { return 0; }\n")
        script = root / "pcons-build.py"
        script.write_text(
            "from pcons import Project\n"
            "p = Project('demo')\n"
            "e = p.Environment(toolchain='c')\n"
            "p.Program('hello', e, sources=['hello.c'])\n"
            "dashed = p.Program('dashed', e, sources=['hello.c'])\n"
            "dashed.output_name = '-dash-target'\n"
        )
        monkeypatch.delenv("PCONS_BUILD_DIR", raising=False)
        _clear_cli_vars()
        assert run_script(script, root / "build")[0] == 0
        monkeypatch.chdir(root)
        yield root

    def test_an_option_is_not_offered(self, project: Path) -> None:
        assert _completions(["--"], "--ver") == []

    def test_without_the_double_dash_it_still_is(self, project: Path) -> None:
        assert _completions([], "--ver") == ["--version", "--verbose"]

    def test_a_command_name_is_not_offered(self, project: Path) -> None:
        """`pcons -- build` builds a target called build, it runs no command."""
        offered = _completions(["--"], "")
        assert "build" not in offered
        assert "hello" in offered

    def test_without_the_double_dash_a_command_name_is(self, project: Path) -> None:
        assert "build" in _completions([], "")

    def test_a_target_whose_name_starts_with_a_dash_is_offered(
        self, project: Path
    ) -> None:
        """The case `--` exists for, so it must survive the option refusal."""
        assert _completions(["--"], "-") == ["-dash-target"]

    def test_a_command_after_a_double_dash_was_already_right(
        self, project: Path
    ) -> None:
        """A command falls through to its EXTRA argument before reaching the
        option list, so only the group ever had this wrong."""
        assert _completions(["build", "--"], "--ver") == []


class TestTheBuildDirBehindTheNames:
    """Where `_cached_names` looks, when nothing has parsed a `-B` yet.

    `--help` is eager and fires from inside `parse_args`, so the level it runs
    on has an empty `ctx.params`. `_declared_build_dir` reads the option's own
    declaration instead. It answers for a context whatever the command tree
    around that context looks like, and `_cached_names` never raises whatever
    it gets back, because for completion stdout is the candidate stream.
    """

    @staticmethod
    def _without_a_build_dir() -> click.Command:
        command = cli.commands["completion"]
        assert all(param.name != "build_dir" for param in command.params)
        return command

    def test_a_command_of_its_own_answers_from_the_group_above_it(self) -> None:
        parent = click.Context(cli)
        child = click.Context(self._without_a_build_dir(), parent=parent)
        assert _declared_build_dir(child) == "build"

    def test_nothing_declaring_one_answers_nothing(self) -> None:
        assert _declared_build_dir(click.Context(self._without_a_build_dir())) is None

    def test_no_build_dir_at_all_names_nothing(self) -> None:
        ctx = click.Context(self._without_a_build_dir())
        assert _cached_names(ctx, "targets") == []

    def test_an_unreadable_cache_names_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever the cache does, the shell must not be handed a traceback."""

        def refuse(build_dir: Path) -> None:
            raise OSError("cache unreadable")

        monkeypatch.setattr("pcons.core.cache.BuildCache", refuse)
        ctx = click.Context(cli)
        ctx.params["build_dir"] = tmp_path
        assert _cached_names(ctx, "targets") == []
