# SPDX-License-Identifier: MIT
"""The builder a decoration returns, and each call's build edge."""

from __future__ import annotations

import functools
import inspect
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.tools.pybuilder.arguments import _captured_sys_path, check_arguments
from pcons.tools.pybuilder.errors import PyBuilderError, _and_list, _describe
from pcons.tools.pybuilder.files import _runner_bytes, _runner_rel, emit_args
from pcons.tools.pybuilder.function import ValidatedFunction, emit_module, validate
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pcons.core.environment import Environment
    from pcons.core.node import Node
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation

#: The runner's depfile is the edge's target plus this, as every pcons
#: depfile is named, so ``env.Command(depfile=)`` and the command agree.
DEPFILE_SUFFIX = ".d"


def _as_list(value: object) -> list[Any]:
    """One file or several, as a list; nothing at all is an empty list."""
    from pcons.core.target import Target as TargetClass

    if value is None:
        return []
    if isinstance(value, (str, Path, TargetClass)):
        return [value]
    return list(cast("Sequence[Any]", value))


def check_emitter(
    emitter: Callable[..., Any],
    *,
    function: types.FunctionType,
    kwargs: Mapping[str, Any],
    at: SourceLocation,
) -> None:
    """Refuse at the call an emitter the resolver could not call.

    The emitter runs much later than the line that would be blamed for it, so
    what can be settled now is settled now: it takes ``(targets, sources,
    env)`` plus this call's own keywords. Those are the function's, and the
    emitter sees the same ones, so an emitter that wants one of them for
    naming declares it, or takes ``**kwargs``.

    Raises:
        PyBuilderError: If the emitter's signature cannot take what it will
            be handed.
    """
    try:
        signature = inspect.signature(emitter)
    except (TypeError, ValueError):
        return  # A builtin with no signature to read; let the call speak.
    try:
        signature.bind([], [], None, **kwargs)
    except TypeError as exc:
        given = _and_list(sorted(kwargs)) or "nothing"
        raise PyBuilderError(
            f"PyBuilder {function.__name__}(): the emitter "
            f"{_describe(emitter)} cannot be called with {given}: {exc}. It "
            f"is called as emitter(targets, sources, env, **kwargs) with the "
            f"same keywords the function gets, so it takes the ones it uses "
            f"and **kwargs for the rest.",
            at,
        ) from exc


def run_emitter(
    emitter: Callable[..., Any],
    *,
    targets: list[Any],
    sources: list[Any],
    env: Environment,
    kwargs: Mapping[str, Any],
    function: types.FunctionType,
    at: SourceLocation,
) -> tuple[list[Any], list[Any]]:
    """What the emitter makes of one call's targets and sources.

    Args:
        emitter: What the decoration was given.
        targets: The call's ``target=``, as a list, possibly empty.
        sources: The call's ``source=``, as a list, possibly empty.
        env: The environment the edge builds in.
        kwargs: The call's own keywords, the function's too.
        function: The decorated function, for the message.
        at: Where the call is.

    Returns:
        The targets and the sources the edge really has.

    Raises:
        PyBuilderError: If the emitter returns anything but two lists.
    """
    result = emitter(targets, sources, env, **kwargs)
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        raise PyBuilderError(
            f"PyBuilder {function.__name__}(): the emitter returned "
            f"{result!r}. It returns the pair (targets, sources), each a "
            f"list of files, having added to what the call passed or "
            f"replaced it.",
            at,
        )
    emitted, consumed = result
    return _as_list(emitted), _as_list(consumed)


@dataclass(frozen=True)
class _HowToRun:
    """How the function runs, which every edge of one builder shares.

    These describe the body rather than any one edge, so they sit on the
    decoration. What to build sits on the call, and no option sits on both,
    except ``depends``: the decoration's is a dependency of every edge the
    builder makes, the call's is a dependency of that edge alone. ``emitter``
    and ``discovers`` are here for the same reason: how this builder works
    out what an edge builds, and whether its body reports what it read, are
    one rule each, whatever a call passes.
    """

    python: str | None = None
    restat: bool = False
    write_if_different: bool = False
    cwd: str | Path | None = None
    launcher: Sequence[str] | None = None
    env_vars: Mapping[str, str] | None = None
    worker: Any = None
    depends: Target | str | Path | Sequence[Target | str | Path] | None = None
    emitter: Callable[..., Any] | None = None
    discovers: bool = False

    def command_kwargs(self) -> dict[str, Any]:
        """What ``env.Command`` takes, as given or, for ``discovers``, derived."""
        kwargs: dict[str, Any] = {
            "restat": self.restat,
            "write_if_different": self.write_if_different,
            "cwd": self.cwd,
            "launcher": self.launcher,
            "env_vars": self.env_vars,
            "worker": self.worker,
        }
        if self.discovers:
            kwargs["depfile"] = DEPFILE_SUFFIX
            kwargs["deps_style"] = "gcc"
        return kwargs


