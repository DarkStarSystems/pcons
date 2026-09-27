# SPDX-License-Identifier: MIT
"""One call's keyword arguments: checked at the call, pickled at resolve.

A ``Target``, a node or a ``Subst`` in them expands when the edge resolves,
inside the pickler, so it's replaced wherever it sits.
"""

from __future__ import annotations

import functools
import inspect
import io
import os
import pickle
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.core.errors import PconsError
from pcons.core.invocation import launcher_entry
from pcons.tools.pybuilder.errors import PyBuilderError, _and_list
from pcons.tools.pybuilder.files import MODULE_PREFIX
from pcons.util import pybuilder as runner
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Callable

    from pcons.core.environment import Environment
    from pcons.core.paths import PathResolver
    from pcons.tools.pybuilder.function import ValidatedFunction
    from pcons.util.source_location import SourceLocation


def _bind_arguments(
    function: types.FunctionType, kwargs: Mapping[str, Any], at: SourceLocation
) -> None:
    """Refuse a call the build-time call would refuse.

    The runner calls ``fn(targets, sources, **kwargs)``, so binding two
    placeholders and the keywords models that exactly: what binds here runs
    there, and what does not would have raised ``TypeError`` inside a
    generated module, with a traceback pointing at a file nobody wrote.

    A function whose own signature ends in ``**kwargs`` accepts every
    keyword, so nothing is refused for it. That is the function's contract,
    not a hole here.

    ``bind`` stops at the first thing it cannot place, and a missing
    parameter is the first thing it looks at, so a misspelled keyword is
    reported as the parameter it failed to fill. The message names what was
    passed as well as what the signature wants, which puts the two spellings
    side by side.

    Raises:
        PyBuilderError: Naming the cause where the shape has one, and quoting
            what the signature says otherwise.
    """
    signature = inspect.signature(function)
    _reject_edge_supplied_keywords(function, signature, kwargs, at)
    try:
        signature.bind(None, None, **kwargs)
    except TypeError as exc:
        given = _and_list(sorted(kwargs)) or "nothing"
        raise PyBuilderError(
            f"PyBuilder {function.__name__}{signature} cannot be called with "
            f"{given}: {exc}. At build time it is called as "
            f"{function.__name__}(targets, sources, **kwargs), so everything "
            f"past the first two parameters is a keyword of the call.",
            at,
        ) from exc


def _reject_edge_supplied_keywords(
    function: types.FunctionType,
    signature: inspect.Signature,
    kwargs: Mapping[str, Any],
    at: SourceLocation,
) -> None:
    """Refuse a keyword that names one of the two arguments the edge fills.

    ``report(target="o.txt", sources=["a.txt"])`` is a plausible slip for
    ``source=``, and ``bind`` answers it with "multiple values for argument
    'sources'", which never mentions that the edge's own inputs are spelled
    without the s. Only the function's first two parameters are refused, so a
    body that really does take a keyword called *sources* through its own
    ``**kwargs`` still gets it.

    Raises:
        PyBuilderError: Naming the clash and the singular spelling.
    """
    slots = [
        p.name
        for p in signature.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ][:2]
    clashing = sorted(set(kwargs) & set(slots))
    if not clashing:
        return
    plural = len(clashing) > 1
    edge_spelling = {"sources": "source=", "targets": "target="}
    meant = [edge_spelling[name] for name in clashing if name in edge_spelling]
    advice = (
        f" The edge's own files are spelled {_and_list(meant)}, which is "
        f"probably what you meant."
        if meant
        else ""
    )
    raise PyBuilderError(
        f"PyBuilder {function.__name__}{signature} already receives "
        f"{_and_list(clashing)} from the edge, so the call cannot pass "
        f"{'them' if plural else 'it'} as well.{advice}",
        at,
    )


