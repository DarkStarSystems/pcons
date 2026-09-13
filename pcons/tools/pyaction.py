# SPDX-License-Identifier: MIT
"""The whole of ``env.PyAction`` except its public name.

``Environment.PyAction`` is a forwarder into :func:`py_action` here, the
way ``Project.cli_command`` forwards into ``pcons.commands``. Source
extraction and emission are the other half.

A function typed in a build script cannot be pickled. The script runs under
``__name__ == "__pcons__"`` and that module is never importable, so pickle,
which stores a function by module and qualname, has nothing to store. The body
reaches build time as a real file instead. :func:`validate` reads the
function once, at decoration. :func:`check_arguments` reads one call's
arguments. :func:`emit_module` writes the function's own source to a
generated module, once per path it lands on, and :func:`emit_args` writes one
edge's arguments to a sidecar pickle beside it. Both hand back the
node-canonical path the build edge names.

The generated module holds the function and nothing else, so a body that uses
a name the build script imported would fail at build time with ``NameError``.
That is caught here instead, along with every other function shape this design
cannot carry, each with its own :class:`PyActionError`.
"""

from __future__ import annotations

import ast
import dis
import functools
import inspect
import pickle
import re
import sys
import textwrap
import types
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.core.builder import anchor_target_paths
from pcons.core.errors import PconsError
from pcons.core.invocation import RUN_NAME
from pcons.util import pyaction as runner
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation

GEN_DIR = "pyact"
MODULE_PREFIX = "pcons_pyact_"

_SAFE_GLOBALS = frozenset({"__name__", "__doc__", "__builtins__"})


class PyActionError(PconsError):
    """A function cannot be turned into a build edge."""


_claimed: weakref.WeakKeyDictionary[
    Project, dict[Path, tuple[SourceLocation, Environment, ValidatedAction | None]]
] = weakref.WeakKeyDictionary()


@dataclass(frozen=True, eq=False)
class ValidatedAction:
    """A function that can be carried to build time, and the module for it.

    What :func:`validate` settles depends on the function alone, so it is
    settled once even when many edges run the same function. What
    :func:`emit_module` and :func:`emit_args` write depends on the
    environment and on the edge, so they run per call.

    ``eq=False`` on purpose: the claim registry tells one action's module from
    another's by identity, and two validations of one ``def`` inside a factory
    are two actions that a generated ``__eq__`` would call equal. Identity is
    the only equality this class has, so ``is`` is the only thing anyone can
    write.

    Attributes:
        function: The function a build script wrote.
        module_text: The whole content of the generated module.
        at: Where the build script handed the function over.
    """

    function: types.FunctionType
    module_text: str
    at: SourceLocation

    @property
    def module_stem(self) -> str:
        """The generated module's file name, without its suffix.

        From the function, never from the edge: one function is one module
        however many edges read it, and two edges of one function would
        otherwise write byte-identical twins.
        """
        return _sanitized(self.function.__name__)


def function_source(fn: Callable[..., object]) -> str:
    """The ``def`` of *fn* alone, with the decorators above it removed.

    Args:
        fn: The function a build script defined.

    Returns:
        The function's source, dedented, ending in a newline.

    Raises:
        PyActionError: If the source cannot be read, or is not a plain
            ``def``.
    """
    return _extract(fn, None)[0]


def _extract(
    fn: Callable[..., object], at: SourceLocation | None
) -> tuple[str, ast.FunctionDef]:
    """The function's own source, and the syntax tree it was read from.

    ``ast.FunctionDef.lineno`` is the line of the ``def`` keyword rather than
    of the first decorator, so slicing there drops the decoration whatever
    shape it had: several decorators, a call spanning lines, a comment above.

    Args:
        fn: The function a build script defined.
        at: Where the build script asked for it, for the messages.

    Returns:
        The source, dedented and ending in a newline, and its ``FunctionDef``.

    Raises:
        PyActionError: If the source cannot be read, or is not a plain
            ``def``.
    """
    try:
        text = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError) as exc:
        raise PyActionError(
            f"PyAction cannot read the source of {_describe(fn)}: {exc}. "
            f"The function must be written out in a build script.",
            at,
        ) from exc
    node = ast.parse(text).body[0]
    if not isinstance(node, ast.FunctionDef):
        raise PyActionError(
            f"PyAction needs a plain function: {_not_a_def(fn, node)}", at
        )
    return "".join(text.splitlines(keepends=True)[node.lineno - 1 :]), node