class PyBuilder:
    """A build-script function, ready to be turned into build edges.

    ``env.PyBuilder(...)`` returns the decorator that makes one of these, and
    calling it makes an edge, the way calling ``env.Program`` does::

        @env.PyBuilder()
        def report(targets, sources, title):
            from pathlib import Path

            Path(targets[0]).write_text(title)

        counts = report(target="counts.txt", source=[a, b], title="counts")
        more = report(target="more.txt", source=[c], title="more")

    One decoration is one generated module however many edges read it, and
    each call has its own argument pickle. Both are written when the build is
    resolved, never by the call. The module belongs to the
    :class:`ValidatedFunction` inside, which is what claims its path, so
    this object is never itself in the claim registry.
    """

    __slots__ = (
        "_depends",
        "_env",
        "_function",
        "_how",
        "_made",
        "_project",
        "_sys_path",
    )

    def __init__(
        self,
        function: ValidatedFunction,
        env: Environment,
        how: _HowToRun,
        sys_path: list[str] | None,
    ) -> None:
        self._function = function
        self._env = env
        self._how = how
        self._project = env._project
        self._sys_path = sys_path
        self._depends: list[Target | Node | Path | str] = (
            [] if how.depends is None else list(_as_list(how.depends))
        )
        self._made: list[Target] = []

    def __repr__(self) -> str:
        return f"<PyBuilder {self._function.function.__name__}>"

    @property
    def function(self) -> types.FunctionType:
        """The function the build script wrote."""
        return self._function.function

    def depends(self, *items: Target | Node | Path | str) -> PyBuilder:
        """Add *items* as a dependency of every edge this builder makes.

        Applies to every edge already made, and to every edge made
        afterward, so it does not matter whether a call or a ``.depends()``
        comes first in the script.

        Args:
            items: Targets, or files as Node, Path or str, taken the way
                :meth:`Target.depends` takes them.

        Returns:
            self, for chaining.

        Raises:
            RuntimeError: If an edge this builder already made has been
                resolved. Same message :meth:`Target.depends` gives.
        """
        self._depends.extend(items)
        for made in self._made:
            made.depends(*items)
        return self

    def __call__(
        self,
        *,
        target: str | Path | list[str | Path] | None = None,
        source: Target | str | Path | Sequence[Target | str | Path] | None = None,
        name: str | None = None,
        depends: Target | str | Path | Sequence[Target | str | Path] | None = None,
        **kwargs: Any,
    ) -> Target:
        """Make one build edge that runs the function.

        Everything is keyword-only. A positional argument would have to be
        told apart from the function's own, and there is no obvious first one:
        ``target`` and ``source`` are equally plausible.

        The generated module and the argument pickle are node tokens of the
        command, which is what makes the generator spell them as the
        execution directory sees them and makes the edge rebuild when either
        changes. A node token does not join ``$SOURCES``, so the script's own
        sources keep index 0 and are all the runner passes on.

        Sources and targets travel on the command line, so a very long source
        list meets the same limit ``env.Command`` already has, about 32000
        characters on Windows.

        Args:
            target: Output file or files, as ``env.Command`` takes them. May
                be left out when the decoration has an ``emitter=``, which
                then names them.
            source: Input files, or None. They arrive as the function's
                *sources*, in the order written, after the emitter's changes.
            name: Optional name for this target. Give one to refer to it by
                name later: ``get_target()``, ``Default()``, ``pcons build``,
                and ``sub::name@env`` from another build script. It must then
                be unique within its environment and project. Leave it out and
                the target needs no name. Does not affect the argument
                pickle, which is named after the first target's own
                build-relative path.
            depends: Extra rebuild triggers that are not sources, for this
                edge alone. Added to whatever the decoration's own
                ``depends=`` and :meth:`depends` gave the builder.
            **kwargs: The function's own arguments. Each must be picklable,
                and together they must fit its signature.

        Returns:
            The edge's ``Target``.

        Raises:
            PyBuilderError: If nothing names the targets, if the arguments do
                not fit the function or its emitter, if one of them holds a
                piece of the build description or cannot be pickled, or if
                the generated module collides with another builder's.
        """
        at = get_caller_location()
        function = self._function.function
        emitter = self._how.emitter
        if emitter is None and not _as_list(target):
            raise PyBuilderError(
                f"PyBuilder {function.__name__}() needs target=: nothing else "
                f"names the files the edge writes. An emitter= on the "
                f"decoration can name them instead.",
                at,
            )
        if emitter is not None:
            check_emitter(emitter, function=function, kwargs=kwargs, at=at)
        arguments = check_arguments(
            self._function, kwargs=kwargs, sys_path=self._sys_path
        )
        module_rel, module_bytes = emit_module(
            self._function, project=self._project, env=self._env
        )
        project, env = self._project, self._env
        runner_rel = _runner_rel(project)
        root = project.top_path_resolver.project_root

        # The edge's files, all settled while it resolves: an emitter may
        # only name the outputs then, and the pickle is named after them.
        @functools.cache
        def args_rel() -> Path:
            first = made.output_nodes[0].path
            return emit_args(
                project=project, env=env, label=made.name, first_output=first, at=at
            )

        def writes() -> list[tuple[Path, bytes]]:
            files = [(root / runner_rel, _runner_bytes())]
            if module_bytes is not None:
                files.append((root / module_rel, module_bytes))
            info = made.output_nodes[0]._build_info or {}
            payload = arguments.pickle(
                env=env, resolver=project.top_path_resolver, cwd=info.get("cwd")
            )
            files.append((root / args_rel(), payload))
            return files

        edge_target: Any = target
        edge_source: Any = source
        if emitter is not None:

            @functools.cache
            def emitted() -> tuple[list[Any], list[Any]]:
                return run_emitter(
                    emitter,
                    targets=_as_list(target),
                    sources=_as_list(source),
                    env=env,
                    kwargs=kwargs,
                    function=function,
                    at=at,
                )

            edge_target = lambda: emitted()[0]  # noqa: E731
            edge_source = lambda: emitted()[1]  # noqa: E731

        interpreter = (self._how.python or sys.executable).replace("\\", "/")
        made = env.Command(
            target=edge_target,
            source=edge_source,
            name=name,
            command=[
                interpreter,
                project._node(runner_rel),
                project._node(module_rel),
                lambda: project._node(args_rel()),
                *(
                    ["--depfile", f"$TARGET{DEPFILE_SUFFIX}"]
                    if self._how.discovers
                    else []
                ),
                "--n-targets",
                lambda: str(len(made.output_nodes)),
                "$TARGETS",
                "$SOURCES",
            ],
            depends=depends,
            **self._how.command_kwargs(),
        )
        if emitter is not None:
            from pcons.core.target import Target as TargetClass

            # The emitter reads the call's Targets, so they resolve first;
            # the ones it keeps are sources too, and rerun the edge as such.
            called = [s for s in _as_list(source) if isinstance(s, TargetClass)]
            made.depends(*called, on_change=False)
        # The function reads what an argument names, so the edge reruns
        # when it changes, as env.Command(tool=) does for its program.
        made.depends(*arguments.inputs, on_change=True)
        made.depends(*self._depends)
        self._made.append(made)
        made._builder_data["writes"] = writes
        return made