@dataclass(frozen=True)
class CallArguments:
    """One call's arguments, checked, and pickled when the edge resolves.

    They're read then, as the environment is, so a keyword may name what
    only resolve knows: a target's files, or the flags the build settled on.

    Attributes:
        payload: What the runner unpickles, markers and all.
        markers: The targets, nodes and ``Subst`` templates in the payload.
        name: The function's name, for messages.
        at: The call, blamed by any message.
    """

    payload: dict[str, Any]
    markers: tuple[Any, ...]
    name: str
    at: SourceLocation

    @property
    def inputs(self) -> tuple[Any, ...]:
        """The targets and nodes the arguments name, which the function reads."""
        from pcons.core.subst import Subst

        return tuple(m for m in self.markers if not isinstance(m, Subst))

    def pickle(
        self, *, env: Environment, resolver: PathResolver, cwd: Path | None
    ) -> bytes:
        """The sidecar pickle's bytes, each marker expanded as the edge sees it.

        Args:
            env: The edge's environment, which expands a ``Subst``.
            resolver: The top project's resolver, which makes a path
                relative to where the function runs.
            cwd: Where the function runs, when that isn't the build
                directory.

        Raises:
            PyBuilderError: If a marker has nothing to expand to.
        """
        expand = functools.partial(
            _expansion, env=env, resolver=resolver, cwd=cwd, name=self.name, at=self.at
        )
        return _payload_bytes(self.payload, self.name, self.at, expand)


def check_arguments(
    function: ValidatedFunction,
    *,
    kwargs: Mapping[str, Any],
    sys_path: list[str] | None,
) -> CallArguments:
    """Everything about one call's arguments, settled before anything is written.

    Nothing reaches the build directory until this has returned, so a refused
    argument leaves no generated file behind and claims no path. That
    includes pickling them, although the pickle the runner reads is made at
    resolve: here, with a placeholder where each marker will be, is where a
    bad argument can still be blamed on the line that passed it.

    The arguments belong to the call rather than to the function, so the
    location is taken here rather than at decoration, and every message names
    the line that passed the value.

    Args:
        function: What :func:`validate` returned.
        kwargs: Keyword arguments for the build-time call.
        sys_path: ``sys.path`` as the decorating script had it, captured by
            :func:`py_builder`'s decorator, or None when ``python=`` names
            another interpreter whose own path applies instead.

    Returns:
        The checked arguments, ready to pickle once the edge resolves.

    Raises:
        PyBuilderError: If the arguments do not fit the function's signature,
            if one of them holds a piece of the build description, if one of
            them would import pcons when unpickled at build time, or if one
            of them cannot be pickled.
    """
    at = get_caller_location()
    name = function.function.__name__
    _bind_arguments(function.function, kwargs, at)
    _reject_description_objects(kwargs, name, at)
    _reject_pcons_references(kwargs, name, at)
    payload = {
        "version": runner.PROTOCOL_VERSION,
        "module": f"{MODULE_PREFIX}{function.module_stem}",
        "function": name,
        "kwargs": dict(kwargs),
        "path": sys_path,
    }
    markers: list[Any] = []

    def probe(marker: object) -> str:
        markers.append(marker)
        return _placeholder(marker)

    _payload_bytes(payload, name, at, probe)
    return CallArguments(payload, tuple(markers), name, at)


def _expansion(
    marker: object,
    *,
    env: Environment,
    resolver: PathResolver,
    cwd: Path | None,
    name: str,
    at: SourceLocation,
) -> str | list[str]:
    """What one marker becomes in the pickle, once the edge has resolved.

    A node is its path and a target the list of its files' paths, each as
    the function opens it from the directory it runs in, the way its own
    sources arrive. A ``Subst`` is its template's tokens in the edge's
    environment, with any path in them seen from there too.

    Raises:
        PyBuilderError: If a template does not expand, or a target builds no
            file to name.
    """
    from pcons.core.node import DirNode, FileNode
    from pcons.core.subst import PathToken, Subst
    from pcons.core.target import Target as TargetClass

    def seen(path: Path | str, *, built: bool) -> str:
        return resolver.make_command_relative(path, built=built, cwd=cwd)

    if isinstance(marker, Subst):
        try:
            tokens: list[Any] = env.subst_list(marker.template)
        except PconsError as exc:
            raise PyBuilderError(
                f"PyBuilder {name}(): Subst({marker.template!r}) does not "
                f"expand in this environment: {exc}",
                at,
            ) from exc
        return [
            t.relativize(lambda p: seen(p, built=False))
            if isinstance(t, PathToken)
            else str(t)
            for t in tokens
        ]
    if isinstance(marker, (FileNode, DirNode)):
        return seen(marker.path, built=marker.is_built)
    target = cast("TargetClass", marker)
    if not target.output_nodes:
        raise PyBuilderError(
            f"PyBuilder {name}(): an argument is the target {target.name!r}, "
            f"which builds no file, so there is no path to pass.",
            at,
        )
    return [seen(node.path, built=True) for node in target.output_nodes]