def validate(fn: Callable[..., object], *, project: Project) -> ValidatedAction:
    """Everything about *fn* that one look at the function settles.

    Nothing is written here. A function this refuses never reaches a build
    directory, and a function it accepts can be emitted as often as there
    are edges for it.

    Args:
        fn: The function to run at build time.
        project: The project whose root the module's origin line is relative
            to. The origin names the script that wrote the function, which
            does not change when another environment emits it.

    Returns:
        The function, the module text, and where the build script asked.

    Raises:
        PyActionError: If the function cannot be carried to build time.
    """
    at = get_caller_location()
    name = _decoration_name(fn)
    function = _plain_function(fn, name, at)
    _reject_uncallable_signature(function, at)
    _reject_reserved_parameters(function, at)
    source, node = _extract(function, at)
    _reject_script_globals(function, node, name, at)
    return ValidatedAction(function, _module_text(source, project, at), at)


def _decoration_name(fn: Callable[..., object]) -> str:
    """How a message names what the decorator was handed.

    The edge has no name yet at decoration, and the function is what the
    reader is looking at.
    """
    return getattr(fn, "__name__", None) or type(fn).__name__


@functools.cache
def _reserved_names() -> frozenset[str]:
    """The parameter names :meth:`PyAction.__call__` spends on the edge.

    Read from that signature rather than listed beside it, so a name added to
    the call is reserved by the same edit and the two cannot disagree.
    ``self`` and the ``**kwargs`` that carry the function's own arguments are
    not keyword-only parameters, so they fall out on their own.
    """
    return frozenset(
        name
        for name, parameter in inspect.signature(PyAction.__call__).parameters.items()
        if parameter.kind is parameter.KEYWORD_ONLY
    )


def _and_list(names: Sequence[str]) -> str:
    """Names as a reader would say them, with the last joined by "and"."""
    if len(names) < 2:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _reject_reserved_parameters(
    function: types.FunctionType, at: SourceLocation
) -> None:
    """Refuse a parameter whose name the call already spends on the edge.

    Raises:
        PyActionError: Naming the parameters to rename.
    """
    taken = sorted(set(inspect.signature(function).parameters) & _reserved_names())
    if not taken:
        return
    plural = len(taken) > 1
    raise PyActionError(
        f"PyAction {function.__name__}() has {_and_list(taken)} as "
        f"{'parameter names' if plural else 'a parameter name'}, and the call "
        f"spends {'those names' if plural else 'that name'} on the edge "
        f"itself. Rename {'them' if plural else 'it'} in the def and at the "
        f"call: the function receives sources and targets as its first two "
        f"arguments, and everything else as a keyword of the call.",
        at,
    )


