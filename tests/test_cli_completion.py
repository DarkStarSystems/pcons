# SPDX-License-Identifier: MIT
"""Tests for `pcons completion`, the shell completion commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.shell_completion import ShellComplete
from click.testing import CliRunner, Result

from pcons._cli_completion import (
    COMPLETE_VAR,
    PROG_NAME,
    SHELLS,
    add_block,
    layout,
    remove_block,
)
from pcons.cli import cli


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