def _describe_description_object(value: object) -> tuple[str, str] | None:
    """What *value* is and what to do with it, when it cannot cross into a build.

    Nothing of the build description exists when the function runs, and most
    of it pickles without complaining, so one of these passed through
    ``kwargs`` would arrive at build time as a stale copy of the graph. That
    is worse than an error, so it is one.

    A target or a node is not on this list: the call turns those into the
    paths the function can open, when the build resolves.
    """
    from pcons.core.environment import Environment
    from pcons.core.node import AliasNode
    from pcons.core.project import Project as ProjectClass
    from pcons.core.toolconfig import ToolConfig

    if isinstance(value, AliasNode):
        return (
            f"the alias {value.name!r}, which groups targets rather than naming a file",
            "Pass the targets it groups, and the function receives their files' paths.",
        )
    if isinstance(value, ToolConfig):
        return (
            f"the {value.name!r} tool namespace, which holds the environment "
            f"it belongs to",
            f"Read the values the function needs here, and pass those: "
            f"env.{value.name}.flags rather than env.{value.name}.",
        )
    if isinstance(value, (Environment, ProjectClass)):
        kind = "environment" if isinstance(value, Environment) else "project"
        return (
            f"the {kind} itself",
            "Read what the function needs from it here, and pass that: a "
            "string, a path, a number.",
        )
    return None


def _reject_description_objects(
    kwargs: Mapping[str, Any], name: str, at: SourceLocation
) -> None:
    """Refuse a kwarg holding a piece of the build description.

    Also refuse one holding a target or a ``Subst`` where its expansion, a
    list, cannot go: a set, or a dictionary key.

    Raises:
        PyBuilderError: Naming where it sits and what to write instead.
    """
    from pcons.core.subst import Subst
    from pcons.core.target import Target as TargetClass

    seen: set[int] = set()

    def walk(value: object, where: str, hashed: bool = False) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        described = _describe_description_object(value)
        if described is not None:
            where_it_is, remedy = described
            raise PyBuilderError(
                f"PyBuilder {name}(): {where} is {where_it_is}, and the build "
                f"description does not exist when the function runs. {remedy}",
                at,
            )
        if hashed and isinstance(value, (TargetClass, Subst)):
            what = (
                f"Subst({value.template!r})"
                if isinstance(value, Subst)
                else f"the target {value.name!r}"
            )
            raise PyBuilderError(
                f"PyBuilder {name}(): {where} is {what}, which becomes a "
                f"list when the build resolves, and a list cannot be a set "
                f"element or a dictionary key. Put it in a list, a tuple, or "
                f"a dictionary value.",
                at,
            )
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(key, f"a key of {where}", hashed=True)
                walk(item, f"{where}[{key!r}]")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{where}[{index}]", hashed)
        elif isinstance(value, (set, frozenset)):
            for item in value:
                walk(item, f"an element of {where}", hashed=True)

    for key, value in kwargs.items():
        walk(value, f"argument {key}")


def _expands(value: object) -> bool:
    """Whether *value* is pickled as what it expands to when the edge resolves.

    A target or a node becomes the paths it names, a ``Subst`` the tokens its
    template makes in the edge's environment.
    """
    from pcons.core.node import DirNode, FileNode
    from pcons.core.subst import Subst
    from pcons.core.target import Target as TargetClass

    return isinstance(value, (TargetClass, FileNode, DirNode, Subst))


def _placeholder(marker: object) -> str:  # noqa: ARG001
    """What a marker pickles as before the build has resolved it."""
    return ""


class _ExpandingPickler(pickle.Pickler):
    """Pickles a call's arguments with each marker replaced by *expand*'s result.

    ``reducer_override`` sees every object pickle does not write itself,
    wherever it sits, so a marker expands in place in a list, a dictionary
    value or an object's attribute alike, and nothing here rebuilds a
    container. An expansion is a string or a list of them, which pickle then
    writes as usual. The protocol is pinned so an interpreter upgrade doesn't
    rewrite every sidecar and rebuild the world once for nothing.
    """

    def __init__(self, file: io.BytesIO, expand: Callable[[Any], Any]) -> None:
        super().__init__(file, protocol=5)
        self._expand = expand

    def reducer_override(self, obj: object) -> Any:
        if _expands(obj):
            value = self._expand(obj)
            return type(value), (value,)
        return NotImplemented


def _pickled(value: object, expand: Callable[[Any], Any] = _placeholder) -> bytes:
    """*value*'s pickle, with each marker in it expanded by *expand*."""
    buffer = io.BytesIO()
    _ExpandingPickler(buffer, expand).dump(value)
    return buffer.getvalue()


