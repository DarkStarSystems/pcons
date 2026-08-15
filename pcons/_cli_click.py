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

import io
import os
from collections.abc import Callable
from functools import update_wrapper
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar, cast

import click
from click.core import ParameterSource
from click.shell_completion import CompletionItem

import pcons
from pcons.core.debug import (
    SUBSYSTEM_DESCRIPTIONS,
    SubsystemListRequested,
    UnknownSubsystemsError,
    print_subsystems,
)

F = TypeVar("F", bound=Callable[..., Any])
P = ParamSpec("P")
R = TypeVar("R")


class PconsContext(click.Context):
    """Every context in the pcons command tree, carrying what parsing learned.

    Both facts are set and read on the group's own context, so they are
    attributes rather than entries in the ``ctx.meta`` dictionary every nested
    context shares. Every command class here declares this as its
    ``context_class``, so a callback taking `PconsContext` gets one.
    """

    #: argv held a `--` before any command name, and the group's parser
    #: consumes it before anything downstream can see it. Everything after
    #: it names a target to build, never a command or an option.
    targets_follow: bool = False

    #: an unresolvable command name was routed to the catch-all command, so the
    #: group callback knows not to run it a second time.
    routed_to_default: bool = False


def pass_pcons_context(f: Callable[Concatenate[PconsContext, P], R]) -> Callable[P, R]:
    """`click.pass_context`, typed for the context every pcons command gets.

    click types its own decorator against `click.Context`, so a callback
    annotated with the subclass every command declares as its ``context_class``
    is rejected there. Same wrapper, one type narrower.
    """

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        return f(cast(PconsContext, click.get_current_context()), *args, **kwargs)

    return update_wrapper(wrapper, f)


def run_cli(
    command: click.Command,
    *,
    prog_name: str,
    argv: list[str] | None = None,
    **kwargs: Any,
) -> int:
    """Run *command* and return the exit code, rather than exiting.

    The four entry points all need this: `pcons` and `pcons-fetch` are console
    scripts, `pcons test` is called in process by `pcons.cli`, and every one of
    them has to turn what click raises into a number.

    ``standalone_mode=False`` makes click return the code for ``ctx.exit()``
    and for ``--help`` itself, and re-raise only the two caught here.

    ``windows_expand_args`` is off: with ``argv=None`` on Windows click applies
    expanduser, expandvars and glob to every token. None of these commands take
    a pattern -- they take build variables, target names, label filters and
    name regexes -- and the expansion runs after the shell, so quoting cannot
    escape it.
    """
    try:
        result = command.main(
            args=argv,
            prog_name=prog_name,
            standalone_mode=False,
            windows_expand_args=False,
            **kwargs,
        )
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except click.exceptions.Abort:
        return 130
    return result if isinstance(result, int) else 0


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


def inherited_param(ctx: click.Context, name: str) -> Any:
    """The value `name` settles on once `_adopt_options_spelled_earlier` has run.

    Same rule, read-only, for the callers that never get to see the merge.
    Completion is the one: click builds its contexts through `parse_args` and
    then answers, so `invoke` never runs and ``pcons -B out build <TAB>`` still
    has this command's own default sitting in ``ctx.params``.

    Change one of these two and change the other.
    """
    if ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE:
        return ctx.params.get(name)
    parent = ctx.parent
    if parent is not None and name in parent.params:
        return inherited_param(parent, name)
    return ctx.params.get(name)


def configure_logging(ctx: click.Context) -> None:
    """Set logging up from the options every command shares, once they settle.

    Read out of what the merge left in ``ctx.params``, so ``pcons -v generate``
    and ``pcons generate -v`` configure the same way. A subgroup's verb runs
    this after its group already did, and the deeper spelling wins because it
    runs last.

    A command declaring neither option is left alone. It has nothing to say
    about logging, and configuring anyway would mean the level a command
    without ``-v`` settles on beats one spelled before it, and that ``--debug``
    is validated for a command that never reads it: `pcons test` hands its argv
    to another program with its own logging.

    ``--debug`` names subsystems on `pcons` and is a plain flag on
    `pcons-fetch`, which has no subsystems of its own to name. A flag means
    all of them. What the spec asks for is rendered here rather than in
    `init_debug`, so a bad one reaches the shell as a usage error rather than
    as a SystemExit raised past the entry point's own error handling.
    """
    params = ctx.params
    if "verbose" not in params and "debug" not in params:
        return

    debug = params.get("debug")
    if isinstance(debug, bool):
        debug = "all" if debug else None

    # Imported here because `pcons.cli` imports this module, not the reverse.
    from pcons.cli import setup_logging

    try:
        setup_logging(bool(params.get("verbose", False)), cast("str | None", debug))
    except SubsystemListRequested:
        print_subsystems()
        raise click.exceptions.Exit(0) from None
    except UnknownSubsystemsError as e:
        message = io.StringIO()
        print(e, file=message)
        print_subsystems(file=message)
        raise click.UsageError(message.getvalue().rstrip("\n")) from None


