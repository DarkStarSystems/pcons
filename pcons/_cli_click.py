# SPDX-License-Identifier: MIT
"""click building blocks for the pcons command line.

The pieces here replace three argparse workarounds:

- a subcommand no longer overwrites what was spelled before it, so
  ``pcons -B out generate`` generates into ``out``. See `MergingCommand`.
- ``pcons hello`` is a target to build, not an unknown command. See `PconsGroup`.
- ``-C DIR`` chdirs from an eager callback instead of a hand-written scan that
  edited ``sys.argv`` in place. See `directory_option`.

The option decorators exist so every command declares the option groups it opts
into, instead of two parsers repeating the same lists and drifting apart.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click
from click.core import ParameterSource

import pcons
from pcons.core.debug import SUBSYSTEM_DESCRIPTIONS

F = TypeVar("F", bound=Callable[..., Any])

# Set on the group's context when an unresolvable command name was routed to the
# catch-all command, so the group callback knows not to run it a second time.
ROUTED_TO_DEFAULT = "pcons.routed_to_default"

# Set on the group's context when argv held a `--`, which the group's parser
# consumes before anything downstream can see it.
SAW_DOUBLE_DASH = "pcons.saw_double_dash"


def _adopt_options_spelled_earlier(command: click.Command, ctx: click.Context) -> None:
    """Take an option's value from the command name it was spelled before.

    argparse applied a subparser's defaults unconditionally on top of what the
    top-level parser had already stored, so ``pcons -B out generate`` fell back
    to ``build``. Here the parent's value is taken unless the user spelled the
    option after the command name, so the later spelling still wins.

    The test is "not spelled on the command line" rather than "still at its
    default": ``-B`` also reads ``PCONS_BUILD_DIR``, and a value click took
    from the environment must not beat a ``-B`` spelled before the command.

    Reading only the immediate parent is enough however deep the nesting goes,
    because a `MergingGroup` adopts into its own ``ctx.params`` before it
    dispatches. By the time ``pcons -B out cache path`` reaches ``path``, the
    ``cache`` context already carries ``out``, so the value arrives one level at
    a time rather than needing a walk to the top.
    """
    for param in command.params:
        name = param.name
        if name is None:
            continue
        if ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE:
            continue
        # A command invoked on its own, as a test may do, has no group above it.
        parent = ctx.parent
        if parent is not None and name in parent.params:
            ctx.params[name] = parent.params[name]


class MergingCommand(click.Command):
    """A command that inherits an option spelled before its name.

    See `_adopt_options_spelled_earlier`.
    """

    def invoke(self, ctx: click.Context) -> Any:
        _adopt_options_spelled_earlier(self, ctx)
        return super().invoke(ctx)


class MergingGroup(click.Group):
    """A group that inherits, and so passes the inheritance on to its commands.

    Without this a subcommand of a subgroup would read this group's untouched
    default instead of what was spelled before the group's own name.

    Not a subclass of `MergingCommand`: click's Group already derives from
    Command, and crossing the two hierarchies buys nothing when the behaviour is
    one shared function.
    """

    def invoke(self, ctx: click.Context) -> Any:
        _adopt_options_spelled_earlier(self, ctx)
        return super().invoke(ctx)

    #: A subgroup's commands inherit too, without each restating it.
    command_class = MergingCommand

    def list_commands(self, ctx: click.Context) -> list[str]:
        """Declaration order, as `PconsGroup` does, not click's alphabetical."""
        return list(self.commands)


class _GroupPathContext(click.Context):
    """Report the group's command path as the command's own.

    The catch-all command is hidden and has no name a user can type, so its
    usage line and its "Try ... for help" hint must read ``pcons``. click
    builds `Context.command_path` as ``f"{parent} {info_name}"`` and only
    lstrips it, so an empty name would leave a trailing space.
    """

    @property
    def command_path(self) -> str:
        if self.parent is not None:
            return self.parent.command_path
        return super().command_path


class DefaultCommand(MergingCommand):
    """The catch-all command, reporting the group's path instead of its own."""

    context_class = _GroupPathContext


