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

import argparse
import os
from collections.abc import Callable
from typing import Any, TypeVar

import click
from click.core import ParameterSource

from pcons.core.debug import SUBSYSTEM_DESCRIPTIONS

F = TypeVar("F", bound=Callable[..., Any])

GENERATORS = ["ninja", "make", "makefile", "metadata", "xcode"]

# Set on the group's context when an unresolvable command name was routed to the
# catch-all command, so the group callback knows not to run it a second time.
ROUTED_TO_DEFAULT = "pcons.routed_to_default"


class MergingCommand(click.Command):
    """Let a subcommand inherit an option spelled before the command name.

    argparse applied a subparser's defaults unconditionally on top of what the
    top-level parser had already stored, so ``pcons -B out generate`` fell back
    to ``build``. Here the parent value is taken unless the user spelled the
    option after the subcommand, so the later spelling still wins.

    The test is "not spelled on the command line" rather than "still at its
    default": ``-B`` also reads ``PCONS_BUILD_DIR``, and a value click took
    from the environment must not beat a ``-B`` spelled before the command.
    """

    def invoke(self, ctx: click.Context) -> Any:
        parent = ctx.parent
        # A command invoked on its own, as a test may do, has no group above it.
        if parent is not None:
            for param in self.params:
                name = param.name
                if name is None or name not in parent.params:
                    continue
                if ctx.get_parameter_source(name) is not ParameterSource.COMMANDLINE:
                    ctx.params[name] = parent.params[name]
        return super().invoke(ctx)


class PconsGroup(click.Group):
    """Route an unknown command name to a hidden catch-all command.

    ``pcons hello`` builds a target called hello and ``pcons CC=clang hello``
    sets a variable first. Neither is a command name, so an unresolvable first
    positional falls through to `DEFAULT_COMMAND` instead of failing.
    """

    DEFAULT_COMMAND = "_default"

    def list_commands(self, ctx: click.Context) -> list[str]:
        """Declaration order, which groups the commands by what they do."""
        return [name for name in self.commands if name != self.DEFAULT_COMMAND]

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
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
            default = self.get_command(ctx, self.DEFAULT_COMMAND)
            if default is None:
                raise
            # A None name leaves the sub-context's command path as the group's
            # own, so usage and errors read "pcons", not "pcons _default". It
            # also leaves ctx.invoked_subcommand None, which is what the group
            # callback sees when there is no command at all, hence the marker.
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


def _namespace(
    ctx: click.Context, command: str | None, **kw: Any
) -> argparse.Namespace:
    """Hand the existing cmd_* functions the Namespace they already take.

    This is a boundary, not a pattern. The command implementations keep their
    argparse signature, so the conversion touches the parser layer only.

    Values spelled before the command name live on the parent context and are
    picked up here, so a command sees the union of both sides. `MergingCommand`
    has already decided which of the two wins for the options both carry.
    """
    params = dict(ctx.parent.params) if ctx.parent is not None else {}
    params.update(kw)
    params["command"] = command
    params.setdefault("extra", [])
    params.setdefault("targets", [])
    ns = argparse.Namespace(**params)
    # click hands back tuples where argparse handed back lists, and downstream
    # code tests `generator` for falsiness rather than for emptiness.
    ns.extra = list(ns.extra)
    generator = getattr(ns, "generator", None)
    if generator is not None:
        ns.generator = list(generator) or None
    return ns


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
    f = click.option("-b", "--build-script", help="Path to pcons-build.py script")(f)
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
        type=click.Choice(GENERATORS),
        help="Generator to use (ninja, make, metadata, xcode). Repeatable. Default: ninja",
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
        "-j", "--jobs", type=int, help="Number of parallel jobs for build"
    )(f)
