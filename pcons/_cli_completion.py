# SPDX-License-Identifier: MIT
"""Shell completion: the script click writes, and where each shell wants it.

click generates the completion script itself, from the command tree, so nothing
here describes pcons' options. What is left is per-shell knowledge: which file
the script goes in, and which line a startup file needs so the shell reads it.

Two tiers, because writing to a file outside the project is a different act from
printing one. `emit` prints the script for a shell to evaluate itself. `install`
writes it, after saying what it is about to write, and `uninstall` takes it back
out.

An rc file is edited by appending one delimited block. The delimiters make the
edit both idempotent, so repeated installs neither duplicate the lines nor
accrete blank lines, and reversible, so uninstall removes what was added and
leaves the rest of the user's file alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import click
from click.shell_completion import get_completion_class

#: What ``[project.scripts]`` installs, which is also what click derives
#: `COMPLETE_VAR` from. Both have to agree with the running program's name, or
#: the script asks for completions from a variable the program never reads.
PROG_NAME = "pcons"

#: The variable click looks at, spelled as `click.Command.main` derives it from
#: `PROG_NAME`.
COMPLETE_VAR = f"_{PROG_NAME.upper()}_COMPLETE"

#: The shells click can write a script for. PowerShell is absent because click
#: has no completion class for it, and hand-writing one is a separate feature.
SHELLS = ("bash", "zsh", "fish")

_BEGIN = f"# >>> {PROG_NAME} completion >>>"
_END = f"# <<< {PROG_NAME} completion <<<"


@dataclass(frozen=True)
class Layout:
    """Where one shell reads its completions from."""

    shell: str

    #: The file the generated script is written to.
    script: Path

    #: The startup file that has to be told about `script`, if any. fish reads
    #: its completions directory on its own, so it needs no line anywhere.
    rc: Path | None

    #: The lines that startup file needs.
    rc_lines: tuple[str, ...]


def layout(shell: str) -> Layout:
    """The install locations for `shell`, resolved against the current home.

    Resolved per call rather than at import, so a test setting ``HOME`` gets its
    own tree.
    """
    home = Path.home()
    if shell == "bash":
        return Layout(
            shell,
            home / ".bash_completions" / f"{PROG_NAME}.sh",
            home / ".bashrc",
            (f'source "$HOME/.bash_completions/{PROG_NAME}.sh"',),
        )
    if shell == "zsh":
        return Layout(
            shell,
            home / ".zfunc" / f"_{PROG_NAME}",
            home / ".zshrc",
            (
                'fpath=("$HOME/.zfunc" $fpath)',
                "autoload -Uz compinit && compinit",
            ),
        )
    return Layout(
        shell,
        home / ".config" / "fish" / "completions" / f"{PROG_NAME}.fish",
        None,
        (),
    )


def script_for(shell: str) -> str:
    """The completion script click generates for `shell`."""
    # Imported here because `pcons.cli` imports this module, not the reverse.
    from pcons.cli import cli

    completion_class = get_completion_class(shell)
    if completion_class is None:  # pragma: no cover - SHELLS is click's own list
        raise click.UsageError(f"no completion support for {shell}")
    return completion_class(cli, {}, PROG_NAME, COMPLETE_VAR).source()


def resolve_shell(shell: str | None) -> str:
    """The shell to act on: the one asked for, else the one being run.

    Detection reads ``SHELL`` and nothing else, so there is no shell-detection
    dependency. Failing loudly is the point: guessing would install completion
    for a shell the user does not run, in a file they would then have to find.
    """
    if shell is not None:
        return shell
    detected = os.environ.get("SHELL")
    if not detected:
        # Not a UsageError: nothing was mistyped, so printing the usage line
        # and exiting 2 would say the wrong thing. This is the environment
        # missing a fact, which is exit 1 like every other pcons failure.
        raise click.ClickException(
            f"cannot detect the shell (SHELL is not set); "
            f"name one of: {', '.join(SHELLS)}"
        )
    name = Path(detected).name
    if name not in SHELLS:
        raise click.ClickException(
            f"no completion support for {name} (SHELL={detected}); "
            f"click writes scripts for: {', '.join(SHELLS)}"
        )
    return name


def _block(lines: tuple[str, ...]) -> str:
    return "\n".join([_BEGIN, *lines, _END]) + "\n"


def _find_block(content: str) -> tuple[int, int] | None:
    """The span of the block this program wrote, delimiters included."""
    start = content.find(_BEGIN)
    if start < 0:
        return None
    end = content.find(_END, start)
    if end < 0:
        return None
    end += len(_END)
    if content[end : end + 1] == "\n":
        end += 1
    return start, end


def add_block(content: str, lines: tuple[str, ...]) -> tuple[str, bool]:
    """`content` with the block present, and whether that changed anything.

    A block already there is replaced when its lines differ, so an install after
    an upgrade updates them, and left alone when they do not. Appending
    normalises the trailing newline first, which is what keeps repeated installs
    from accreting blank lines.
    """
    block = _block(lines)
    span = _find_block(content)
    if span is not None:
        start, end = span
        if content[start:end] == block:
            return content, False
        return content[:start] + block + content[end:], True
    if content and not content.endswith("\n"):
        content += "\n"
    return content + block, True


def remove_block(content: str) -> tuple[str, bool]:
    """`content` without the block, and whether it had one."""
    span = _find_block(content)
    if span is None:
        return content, False
    start, end = span
    return content[:start] + content[end:], True


def emit(shell: str | None) -> int:
    """Print the completion script for `shell` on stdout."""
    click.echo(script_for(resolve_shell(shell)), nl=False)
    return 0


def install(shell: str | None, *, assume_yes: bool) -> int:
    """Write the completion script for `shell`, and wire it up.

    Writing to a startup file is hard to undo and outside the project, so what
    is about to be written is shown first and confirmed, unless the caller
    already said yes.
    """
    target = layout(resolve_shell(shell))
    click.echo(f"Will write the {target.shell} completion script to {target.script}")
    if target.rc is not None:
        click.echo(f"and add these lines to {target.rc}:")
        for line in target.rc_lines:
            click.echo(f"    {line}")
    if not assume_yes and not click.confirm("Continue?", default=True):
        click.echo("Nothing was installed.")
        return 1

    target.script.parent.mkdir(parents=True, exist_ok=True)
    target.script.write_text(script_for(target.shell), encoding="utf-8")
    click.echo(f"{target.shell} completion installed in {target.script}.")

    if target.rc is not None:
        content = target.rc.read_text(encoding="utf-8") if target.rc.is_file() else ""
        updated, changed = add_block(content, target.rc_lines)
        if changed:
            target.rc.parent.mkdir(parents=True, exist_ok=True)
            target.rc.write_text(updated, encoding="utf-8")
            click.echo(f"Wired up in {target.rc}.")
        else:
            click.echo(f"Already wired up in {target.rc}.")

    click.echo("Completion will take effect once you restart the terminal.")
    return 0


def uninstall(shell: str | None) -> int:
    """Remove the completion script for `shell` and the lines that read it."""
    target = layout(resolve_shell(shell))
    removed = False

    if target.script.is_file():
        target.script.unlink()
        click.echo(f"Removed {target.script}.")
        removed = True

    if target.rc is not None and target.rc.is_file():
        content = target.rc.read_text(encoding="utf-8")
        updated, changed = remove_block(content)
        if changed:
            target.rc.write_text(updated, encoding="utf-8")
            click.echo(f"Removed the {PROG_NAME} lines from {target.rc}.")
            removed = True

    if not removed:
        click.echo(f"No {target.shell} completion was installed.")
    return 0