def load_declared_modules(command: click.Command, ctx: click.Context) -> None:
    """Load add-on modules, for a command that says it wants them.

    Only a command that runs the build script does. Loading executes each
    module's ``register()``, and `pcons clean` has no reason to run a user's
    code.
    """
    if not getattr(command, "loads_modules", False):
        return

    from pcons.cli import _load_user_modules

    _load_user_modules(cast("str | None", ctx.params.get("modules_path")))


class MergingCommand(click.Command):
    """A command that inherits an option spelled before its name.

    See `_adopt_options_spelled_earlier`.
    """

    context_class = PconsContext

    def __init__(self, *args: Any, loads_modules: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: run the user's add-on modules before the callback. For a command
        #: that runs the build script, which is what a module extends.
        self.loads_modules = loads_modules

    def invoke(self, ctx: click.Context) -> Any:
        _adopt_options_spelled_earlier(self, ctx)
        configure_logging(ctx)
        load_declared_modules(self, ctx)
        return super().invoke(ctx)


class MergingGroup(click.Group):
    """A group that inherits, and so passes the inheritance on to its commands.

    Without this a subcommand of a subgroup would read this group's untouched
    default instead of what was spelled before the group's own name.

    Not a subclass of `MergingCommand`: click's Group already derives from
    Command, and crossing the two hierarchies buys nothing when the behaviour is
    one shared function.
    """

    context_class = PconsContext

    #: A subgroup's commands inherit too, without each restating it, and so
    #: does a group nested under it: `type` is click's spelling for "this
    #: class", and without it click would fall back to a plain `click.Group`
    #: that inherits nothing.
    command_class = MergingCommand
    group_class = type

    def __init__(self, *args: Any, loads_modules: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.loads_modules = loads_modules

    def invoke(self, ctx: click.Context) -> Any:
        _adopt_options_spelled_earlier(self, ctx)
        configure_logging(ctx)
        load_declared_modules(self, ctx)
        return super().invoke(ctx)

    def list_commands(self, ctx: click.Context) -> list[str]:
        """Declaration order, as `PconsGroup` does, not click's alphabetical."""
        return list(self.commands)


class _GroupPathContext(PconsContext):
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


def _consumes_next_token(token: str, takes_value: set[str]) -> bool:
    """Whether *token* is an option whose value is the token after it.

    A long option carries its value inline when it is spelled with an ``=``.
    A short one may be bundled with flags before it, ``-vC build``, or carry
    its value attached, ``-Cbuild``, so only a value-taking letter in last
    place reaches for the next token.
    """
    if token.startswith("--"):
        return "=" not in token and token in takes_value
    for position, letter in enumerate(token[1:], start=1):
        if f"-{letter}" in takes_value:
            return position == len(token) - 1
    return False


class PconsGroup(click.Group):
    """Route an unknown command name to a hidden catch-all command.

    ``pcons hello`` builds a target called hello and ``pcons CC=clang hello``
    sets a variable first. Neither is a command name, so an unresolvable first
    positional falls through to `DEFAULT_COMMAND` instead of failing.
    """

    DEFAULT_COMMAND = "_default"

    context_class = PconsContext

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

    def _resolve_command_anywhere(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """Find a command name that is not the first argument.

        click resolves a subcommand from the first argument and nothing else,
        and a group stops parsing at the first thing that is not an option. So
        ``pcons CC=clang generate`` took ``CC=clang`` for a command name, failed
        to resolve it, and handed the whole line to the catch-all -- where
        ``generate`` stopped being a command and became a target to build. Build
        variables legitimately precede a command name, and once one of them has
        stopped the parser the group's own options are sitting unparsed in front
        of it too.

        Everything except the command name comes back in the order it was
        written. The command re-parses it: the options it shares with the group
        are declared on it as well, and the variables land in its trailing
        ``nargs=-1``.

        A token that is the value of an option is not a candidate, or
        ``pcons -C build generate`` would find the directory rather than the
        command. A bare ``--`` ends the scan: everything after it names a
        target, so ``pcons FOO=bar -- clean`` builds ``clean`` rather than
        running it. Returns ``(None, None, args)`` when nothing names a command,
        which leaves the caller's catch-all fallback in charge -- so
        ``pcons CC=clang hello`` still builds a target called ``hello``.
        """
        takes_value = self._takes_value_set()
        skip = False
        for index, token in enumerate(args):
            if skip:
                skip = False
                continue
            if token == "--":
                break
            if token.startswith("-"):
                skip = _consumes_next_token(token, takes_value)
                continue
            command = self.get_command(ctx, token)
            if command is not None:
                return token, command, [*args[:index], *args[index + 1 :]]
        return None, None, args

    def shell_complete(
        self, ctx: click.Context, incomplete: str
    ) -> list[CompletionItem]:
        """Command names, then the targets the catch-all would have accepted.

        `pcons hello` builds a target, so the group offers target names itself.
        The command declaring the argument that carries them is the hidden
        catch-all, which `get_command` refuses to resolve and `list_commands`
        leaves out, so click's own walk of the tree never reaches it.
        """
        items = super().shell_complete(ctx, incomplete)
        items.extend(complete_target(ctx, None, incomplete))
        return items

    def format_options(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        """Options, then the commands click adds, then the targets.

        `pcons hello` builds a target, so the names belong next to the command
        names rather than only under `pcons build --help`.
        """
        super().format_options(ctx, formatter)
        format_recorded_targets(ctx, formatter)

    # click types these hooks against the base context, and narrowing a
    # parameter in an override is unsound in general, so the class this group
    # builds its own context from is spelled out at each one.
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # Recorded before click's parser eats the `--`: does one come before
        # the first positional token? Option values do not count as
        # positionals, or `pcons -B out -- clean` would read `out` as one.
        takes_value = self._takes_value_set()
        targets_follow = False
        skip = False
        for token in args:
            if skip:
                skip = False
                continue
            if token == "--":
                targets_follow = True
                break
            if token.startswith("-"):
                skip = _consumes_next_token(token, takes_value)
                continue
            break  # a positional: any later `--` belongs to that command
        cast(PconsContext, ctx).targets_follow = targets_follow
        return super().parse_args(ctx, args)

    def _takes_value_set(self) -> set[str]:
        """Spellings of the group's value-taking options."""
        return {
            opt
            for param in self.params
            if isinstance(param, click.Option) and not param.is_flag
            for opt in (*param.opts, *param.secondary_opts)
        }

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        pcons_ctx = cast(PconsContext, ctx)
        if args and pcons_ctx.targets_follow:
            # A `--` before any command name means targets follow: everything
            # after it goes to the catch-all, so `pcons -- clean` builds a
            # target called clean rather than running the clean command, the
            # same reading `pcons FOO=bar -- clean` gets from the scan below.
            # To run a command, name it before any `--`.
            default = self._catch_all()
            if default is not None:
                pcons_ctx.routed_to_default = True
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
            name, command, rest = self._resolve_command_anywhere(ctx, args)
            if command is not None:
                return name, command, rest
            default = self._catch_all()
            if default is None:
                raise
            # A None name keeps ctx.invoked_subcommand None, so the group
            # callback cannot tell this apart from a command line naming no
            # command at all, hence the flag. `DefaultCommand` is what makes
            # usage and errors read "pcons" rather than "pcons " or
            # "pcons _default".
            pcons_ctx.routed_to_default = True
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


#: What `--debug` accepts besides a subsystem name. `all` is in
#: `debug.SUBSYSTEMS` and `help` is handled by `SubsystemListRequested`, so
#: neither is in `SUBSYSTEM_DESCRIPTIONS` and both have to be spelled here, once,
#: for the help text and the completion to stay in step.
DEBUG_EXTRAS: dict[str, str] = {
    "all": "Every subsystem",
    "help": "List the subsystems and exit",
}


def _debug_help() -> str:
    subsystems = ",".join([*SUBSYSTEM_DESCRIPTIONS, *DEBUG_EXTRAS])
    return f"Enable debug tracing for subsystems (comma-separated): {subsystems}"


def _complete_debug(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete one subsystem of a comma-separated `--debug` spec.

    The spec is a list in a single word, so what is completed is the segment
    after the last comma and the segments already typed come back as a prefix.
    That works here and not for `--modules-path` because these are `plain`
    candidates, whose value the shell uses, rather than path directives, whose
    value it ignores.
    """
    head, sep, tail = incomplete.rpartition(",")
    return [
        CompletionItem(head + sep + name, help=description)
        for name, description in {**SUBSYSTEM_DESCRIPTIONS, **DEBUG_EXTRAS}.items()
        if name.startswith(tail)
    ]


def _complete_runner(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """The ninja-compatible runners pcons knows by name.

    Only the two names, with no file fallback: mixing a `file` directive in
    would drop them, since the shell clears the candidates it has collected
    before completing the word itself.
    """
    return [
        CompletionItem(name) for name in ("ninja", "n2") if name.startswith(incomplete)
    ]


class UncheckedPath(click.Path):
    """A path click completes but does not check.

    `click.Path` does two jobs: it tells the shell whether to complete files or
    directories, and it rejects a path of the wrong kind. An option that owns
    its own error path wants the first without the second. `-C` on a file has
    to stay `_chdir`'s exit 1 rather than become a UsageError's 2.

    `file_okay=False` completes directories, `dir_okay=False` completes files,
    the same way `click.Path` picks its directive. The item's value is ignored:
    every shell click writes a script for turns the result into its own path
    completion over the whole word (bash runs `compopt -o dirnames`).
    """

    def convert(
        self,
        value: str | os.PathLike[str],
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str | bytes | os.PathLike[str]:
        return self.coerce_path_result(value)


def _complete_path_list(
    ctx: click.Context, param: click.Parameter | None, incomplete: str
) -> list[CompletionItem]:
    """Hand the shell its own directory completion for a separated list.

    A completer, not a path type, because the value is several paths joined by
    `os.pathsep` rather than one path. The shell completes the whole word, so
    `--modules-path a:b` completes only its first segment.
    """
    return [CompletionItem(incomplete, type="dir")]


def _declared_build_dir(ctx: click.Context) -> str | Path | None:
    """What ``-B`` would settle on with nothing parsed yet: env var, then default.

    `--help` is eager, so it runs from inside `parse_args` and `ctx.params` is
    still empty on the level it fires from. Reading the values off the option's
    own declaration keeps its spelling in one place.
    """
    node: click.Context | None = ctx
    while node is not None:
        for param in node.command.params:
            if param.name != "build_dir":
                continue
            envvar = param.envvar
            if isinstance(envvar, str):
                from_env = os.environ.get(envvar)
                if from_env:
                    return from_env
            return param.get_default(node)
        node = node.parent
    return None


def _cached_names(ctx: click.Context, key: str) -> list[str]:
    """The list the last generate left under `key`, for this build directory.

    Never runs the build script. Completion fires on every keystroke, `--help`
    is meant to be instant, and a build script does configure checks. So these
    names are recorded when a generate runs and only read back here.

    Answers an empty list rather than raising, whatever it finds. For completion
    stdout is the candidate stream, so anything escaping from here is parsed by
    the shell as a completion.
    """
    from pcons.core.cache import BuildCache

    build_dir = inherited_param(ctx, "build_dir")
    if build_dir is None:
        build_dir = _declared_build_dir(ctx)
    if build_dir is None:
        return []
    try:
        names = BuildCache(Path(build_dir)).get(key)
    except OSError:
        return []
    if not isinstance(names, list):
        return []
    return [name for name in names if isinstance(name, str)]


def complete_target(
    ctx: click.Context, param: click.Parameter | None, incomplete: str
) -> list[CompletionItem]:
    """The target names the last generate left in this build directory's cache."""
    return [
        CompletionItem(name)
        for name in _cached_names(ctx, "targets")
        if name.startswith(incomplete)
    ]


def _complete_variant(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """The variant names this build directory has been seen using.

    Variants have no registry to read: what a build script accepts is only
    knowable by running it. So these are the names earlier runs passed to
    `env.set_variant`, accumulated. A script that branches on `get_variant()`
    without calling it names nothing, and completes nothing.
    """
    return [
        CompletionItem(name)
        for name in _cached_names(ctx, "variants")
        if name.startswith(incomplete)
    ]


def format_recorded_targets(ctx: click.Context, formatter: click.HelpFormatter) -> None:
    """List what the last generate left buildable, if anything.

    Silent on a build directory that never generated, so help outside a pcons
    project reads exactly as it did before there was a section to print. Same
    source as the completion of the same names, and the same rule: never run the
    build script to find them.
    """
    names = _cached_names(ctx, "targets")
    if not names:
        return
    with formatter.section("Targets"):
        formatter.write_dl([(name, "") for name in names])


class TargetsCommand(MergingCommand):
    """A command that lists the recorded targets in its help.

    For the commands whose ``EXTRA`` accepts a target name. `pcons info` and
    `pcons generate` take build variables there and would swallow one, so they
    do not get this.
    """

    def format_options(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        super().format_options(ctx, formatter)
        format_recorded_targets(ctx, formatter)


def targets_argument(f: F) -> F:
    """EXTRA: targets to build, and/or KEY=value build variables.

    Variable names are not completed. Only the build script knows them, and
    running it is what completion must not do.
    """
    return click.argument("extra", nargs=-1, shell_complete=complete_target)(f)


def directory_option(f: F) -> F:
    """-C DIR, applied before every other option on every command."""
    return click.option(
        "-C",
        "--directory",
        type=UncheckedPath(file_okay=False),
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
        shell_complete=_complete_path_list,
        help="Additional paths to search for pcons modules (colon/semicolon-separated)",
    )(f)
    f = click.option(
        "-B",
        "--build-dir",
        # A Path, so no command has to convert it first. The metavar is spelled
        # out because click.Path would otherwise print its own.
        type=click.Path(file_okay=False, path_type=Path),
        metavar="DIR",
        # Eager so it is processed before `--help`, which is eager itself and
        # would otherwise format the help out of a context where -B has not
        # been read yet: `pcons -B out --help` would list the default build
        # directory's targets. Among eager parameters click processes the one
        # spelled first, so this only reorders -B against -C, and neither
        # resolves anything at parse time.
        is_eager=True,
        envvar="PCONS_BUILD_DIR",
        default="build",
        help="Build directory (default: $PCONS_BUILD_DIR, or 'build')",
    )(f)
    f = click.option(
        "--debug",
        metavar="SUBSYSTEMS",
        shell_complete=_complete_debug,
        help=_debug_help(),
    )(f)
    f = click.option(
        "--pdb",
        "pdb_",
        is_flag=True,
        default=False,
        expose_value=False,
        callback=_enable_postmortem,
        envvar="PCONS_PDB",
        help="On a build-script crash, enter pdb postmortem at the raise",
    )(f)
    f = click.option(
        "-v", "--verbose", is_flag=True, default=False, help="Verbose output"
    )(f)
    return f


def _enable_postmortem(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """--pdb sets PCONS_PDB for the whole process: the crash handlers live in
    run_script, and the flag may be spelled on either side of the command
    name, so an environment variable is the one channel they all share."""
    if value:
        os.environ["PCONS_PDB"] = "1"


def generate_options(f: F) -> F:
    """Options for commands that generate build files."""
    f = click.option(
        "-b",
        "--build-script",
        type=click.Path(dir_okay=False),
        metavar="FILE",
        help="Path to pcons-build.py script",
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
        "--variant",
        metavar="NAME",
        shell_complete=_complete_variant,
        help="Build variant (debug, release, etc.)",
    )(f)
    return f


def build_options(f: F) -> F:
    """Options that affect how the build is run, not how it is generated."""
    # n2 is a ninja-compatible runner (Rust rewrite of Ninja) with more advanced
    # rebuild tracking.
    return click.option(
        "--ninja",
        metavar="PROG",
        shell_complete=_complete_runner,
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