class _PconsReferenceFinder(_ExpandingPickler):
    """A pickler that records the first pcons object it would write.

    ``reducer_override`` runs for every object pickle does not special-case
    as a built-in scalar or container, which includes a class or a function
    written by reference: those never reach ``__reduce_ex__``, so a scan of
    the finished opcodes would have to name them from the memo instead.
    Recording here, one object at a time, also keeps the argument that held
    it, which the opcodes alone do not carry.
    """

    def __init__(self, file: io.BytesIO) -> None:
        super().__init__(file, _placeholder)
        self.found: str | None = None

    def reducer_override(self, obj: object) -> Any:
        if _expands(obj):
            return super().reducer_override(obj)
        if self.found is None:
            by_reference = isinstance(obj, (type, types.FunctionType))
            module = obj.__module__ if by_reference else type(obj).__module__
            if module is not None and (
                module == "pcons" or module.startswith("pcons.")
            ):
                kind = "function" if isinstance(obj, types.FunctionType) else "class"
                label = f"{kind} {obj.__name__}" if by_reference else type(obj).__name__
                self.found = label
        return NotImplemented


def _pcons_reference(value: object) -> str | None:
    """What *value* pickles as, if pickling it would reach into pcons."""
    finder = _PconsReferenceFinder(io.BytesIO())
    try:
        finder.dump(value)
    except Exception:  # noqa: BLE001
        pass
    return finder.found


def _reject_pcons_references(
    kwargs: Mapping[str, Any], name: str, at: SourceLocation
) -> None:
    """Refuse a kwarg whose pickle would import pcons at build time.

    A pcons value pickles without complaining, so it would otherwise reach
    the runner as a payload that imports ``pcons`` to unpickle, which the
    runner is not meant to do.

    Raises:
        PyBuilderError: Naming the argument and what it holds.
    """
    for key, value in kwargs.items():
        found = _pcons_reference(value)
        if found is None:
            continue
        raise PyBuilderError(
            f"PyBuilder {name}(): argument {key} holds a pcons {found}, and "
            f"unpickling it at build time would import pcons. Pass "
            f'Subst("$tool.name") for the value the build settles on, or '
            f"list(...) to copy the values out now.",
            at,
        )


def _payload_bytes(
    payload: dict[str, Any],
    name: str,
    at: SourceLocation,
    expand: Callable[[Any], Any] = _placeholder,
) -> bytes:
    """The sidecar pickle's bytes, each marker expanded by *expand*.

    Raises:
        PyBuilderError: Naming the arguments that cannot be pickled.
    """
    try:
        return _pickled(payload, expand)
    except (pickle.PicklingError, TypeError, AttributeError) as exc:
        bad = _unpicklable(payload["kwargs"])
        label = (
            f"{'arguments' if len(bad) > 1 else 'argument'} {_and_list(bad)}"
            if bad
            else "one of its arguments"
        )
        raise PyBuilderError(
            f"PyBuilder {name}() cannot pickle {label}: {exc}. "
            f"Arguments travel to build time as a file, so each one must be "
            f"picklable. Pass what describes it instead, a path or a string, "
            f"and build the object inside the function.",
            at,
        ) from exc


def _unpicklable(kwargs: dict[str, Any]) -> list[str]:
    """The keys whose values pickle refuses, one probe per key."""
    bad: list[str] = []
    for key, value in kwargs.items():
        try:
            _pickled(value)
        except Exception:  # noqa: BLE001
            bad.append(key)
    return bad


def _captured_sys_path() -> list[str]:
    """``sys.path`` as the decorating script has it right now, one entry each.

    Order and duplicates kept, except the launcher entry: the first entry
    equal to :func:`pcons.core.invocation.launcher_entry`, an artifact of
    how this process happened to be started rather than something the
    script itself put on its path, is left out. Compared through
    ``os.path.normcase``, so a launcher and a capture that spell the same
    path with a different separator, or in different case on Windows,
    still match. Each remaining entry is made absolute immediately: a
    relative or empty entry names the build script's own directory or
    current directory, which is not the edge's once the runner takes over.
    """
    entries = [os.path.abspath(entry) for entry in sys.path]
    launcher = launcher_entry()
    if launcher is not None:
        normalized_launcher = os.path.normcase(launcher)
        for index, entry in enumerate(entries):
            if os.path.normcase(entry) == normalized_launcher:
                del entries[index]
                break
    return [Path(entry).as_posix() for entry in entries]