def _reject_uncallable_signature(
    function: types.FunctionType, at: SourceLocation
) -> None:
    """Refuse a signature the build-time call could never satisfy.

    The runner calls ``fn(sources, targets, **kwargs)``. Two shapes make that
    impossible however the call is written, and both are properties of the
    ``def``, so they are refused where the ``def`` is rather than at the first
    call. Left to ``inspect``, each is reported in the words of the probe
    rather than of the mistake: the first as "too many positional arguments",
    the second as a missing "positional-only" argument to somebody who just
    typed that keyword.

    Raises:
        PyActionError: Naming which half of the call shape is impossible.
    """
    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())
    kinds = {p.kind for p in parameters}
    slots = [
        p for p in parameters if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(slots) < 2 and inspect.Parameter.VAR_POSITIONAL not in kinds:
        raise PyActionError(
            f"PyAction {function.__name__}{signature} cannot receive sources "
            f"and targets: it has "
            f"{'only one parameter' if len(slots) == 1 else 'no parameters'} "
            f"that can be filled positionally. At build time it is called as "
            f"{function.__name__}(sources, targets, **kwargs), so write the "
            f"first two as plain parameters: "
            f"def {function.__name__}(sources, targets, ...).",
            at,
        )
    late = [p.name for p in parameters[2:] if p.kind is p.POSITIONAL_ONLY]
    if late:
        plural = len(late) > 1
        raise PyActionError(
            f"PyAction {function.__name__}{signature} cannot be given "
            f"{_and_list(late)}: {'they are' if plural else 'it is'} "
            f"positional-only, and everything past sources and targets "
            f"arrives as a keyword of the call. Move the / up so it follows "
            f"targets: def {function.__name__}(sources, targets, /, "
            f"{', '.join(late)}).",
            at,
        )


def _bind_arguments(
    function: types.FunctionType, kwargs: Mapping[str, Any], at: SourceLocation
) -> None:
    """Refuse a call the build-time call would refuse.

    The runner calls ``fn(sources, targets, **kwargs)``, so binding two
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
        PyActionError: Naming the cause where the shape has one, and quoting
            what the signature says otherwise.
    """
    signature = inspect.signature(function)
    _reject_edge_supplied_keywords(function, signature, kwargs, at)
    try:
        signature.bind(None, None, **kwargs)
    except TypeError as exc:
        given = _and_list(sorted(kwargs)) or "nothing"
        raise PyActionError(
            f"PyAction {function.__name__}{signature} cannot be called with "
            f"{given}: {exc}. At build time it is called as "
            f"{function.__name__}(sources, targets, **kwargs), so everything "
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
        PyActionError: Naming the clash and the singular spelling.
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
    raise PyActionError(
        f"PyAction {function.__name__}{signature} already receives "
        f"{_and_list(clashing)} from the edge, so the call cannot pass "
        f"{'them' if plural else 'it'} as well.{advice}",
        at,
    )


def emit_module(action: ValidatedAction, *, project: Project, env: Environment) -> Path:
    """Write the generated module for *env*, once per path it lands on.

    The same action reaching one path again is one file two edges read, so
    the second claim succeeds and skips the write. A different action on that
    path is two functions of one name, which is an error.

    Args:
        action: What :func:`validate` returned.
        project: Any project of the tree; the claim registry hangs off its top.
        env: The environment whose build directory holds the module.

    Returns:
        The module's path, relative to the build directory and anchored the
        way a node path is. Neither a disk path nor what ``env.Command``
        takes as a source: write to ``root / path``, and hand the builder
        ``project.node(path)``, or a subdirectory's offset is applied twice.

    Raises:
        PyActionError: If another action already wrote that file.
    """
    module_rel = _gen_dir(env) / f"{action.module_stem}.py"
    claimed = _claim(
        project,
        env,
        module_rel,
        action.function.__name__,
        action.at,
        owner=action,
    )
    if claimed:
        _write_if_changed(
            project._path_resolver.project_root / module_rel,
            action.module_text.encode("utf-8"),
        )
    return module_rel


def check_arguments(action: ValidatedAction, *, kwargs: Mapping[str, Any]) -> bytes:
    """Everything about one call's arguments, settled before anything is written.

    Nothing reaches the build directory until this has returned, so a refused
    argument leaves no generated file behind and claims no path.

    The arguments belong to the call rather than to the function, so the
    location is taken here rather than at decoration, and every message names
    the line that passed the value.

    Args:
        action: What :func:`validate` returned.
        kwargs: Keyword arguments for the build-time call.

    Returns:
        The sidecar pickle's bytes, ready for :func:`emit_args`.

    Raises:
        PyActionError: If the arguments do not fit the function's signature,
            if one of them holds a piece of the build description, or if one
            of them cannot be pickled.
    """
    at = get_caller_location()
    name = action.function.__name__
    _bind_arguments(action.function, kwargs, at)
    _reject_description_objects(kwargs, name, at)
    return _payload_bytes(
        {
            "version": runner.PROTOCOL_VERSION,
            "module": f"{MODULE_PREFIX}{action.module_stem}",
            "function": name,
            "kwargs": dict(kwargs),
        },
        name,
        at,
    )


def emit_args(*, project: Project, env: Environment, name: str, payload: bytes) -> Path:
    """Write one edge's argument pickle.

    One edge is one pickle, so this path is exclusive: nothing may share it,
    not even the action that claimed the module beside it.

    Args:
        project: Any project of the tree; the claim registry hangs off its top.
        env: The environment whose build directory holds the pickle.
        name: The edge's name, which the pickle is named after.
        payload: What :func:`check_arguments` returned.

    Returns:
        The pickle's path, anchored the way :func:`emit_module` returns one.

    Raises:
        PyActionError: If another edge already claimed that file.
    """
    args_rel = _gen_dir(env) / f"{_sanitized(name)}.args.pkl"
    _claim(project, env, args_rel, name, get_caller_location(), owner=None)
    _write_if_changed(project._path_resolver.project_root / args_rel, payload)
    return args_rel


def _gen_dir(env: Environment) -> Path:
    """Where both generated files go, anchored the way a node path is."""
    return anchor_target_paths(env, [Path(GEN_DIR)])[0]


def _describe(fn: Callable[..., object]) -> str:
    """Name a callable the way an error message should."""
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
    return f"{name}()" if name else repr(fn)


def _not_a_def(fn: Callable[..., object], node: ast.stmt) -> str:
    """Why the source that was found is not a function definition.

    The second case is reached by a function whose source does not start at a
    ``def``: a lambda given another ``__name__``, or a function assembled at
    run time from another one's code object.
    """
    if isinstance(node, ast.AsyncFunctionDef):
        return (
            f"{_describe(fn)} is a coroutine function, which a build edge "
            f"cannot await. Write it as a plain def."
        )
    return (
        f"the source found for {_describe(fn)} is not a def, it reads as "
        f"{type(node).__name__}. A function built at run time has no def to "
        f"extract: write one out in the build script."
    )


def _plain_function(
    fn: Callable[..., object], name: str, at: SourceLocation
) -> types.FunctionType:
    """*fn* itself, refusing every shape source extraction cannot carry.

    Args:
        fn: The callable the decorator was given.
        name: The edge's name, for the messages.
        at: Where the build script asked for it.

    Returns:
        The same function, known to be a plain one.

    Raises:
        PyActionError: With one message per rejected shape.
    """
    if isinstance(fn, functools.partial):
        raise PyActionError(
            "PyAction was given a functools.partial. Pass the function itself "
            "and give its bound arguments to the call: "
            "action(target=..., bound=value).",
            at,
        )
    if not isinstance(fn, types.FunctionType):
        raise PyActionError(
            f"PyAction needs a function written in a build script, not "
            f"{_describe(fn)} of type {type(fn).__name__}. Write a def beside "
            f"the other targets and pass what it needs at the call: "
            f"action(target=..., value=...).",
            at,
        )
    if fn.__name__ == "<lambda>":
        raise PyActionError(
            "PyAction was given a lambda. Its source cannot be extracted on "
            "its own: write it as a def.",
            at,
        )
    if fn.__closure__ is not None:
        free = sorted(fn.__code__.co_freevars)
        plural = len(free) > 1
        raise PyActionError(
            f"PyAction {name}() reads {_and_list(free)} from the function it "
            f"is nested in. Only the function's own source travels to build "
            f"time, so there is nothing to read "
            f"{'them' if plural else 'it'} from. Take "
            f"{'them' if plural else 'it'} as "
            f"{'parameters' if plural else 'a parameter'} and pass "
            f"{'them' if plural else 'it'} at the call: "
            f"{name}(target=..., {'=..., '.join(free)}=...).",
            at,
        )
    if _class_scoped(fn):
        raise PyActionError(
            f"PyAction was given {_describe(fn)}, defined in a class body. "
            f"Only a plain function can be extracted: move the def out of "
            f"the class.",
            at,
        )
    return fn


def _class_scoped(fn: types.FunctionType) -> bool:
    """Whether *fn* was defined in a class body rather than at function scope."""
    parts = fn.__qualname__.split(".")
    return len(parts) > 1 and parts[-2] != "<locals>"


def _global_loads(code: types.CodeType) -> set[str]:
    """Every global name *code* loads, its nested code objects included.

    Read from the bytecode rather than from ``co_names``, which mixes in every
    attribute name the body touches and would report ``out.write`` as a global
    named ``write``.
    """
    names = {
        ins.argval for ins in dis.get_instructions(code) if ins.opname == "LOAD_GLOBAL"
    }
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _global_loads(const)
    return names


def _evaluated_names(node: ast.FunctionDef) -> set[str]:
    """The names the generated module evaluates when it defines the function.

    Default values, and nothing else. A default is evaluated at ``def`` time,
    so it runs again in the generated module, where the build script's globals
    are gone. An annotation is not: the generated module carries ``from
    __future__ import annotations``, which leaves every annotation an
    unevaluated string, so a parameter typed ``out: Path`` costs nothing
    there.
    """
    names: set[str] = set()
    for default in (*node.args.defaults, *node.args.kw_defaults):
        if default is not None:
            names |= {sub.id for sub in ast.walk(default) if isinstance(sub, ast.Name)}
    return names


def _defined_by_the_script(value: object) -> bool:
    """Whether the build script itself defines this, so no import can reach it.

    A build script runs as ``__pcons__``, a module nothing can import, so a
    helper defined beside the targets has nowhere to be imported from.
    ``__main__`` is the same situation from the other direction: importing it
    by name would run a second copy of the script rather than reach this one.
    """
    return getattr(value, "__module__", None) in (RUN_NAME, "__main__")


def _global_remedy(
    found: str,
    value: object,
    from_default: bool,
    imports: list[str],
    parameters: list[str],
) -> str | None:
    """What to type instead, for one name the body reads from the script.

    An import line is only ever synthesised for a module, where the module's
    own name is the whole answer. For anything else the script's existing
    import is the thing to move, and echoing a line built from
    ``__module__`` would name the implementation rather than the module the
    script imported: ``from os.path import join`` would come back as
    ``from posixpath import join``, which is wrong on Windows.

    Args:
        found: The name.
        value: What the build script has under it.
        from_default: Whether it was read by a parameter's default value.
        imports: Collects the import lines, which are answered together.
        parameters: Collects the names that have to become parameters, which
            are answered together too, in one call rather than one per name.

    Returns:
        A sentence, or None when the name joins the import advice instead.
    """
    if _defined_by_the_script(value):
        return (
            f"{found} lives only in this build script, which is not a module "
            f"anything can import: write out what it does inside the "
            f"function body, or move it to a module the build can import."
        )
    if from_default:
        parameters.append(found)
        return (
            f"{found} is a parameter's default value, and a default is "
            f"evaluated again where the generated module defines the "
            f"function, so write the parameter without a default."
        )
    if isinstance(value, types.ModuleType):
        imports.append(f"import {value.__name__}")
        return None
    if callable(value) or isinstance(value, type):
        return (
            f"Import {found} inside the function body, the way this script imports it."
        )
    parameters.append(found)
    return None


def _reject_script_globals(
    fn: types.FunctionType, node: ast.FunctionDef, name: str, at: SourceLocation
) -> None:
    """Refuse a body that reads a name only the build script defines.

    A name that is not an identifier is not one a build script could have
    written: pytest rewrites the asserts of a test module and its injected
    ``@pytest_ar`` would otherwise be reported as a global of the body.

    Raises:
        PyActionError: Naming those globals and what to type instead.
    """
    allowed = _SAFE_GLOBALS | {fn.__name__}
    defaults = _evaluated_names(node)
    suspect = sorted(
        found
        for found in _global_loads(fn.__code__) | defaults
        if found.isidentifier() and found in fn.__globals__ and found not in allowed
    )
    if not suspect:
        return

    if "__file__" in suspect:
        raise PyActionError(
            f"PyAction {name}() uses __file__, which at build time names the "
            f"generated module rather than this script. Take the path it "
            f"means as a parameter and pass it at the call, "
            f'{name}(target=..., here=project.root_dir / "...").',
            at,
        )

    imports: list[str] = []
    parameters: list[str] = []
    remedies = [
        remedy
        for found in suspect
        if (
            remedy := _global_remedy(
                found, fn.__globals__[found], found in defaults, imports, parameters
            )
        )
        is not None
    ]
    if parameters:
        plural = len(parameters) > 1
        passed = ", ".join(f"{each}={each}" for each in parameters)
        remedies.insert(
            0,
            f"Take {_and_list(parameters)} as "
            f"{'parameters' if plural else 'a parameter'} and pass "
            f"{'them' if plural else 'it'} at the call, "
            f"{name}(target=..., {passed}).",
        )
    if imports:
        written = ", ".join(f'"{line}"' for line in dict.fromkeys(imports))
        remedies.insert(
            0, f"Write {written} at the top of the function body, not of the script."
        )
    those = "those names" if len(suspect) > 1 else "that name"
    raise PyActionError(
        f"PyAction {name}() uses {_and_list(suspect)} from the build script, "
        f"and only the function's own source travels to build time, so "
        f"nothing defines {those} there. " + " ".join(remedies),
        at,
    )


def _sanitized(name: str) -> str:
    """*name* reduced to what is legal in a file name and a module name."""
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


def _env_label(env: Environment) -> str:
    """How to name an environment in a message, or nothing when it is unnamed."""
    return f" in environment {env.name!r}" if env.name else ""


def _claim(
    project: Project,
    env: Environment,
    path: Path,
    name: str,
    at: SourceLocation,
    *,
    owner: ValidatedAction | None,
) -> bool:
    """Record that *path* is taken, and say whether the caller should write.

    The registry hangs off the top-level project rather than off this module,
    so a second project in the same process starts clean.

    *owner* is what may legally reach one path twice. A module's owner is the
    :class:`ValidatedAction` behind it, so one action emitting into two
    environments that share a build directory writes one file and two edges
    read it. A pickle passes ``None``, which makes its path exclusive,
    because one edge's arguments are nobody else's.

    Two environments decorating one function through a factory land on the
    same source line, so the environment is what tells the two claims apart,
    and giving one of them a ``build_prefix`` is the fix the factory shape
    calls for.

    Returns:
        True when the claim is new and the file has to be written, False when
        *owner* already holds that path and the bytes are there.

    Raises:
        PyActionError: If somebody else already claimed that file.
    """
    taken = _claimed.setdefault(project.top, {})
    first = taken.get(path)
    if first is None:
        taken[path] = (at, env, owner)
        return True
    first_at, first_env, first_owner = first
    if owner is not None and owner is first_owner:
        return False
    advice = _collision_advice(owner, first_owner, same_env=first_env is env)
    subject = f"PyAction {name}()" if owner is not None else f"PyAction edge {name!r}"
    wrote = "the PyAction" if owner is not None else "the edge"
    raise PyActionError(
        f"{subject}{_env_label(env)} would overwrite {path.as_posix()}, "
        f"already written by {wrote}{_env_label(first_env)} at {first_at}. "
        f"{advice}",
        at,
    )


def _collision_advice(
    owner: ValidatedAction | None,
    first: ValidatedAction | None,
    *,
    same_env: bool,
) -> str:
    """What to do about two claims on one path, in the words that apply.

    A pickle belongs to one edge, so naming one of the edges parts them. A
    module belongs to one function, so naming an edge does nothing for it:
    when two environments share a build directory, only a build_prefix parts
    them, and when one environment claims twice, the answer turns on whether
    it is one function or two. The module text is what tells those apart. A
    factory that writes the ``def`` inside itself makes a fresh function
    object every call, so comparing the objects would report a name clash
    where there is one function and no clash at all.
    """
    fixes: list[str] = []
    if not same_env:
        fixes.append("give one environment its own build_prefix")
    if owner is None or first is None:
        fixes.append('name one of the edges, name="something-else"')
    elif owner.module_text != first.module_text:
        fixes.append("rename one of the functions")
    elif same_env:
        fixes.append("decorate the function once and call the action twice")
    sentence = ", or ".join(fixes)
    return f"{sentence[:1].upper()}{sentence[1:]}."


def _origin(project: Project, at: SourceLocation) -> str:
    """Which script the generated module came from.

    The file only, never the line: a line number would change whenever a line
    is inserted above the decoration, rewriting a module whose function did
    not change and re-running the edge that reads it, which is the one thing
    ``_write_if_changed`` exists to avoid. Relative to the project root where
    possible and the bare file name otherwise, so the generated bytes say the
    same thing in every checkout.
    """
    path = Path(at.filename)
    try:
        return path.relative_to(project._path_resolver.project_root).as_posix()
    except ValueError:
        return path.name


def _module_text(source: str, project: Project, at: SourceLocation) -> str:
    """The whole content of the generated module.

    ``from __future__ import annotations`` is what makes a parameter
    annotation free: the build script's ``out: Path`` is never evaluated
    here, where ``Path`` was never imported.
    """
    return (
        "# SPDX-License-Identifier: MIT\n"
        f"# Generated by pcons from {_origin(project, at)}. Do not edit.\n"
        "\n"
        "from __future__ import annotations\n"
        "\n\n"
        f"{source}"
    )


def _describe_description_object(value: object) -> tuple[str, str] | None:
    """What *value* is and what to do with it, when it cannot cross into a build.

    Nothing of the build description exists when the function runs, and most
    of it pickles without complaining, so a target passed through ``kwargs``
    would arrive at build time as a stale copy of the graph with no edge
    behind it. That is worse than an error, so it is one.
    """
    from pcons.core.environment import Environment
    from pcons.core.node import Node
    from pcons.core.project import Project as ProjectClass
    from pcons.core.target import Target as TargetClass
    from pcons.core.toolconfig import ToolConfig

    if isinstance(value, TargetClass):
        return (
            f"the target {value.name!r}",
            "List it in source= instead, and the function receives its "
            "output paths in sources.",
        )
    if isinstance(value, Node):
        return (
            f"the build graph's file {value.name!r}",
            "List it in source= instead, and the function receives its path "
            "in sources.",
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

    Raises:
        PyActionError: Naming where it sits and what to write instead.
    """
    seen: set[int] = set()

    def walk(value: object, where: str) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        described = _describe_description_object(value)
        if described is not None:
            where_it_is, remedy = described
            raise PyActionError(
                f"PyAction {name}(): {where} is {where_it_is}, and the build "
                f"description does not exist when the function runs. {remedy}",
                at,
            )
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(key, f"a key of {where}")
                walk(item, f"{where}[{key!r}]")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{where}[{index}]")
        elif isinstance(value, (set, frozenset)):
            for item in value:
                walk(item, f"an element of {where}")

    for key, value in kwargs.items():
        walk(value, f"argument {key}")


def _payload_bytes(payload: dict[str, Any], name: str, at: SourceLocation) -> bytes:
    """The sidecar pickle's bytes, at a fixed protocol.

    The protocol is pinned so an interpreter upgrade does not rewrite every
    sidecar and rebuild the world once for nothing.

    Raises:
        PyActionError: Naming the arguments that cannot be pickled.
    """
    try:
        return pickle.dumps(payload, protocol=5)
    except (pickle.PicklingError, TypeError, AttributeError) as exc:
        bad = _unpicklable(payload["kwargs"])
        label = (
            f"{'arguments' if len(bad) > 1 else 'argument'} {_and_list(bad)}"
            if bad
            else "one of its arguments"
        )
        raise PyActionError(
            f"PyAction {name}() cannot pickle {label}: {exc}. "
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
            pickle.dumps(value, protocol=5)
        except Exception:  # noqa: BLE001
            bad.append(key)
    return bad


def _write_if_changed(path: Path, content: bytes) -> None:
    """Write a generate-time file only when its bytes changed.

    Keeps the file's modification time stable across regenerations, so the
    build edges reading it do not re-run for a build description that says the
    same thing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)


def _runner_path() -> str:
    """The build-time runner's absolute path, as a command token.

    A path, never ``-m pcons.util.pyaction``: the ``-m`` form executes
    ``pcons/__init__.py`` first, importing the generators, toolchains and
    packages on every edge for several times the interpreter's own start-up,
    and ``pcons.workers.python_server.script_argv`` hands back any argv whose
    first argument starts with ``-``, which would make ``worker=`` a no-op.
    """
    return str(Path(runner.__file__)).replace("\\", "/")


def _as_list(value: object) -> list[Any]:
    """One target or several, as a list. Both call sites pass ``target=``."""
    from pcons.core.target import Target as TargetClass

    if isinstance(value, (str, Path, TargetClass)):
        return [value]
    return list(cast("Sequence[Any]", value))


def _derive_name(target: object) -> str:
    """The edge's name when the script gave none: the first target's stem."""
    first = target if isinstance(target, (str, Path)) else _as_list(target)[0]
    return Path(str(first)).stem


@dataclass(frozen=True)
class _HowToRun:
    """How the function runs, which every edge of one action shares.

    These describe the body rather than any one edge, so they sit on the
    decoration. What to build sits on the call, and no option sits on both.
    """

    python: str | None = None
    restat: bool = False
    write_if_different: bool = False
    cwd: str | Path | None = None
    launcher: Sequence[str] | None = None
    env_vars: Mapping[str, str] | None = None
    worker: Any = None

    def command_kwargs(self) -> dict[str, Any]:
        """The part of this that ``env.Command`` takes verbatim."""
        return {
            "restat": self.restat,
            "write_if_different": self.write_if_different,
            "cwd": self.cwd,
            "launcher": self.launcher,
            "env_vars": self.env_vars,
            "worker": self.worker,
        }


class PyAction:
    """A build-script function, ready to be turned into build edges.

    ``env.PyAction(...)`` returns the decorator that makes one of these, and
    calling it makes an edge, the way calling ``env.Program`` does::

        @env.PyAction()
        def report(sources, targets, title):
            from pathlib import Path

            Path(targets[0]).write_text(title)

        counts = report(target="counts.txt", source=[a, b], title="counts")
        more = report(target="more.txt", source=[c], title="more")

    One decoration is one generated module however many edges read it, and
    each call writes its own argument pickle. The module belongs to the
    :class:`ValidatedAction` inside, which is what claims its path, so this
    object is never itself in the claim registry.
    """

    __slots__ = ("_action", "_env", "_how", "_project")

    def __init__(
        self, action: ValidatedAction, env: Environment, how: _HowToRun
    ) -> None:
        self._action = action
        self._env = env
        self._how = how
        self._project = env._project

    def __repr__(self) -> str:
        return f"<PyAction {self._action.function.__name__}>"

    @property
    def function(self) -> types.FunctionType:
        """The function the build script wrote."""
        return self._action.function

    def __call__(
        self,
        *,
        target: str | Path | list[str | Path],
        source: Target | str | Path | Sequence[Target | str | Path] | None = None,
        name: str | None = None,
        depends: str | Path | Sequence[str | Path] | None = None,
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
            target: Output file or files, as ``env.Command`` takes them.
            source: Input files, or None. They arrive as the function's
                *sources*, in the order written.
            name: Edge name for ``ninja <name>``, and the argument pickle's
                file name. Defaults to the first target's stem.
            depends: Extra rebuild triggers that are not sources.
            **kwargs: The function's own arguments. Each must be picklable,
                and together they must fit its signature.

        Returns:
            The edge's ``Target``.

        Raises:
            PyActionError: If the arguments do not fit the function, if one
                of them holds a piece of the build description or cannot be
                pickled, or if the generated module collides with another
                action's.
        """
        edge_name = name or _derive_name(target)
        payload = check_arguments(self._action, kwargs=kwargs)
        module_rel = emit_module(self._action, project=self._project, env=self._env)
        args_rel = emit_args(
            project=self._project, env=self._env, name=edge_name, payload=payload
        )
        interpreter = (self._how.python or sys.executable).replace("\\", "/")
        return self._env.Command(
            target=target,
            source=source,
            name=edge_name,
            command=[
                interpreter,
                _runner_path(),
                self._project.node(module_rel),
                self._project.node(args_rel),
                "--n-targets",
                str(len(_as_list(target))),
                "$TARGETS",
                "$SOURCES",
            ],
            depends=depends,
            **self._how.command_kwargs(),
        )


def py_action(
    env: Environment,
    *,
    python: str | None = None,
    restat: bool = False,
    write_if_different: bool = False,
    cwd: str | Path | None = None,
    launcher: Sequence[str] | None = None,
    env_vars: Mapping[str, str] | None = None,
    worker: Any = None,
) -> Callable[[Callable[..., object]], PyAction]:
    """The decorator ``Environment.PyAction`` returns.

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

    Returns:
        A decorator that returns the ``PyAction`` the build script calls.
    """
    how = _HowToRun(
        python=python,
        restat=restat,
        write_if_different=write_if_different,
        cwd=cwd,
        launcher=launcher,
        env_vars=env_vars,
        worker=worker,
    )

    def decorate(fn: Callable[..., object]) -> PyAction:
        return PyAction(validate(fn, project=env._project), env, how)

    return decorate