class PconsGroup(click.Group):
    """Route an unknown command name to a hidden catch-all command.

    ``pcons hello`` builds a target called hello and ``pcons CC=clang hello``
    sets a variable first. Neither is a command name, so an unresolvable first
    positional falls through to `DEFAULT_COMMAND` instead of failing.
    """

    DEFAULT_COMMAND = "_default"

    #: Every command inherits an option spelled before its name, so no command
    #: has to ask for it. The catch-all overrides this with `DefaultCommand`.
    command_class = MergingCommand
    group_class = MergingGroup

    def list_commands(self, ctx: click.Context) -> list[str]:
        """Declaration order, which groups the commands by what they do."""
        return [name for name in self.commands if name != self.DEFAULT_COMMAND]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """Refuse to resolve the catch-all by name.

        Its name is not part of the interface, so a user typing it means a
        target called `_default`. Without this it resolves like any other
        command and the token disappears from the target list.
        """
        if cmd_name == self.DEFAULT_COMMAND:
            return None
        return super().get_command(ctx, cmd_name)

    def _catch_all(self) -> click.Command | None:
        """The catch-all command, which only `resolve_command` may reach."""
        return self.commands.get(self.DEFAULT_COMMAND)

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # Recorded before click's parser eats the `--`: after one, a token
        # starting with a dash is a target to build, not an option.
        ctx.meta[SAW_DOUBLE_DASH] = "--" in args
        return super().parse_args(ctx, args)

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and args[0].startswith("-") and ctx.meta.get(SAW_DOUBLE_DASH):
            # `pcons -- -foo` builds a target called -foo. No command name
            # starts with a dash, so this cannot capture one: `pcons -- build`
            # still runs the build command.
            default = self._catch_all()
            if default is not None:
                ctx.meta[ROUTED_TO_DEFAULT] = True
                # The `--` goes back in. The group's parser consumed it, and
                # the catch-all's own parser needs it to stop reading the rest
                # as options, which is what keeps a plain typo an error.
                return None, default, ["--", *args]
        try:
            return super().resolve_command(ctx, args)
        except click.NoSuchOption:
            # click re-parses the group's options when the token looks like one,
            # and NoSuchOption derives from UsageError. An unknown option stays
            # an error: only an unresolvable command name falls through.
            raise
        except click.UsageError:
            if not args or args[0].startswith("-"):
                raise
            default = self._catch_all()
            if default is None:
                raise
            # A None name keeps ctx.invoked_subcommand None, so the group
            # callback cannot tell this apart from a command line naming no
            # command at all, hence the marker. `DefaultCommand` is what makes
            # usage and errors read "pcons" rather than "pcons " or
            # "pcons _default".
            ctx.meta[ROUTED_TO_DEFAULT] = True
            return None, default, args


def _chdir(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Change directory before any other option is processed.

    Prints and exits 1 rather than raising a UsageError, which would exit 2:
    the directory being missing is not a usage mistake, and the code the CLI
    has always returned here is 1.
    """
    if value:
        try:
            os.chdir(value)
        except OSError as e:
            click.echo(f"error: -C {value}: {e}", err=True)
            ctx.exit(1)
    return value


def _generator_names() -> list[str]:
    """The registered generator names, in registration order.

    Read at import time, which is correct only while `pcons/__init__.py` does
    not import `pcons.cli`: the registry has to be populated before the option
    is declared.
    """
    return list(pcons.GENERATORS)


def _generator_help() -> str:
    # Several names can point at one generator (`make` and `makefile` do), and
    # listing both reads as two generators. Name each one once.
    primary: dict[Any, str] = {}
    for name, generator in pcons.GENERATORS.items():
        primary.setdefault(generator, name)
    names = ", ".join(primary.values())
    return f"Generator to use ({names}). Repeatable. Default: ninja"


def _debug_help() -> str:
    subsystems = ",".join(SUBSYSTEM_DESCRIPTIONS) + ",all,help"
    return f"Enable debug tracing for subsystems (comma-separated): {subsystems}"


def directory_option(f: F) -> F:
    """-C DIR, applied before every other option on every command."""
    return click.option(
        "-C",
        "--directory",
        metavar="DIR",
        callback=_chdir,
        is_eager=True,
        expose_value=False,
        help="Change to DIR before doing anything else",
    )(f)


def common_options(f: F) -> F:
    """The options every command accepts, on both sides of the command name."""
    f = click.option(
        "--modules-path",
        metavar="PATHS",
        help="Additional paths to search for pcons modules (colon/semicolon-separated)",
    )(f)
    f = click.option(
        "-B",
        "--build-dir",
        # A Path, so no command has to convert it first. The metavar is spelled
        # out because click.Path would otherwise print its own.
        type=click.Path(path_type=Path),
        metavar="DIR",
        envvar="PCONS_BUILD_DIR",
        default="build",
        help="Build directory (default: $PCONS_BUILD_DIR, or 'build')",
    )(f)
    f = click.option("--debug", metavar="SUBSYSTEMS", help=_debug_help())(f)
    f = click.option(
        "-v", "--verbose", is_flag=True, default=False, help="Verbose output"
    )(f)
    return f


def generate_options(f: F) -> F:
    """Options for commands that generate build files."""
    f = click.option(
        "-b", "--build-script", metavar="FILE", help="Path to pcons-build.py script"
    )(f)
    f = click.option(
        "--fresh",
        is_flag=True,
        default=False,
        help="Discard the persisted cache and start clean (like cmake --fresh)",
    )(f)
    f = click.option(
        "--reconfigure",
        is_flag=True,
        default=False,
        help="Force re-run configuration checks",
    )(f)
    f = click.option(
        "-G",
        "--generator",
        metavar="NAME",
        multiple=True,
        type=click.Choice(_generator_names()),
        help=_generator_help(),
    )(f)
    f = click.option(
        "--variant", metavar="NAME", help="Build variant (debug, release, etc.)"
    )(f)
    return f


def build_options(f: F) -> F:
    """Options that affect how the build is run, not how it is generated."""
    # n2 is a ninja-compatible runner (Rust rewrite of Ninja) with more advanced
    # rebuild tracking.
    return click.option(
        "--ninja",
        metavar="PROG",
        help=(
            "Ninja-compatible runner to invoke (e.g., 'n2'). "
            "Defaults to the NINJA env var, then 'ninja'."
        ),
    )(f)


def watch_option(f: F) -> F:
    return click.option(
        "--watch",
        is_flag=True,
        default=False,
        help=(
            "Build, then rebuild whenever a source or the build script "
            "changes (Ctrl-C to stop)"
        ),
    )(f)


def jobs_option(f: F) -> F:
    return click.option(
        "-j", "--jobs", metavar="N", type=int, help="Number of parallel jobs for build"
    )(f)
