# SPDX-License-Identifier: MIT
"""User-declared CLI commands, reachable as ``pcons run <name>``.

A build script, or an add-on module's ``register()``, declares a command with
`cli_command` and it becomes ``pcons run <name>``:

    import click
    import pcons

    @pcons.cli_command()
    @click.option("--baud", default=115200)
    def flash(baud: int) -> None:
        '''Flash the board.'''

The decorators return real click objects, so users write click directly.

One registry holds every declaration, keyed by name. Each entry records the
origin it came from, ``"script"`` or ``"module:<name>"``, and the module whose
body ran the decorator. Between them those say what a re-run of the build
script will re-declare, and so what is safe to drop on the way in: the script's
own body, and nothing else. An add-on module is loaded once per process; so is
a helper module the script imports, whose decorator therefore fires only on the
first run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import click

from pcons.core.errors import PconsError
from pcons.core.invocation import RUN_NAME

if TYPE_CHECKING:
    from pcons._cli_click import UserCommand, UserGroup

SCRIPT_ORIGIN = "script"
_MODULE_PREFIX = "pcons.modules."
# The module name a build script's own body carries: `run_script` and
# `add_subdirectory` both run a script under `RUN_NAME`. Script bodies are
# re-executed on every run, which is what makes them the ones `script_scope`
# may drop.
SCRIPT_BODY = RUN_NAME
SCRIPT_BODIES = frozenset({SCRIPT_BODY})


@dataclass(frozen=True)
class Declared:
    """A command, where it came from, and which module ran the decorator."""

    command: click.Command
    origin: str
    declared_in: str


# name -> every declaration of it. More than one is a conflict, reported when
# the name is used rather than when it is declared: a module the user does not
# control must not be able to fail their build script.
_declared: dict[str, list[Declared]] = {}
_script_depth = 0
# Names recorded since the current script scope opened. A second declaration
# inside one run is the script's own bug; one across runs is a re-run.
_this_scope: set[tuple[str, str]] = set()


@contextmanager
def script_scope() -> Iterator[None]:
    """Attribute declarations to the build script, and drop the previous run's.

    Only what a script **body** declared is dropped, because that is all a
    re-exec re-declares: the top-level script and any sub-script
    `add_subdirectory` runs, both of which are read again every time. A command
    declared by a helper module the script imports is kept: the module is
    already in `sys.modules`, its body does not run again, and its decorator
    never fires again -- so clearing it would lose it for the rest of the
    process. `pcons build --watch` re-runs the script in one process, and the
    listing is written from this registry, so losing it would also delete the
    name from the build directory's listing.

    Module declarations survive for the same reason: they are registered once at
    load, and the script is re-exec'd on every invocation.
    """
    global _script_depth
    if _script_depth == 0:
        # Only the outermost scope means "a fresh script". A nested one must not
        # discard what the enclosing script already declared.
        for name in list(_declared):
            kept = [
                d
                for d in _declared[name]
                if not (d.origin == SCRIPT_ORIGIN and d.declared_in in SCRIPT_BODIES)
            ]
            if kept:
                _declared[name] = kept
            else:
                del _declared[name]
        _this_scope.clear()
    _script_depth += 1
    try:
        yield
    finally:
        _script_depth -= 1


def _declaring_module(func: Callable[..., Any]) -> str:
    """The module whose body ran the decorator."""
    return getattr(func, "__module__", None) or "?"


def _origin_of(func: Callable[..., Any]) -> str:
    """Where a declaration is attributed to.

    Inside `script_scope` it is the build script. Otherwise it is the declaring
    module, named after the function's own ``__module__``: `pcons.modules`
    imports an add-on as ``pcons.modules.<name>``, which is the name the user
    knows it by.
    """
    module = _declaring_module(func)
    if module.startswith(_MODULE_PREFIX):
        # Tested before the scope: a module loaded *during* a script run is
        # still the module's, and attributing it to the script would put it in
        # the persisted listing and drop it on the next run's way in.
        return f"module:{module[len(_MODULE_PREFIX) :]}"
    if _script_depth > 0:
        return SCRIPT_ORIGIN
    return f"module:{module}"


def _record(command: click.Command, func: Callable[..., Any]) -> None:
    name = command.name
    if not name:
        raise PconsError("A declared CLI command must have a name")
    origin = _origin_of(func)
    declared_in = _declaring_module(func)
    entries = _declared.setdefault(name, [])
    clashing = next(
        (
            entry
            for entry in entries
            if entry.origin == origin and entry.declared_in != declared_in
        ),
        None,
    )
    if clashing is not None:
        # Two different modules of one build reaching for the same name. Checked
        # before the two below because it can say *which* two, and because
        # `_this_scope` cannot catch it past the first run in a process: a
        # helper's decorator fires once and is then cached in sys.modules while
        # the script body re-declares every time. Left to the replacement below,
        # the clash would be reported on the first run and silently resolved on
        # every later one.
        raise PconsError(
            f"CLI command '{name}' is declared twice by {origin}, "
            f"in {clashing.declared_in} and in {declared_in}. "
            "Each name may be declared once per origin."
        )
    if (name, origin) in _this_scope:
        raise PconsError(
            f"CLI command '{name}' is declared twice by {origin}. "
            "Each name may be declared once per origin."
        )
    if _script_depth == 0 and any(entry.origin == origin for entry in entries):
        # Outside a script scope there is no run to be "the same" as, so a
        # duplicate is a module declaring one name twice.
        raise PconsError(
            f"CLI command '{name}' is declared twice by {origin}. "
            "Each name may be declared once per origin."
        )
    # A same-origin entry from the same module is stale, not a duplicate: a
    # helper re-imported with importlib.reload re-runs its decorator, and
    # refusing it would fail the build script over a re-run.
    entries[:] = [entry for entry in entries if entry.origin != origin]
    _this_scope.add((name, origin))
    entries.append(Declared(command=command, origin=origin, declared_in=declared_in))


def cli_command(
    name: str | None = None, **attrs: Any
) -> Callable[[Callable[..., Any]], UserCommand]:
    """Declare a command, reachable as ``pcons run <name>``.

    Returns a `UserCommand`, a real `click.Command`, so every click decorator
    applies to the function below this one and ``depends`` is available on the
    result. The name defaults to click's derivation from the function name,
    which turns ``build_docs`` into ``build-docs``.

    Plain click below ``depends``, never pcons' own `MergingCommand`, which
    adopts same-named options from the group above and reads ``--debug``/``-v``
    as pcons means them, so a command declaring a ``--debug`` of its own would
    have its value validated as pcons subsystems, and its ``--build-dir``
    silently replaced by the run group's. A user command owns its options.
    `RunGroup.invoke` has already merged and configured pcons' own by the time
    one runs.

    Passing ``cls`` replaces that class, and the annotation here no longer
    describes what comes back.
    """

    def decorator(func: Callable[..., Any]) -> UserCommand:
        # Local: `_cli_click` imports pcons at module level, and pcons
        # re-exports from here, so a module-level import would close a cycle.
        from pcons._cli_click import UserCommand

        attrs.setdefault("cls", UserCommand)
        command = click.command(name, **attrs)(func)
        _record(command, func)
        return cast("UserCommand", command)

    return decorator


def cli_group(
    name: str | None = None, **attrs: Any
) -> Callable[[Callable[..., Any]], UserGroup]:
    """Declare a group of commands, reachable as ``pcons run <name> <verb>``.

    Add verbs to it with click's own ``@mygroup.command()``. They belong to the
    group and never enter this registry, so they cannot collide with a
    top-level name.

    A verb declares no dependencies of its own; the group's apply to all of
    them. Passing ``cls`` replaces the class, and the annotation here no longer
    describes what comes back.
    """

    def decorator(func: Callable[..., Any]) -> UserGroup:
        from pcons._cli_click import UserGroup

        attrs.setdefault("cls", UserGroup)
        group = click.group(name, **attrs)(func)
        _record(group, func)
        return cast("UserGroup", group)

    return decorator


def declared() -> dict[str, list[Declared]]:
    """Every declaration, keyed by name, in declaration order."""
    return {name: list(entries) for name, entries in _declared.items()}


def lookup(name: str) -> click.Command | None:
    """The command called *name*, or None if nothing declares it.

    Raises:
        PconsError: More than one origin declares the name. Neither runs: the
            user has to resolve it, and picking one silently would make which
            command runs depend on load order.
    """
    entries = _declared.get(name)
    if not entries:
        return None
    if len(entries) > 1:
        origins = ", ".join(entry.origin for entry in entries)
        raise PconsError(
            f"CLI command '{name}' is declared by more than one origin "
            f"({origins}). Rename one of them."
        )
    return entries[0].command


def clear_module_declarations() -> None:
    """Drop what add-on modules declared, keeping the script's.

    Called when the modules themselves are unloaded. Without it a module
    re-imported in one process would declare its commands a second time, which
    is an error against the same origin -- and one `load_modules` swallows,
    leaving the rest of that `register()` undone and the *stale* command object
    still in the registry.
    """
    for name in list(_declared):
        kept = [d for d in _declared[name] if d.origin == SCRIPT_ORIGIN]
        if kept:
            _declared[name] = kept
        else:
            del _declared[name]
    _this_scope.difference_update(
        {key for key in _this_scope if key[1] != SCRIPT_ORIGIN}
    )


def clear() -> None:
    """Drop every declaration. For tests."""
    _declared.clear()
    _this_scope.clear()