def py_builder(
    env: Environment,
    *,
    python: str | None = None,
    restat: bool = False,
    write_if_different: bool = False,
    cwd: str | Path | None = None,
    launcher: Sequence[str] | None = None,
    env_vars: Mapping[str, str] | None = None,
    worker: Any = None,
    depends: Target | str | Path | Sequence[Target | str | Path] | None = None,
    emitter: Callable[..., Any] | None = None,
    discovers: bool = False,
) -> Callable[[Callable[..., object]], PyBuilder]:
    """The decorator ``Environment.PyBuilder`` returns.

    Nothing is checked here: the function has not arrived yet, and every
    refusal about it has to point at the ``def`` rather than at the line
    above it.

    Args:
        env: The environment the edges build in.
        python: Interpreter to run, defaulting to the one running pcons.
        restat: See ``env.Command``.
        write_if_different: See ``env.Command``.
        cwd: See ``env.Command``.
        launcher: See ``env.Command``.
        env_vars: See ``env.Command``.
        worker: See ``env.Command``.
        depends: Dependency of every edge the builder makes, on top of
            whatever a call's own ``depends=`` adds. See
            :meth:`PyBuilder.depends`.
        emitter: Names each edge's targets and sources at resolve; see
            ``Environment.PyBuilder``.
        discovers: The function reports the files it read; see
            ``Environment.PyBuilder``.

    Returns:
        A decorator that returns the ``PyBuilder`` the build script calls.

    Raises:
        PyBuilderError: If *emitter* is given and isn't callable, or
            *discovers* is asked of a function that runs in another *cwd*.
    """
    if emitter is not None and not callable(emitter):
        raise PyBuilderError(
            f"emitter={emitter!r} is not callable. It is a function of the "
            f"build script, called when pcons resolves each edge: "
            f"emitter(targets, sources, env, **kwargs).",
            get_caller_location(),
        )
    if discovers and cwd is not None:
        raise PyBuilderError(
            "discovers=True with cwd= isn't supported: the build tool reads "
            "the paths the function reports from the build directory, and "
            "the function would report them from cwd. Leave out cwd= and "
            "change directory inside the function if it needs to.",
            get_caller_location(),
        )
    how = _HowToRun(
        python=python,
        restat=restat,
        write_if_different=write_if_different,
        cwd=cwd,
        launcher=launcher,
        env_vars=env_vars,
        worker=worker,
        depends=depends,
        emitter=emitter,
        discovers=discovers,
    )

    def decorate(fn: Callable[..., object]) -> PyBuilder:
        sys_path = None if how.python else _captured_sys_path()
        return PyBuilder(validate(fn, project=env._project), env, how, sys_path)

    return decorate
