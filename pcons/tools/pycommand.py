# SPDX-License-Identifier: MIT
"""The whole of ``env.PyCommand`` except its public name.

``Environment.PyCommand`` is a forwarder into :func:`py_command` here, the
way ``Project.cli_command`` forwards into ``pcons.commands``. Source
extraction and emission are the other half.

A function typed in a build script cannot be pickled. The script runs under
``__name__ == "__pcons__"`` and that module is never importable, so pickle,
which stores a function by module and qualname, has nothing to store. The body
reaches build time as a real file instead: :func:`emit` writes the function's
own source to a generated module and its keyword arguments to a sidecar
pickle, both beside the environment's build directory, and hands back the two
node-canonical paths the build edge names.

The generated module holds the function and nothing else, so a body that uses
a name the build script imported would fail at build time with ``NameError``.
That is caught here instead, along with every other function shape this design
cannot carry, each with its own :class:`PyCommandError`.
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
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pcons.core.builder import anchor_target_paths
from pcons.core.errors import PconsError
from pcons.core.invocation import RUN_NAME
from pcons.util import pycommand as runner
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation

GEN_DIR = "pycmd"
MODULE_PREFIX = "pcons_pycmd_"

_SAFE_GLOBALS = frozenset({"__name__", "__doc__", "__builtins__"})


class PyCommandError(PconsError):
    """A function cannot be turned into a build edge."""


_claimed: weakref.WeakKeyDictionary[
    Project, dict[Path, tuple[SourceLocation, Environment]]
] = weakref.WeakKeyDictionary()


def function_source(fn: Callable[..., object]) -> str:
    """The ``def`` of *fn* alone, with the decorators above it removed.

    Args:
        fn: The function a build script defined.

    Returns:
        The function's source, dedented, ending in a newline.

    Raises:
        PyCommandError: If the source cannot be read, or is not a plain
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
        PyCommandError: If the source cannot be read, or is not a plain
            ``def``.
    """
    try:
        text = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError) as exc:
        raise PyCommandError(
            f"PyCommand cannot read the source of {_describe(fn)}: {exc}. "
            f"The function must be written out in a build script.",
            at,
        ) from exc
    node = ast.parse(text).body[0]
    if not isinstance(node, ast.FunctionDef):
        raise PyCommandError(
            f"PyCommand needs a plain function: {_not_a_def(fn, node)}", at
        )
    return "".join(text.splitlines(keepends=True)[node.lineno - 1 :]), node


def emit(
    fn: Callable[..., object],
    *,
    project: Project,
    env: Environment,
    name: str,
    kwargs: Mapping[str, Any],
) -> tuple[Path, Path]:
    """Write the generated module and its argument pickle.

    Both files are written only when their bytes change, so an unchanged build
    description leaves their modification times alone and the edges that read
    them do not re-run.

    Args:
        fn: The function to run at build time.
        project: The project the edge belongs to, any project of the tree.
        env: The environment whose build directory holds the two files.
        name: The edge's name, used for both file names.
        kwargs: Keyword arguments to pickle for the build-time call.

    Returns:
        The module path and the pickle path, relative to the build directory
        and anchored the way a node path is. Neither a disk path nor what
        ``env.Command`` takes as a source: write to ``root / path``, and hand
        the builder ``project.node(path)``, or a subdirectory's offset is
        applied to them a second time.

    Raises:
        PyCommandError: If the function cannot be carried to build time, if
            another edge already wrote the same module, or if an argument
            cannot be pickled.
    """
    at = get_caller_location()
    function = _plain_function(fn, name, at)
    source, node = _extract(function, at)
    _reject_script_globals(function, node, name, at)
    _reject_description_objects(kwargs, name, at)
    text = _module_text(source, project, at)

    stem = _sanitized(name)
    root = project._path_resolver.project_root
    gen_dir = anchor_target_paths(env, [Path(GEN_DIR)])[0]
    module_rel = gen_dir / f"{stem}.py"
    args_rel = gen_dir / f"{stem}.args.pkl"

    _claim(project, env, module_rel, name, at)
    _write_if_changed(root / module_rel, text.encode("utf-8"))
    _write_if_changed(
        root / args_rel,
        _payload_bytes(
            {
                "version": runner.PROTOCOL_VERSION,
                "module": f"{MODULE_PREFIX}{stem}",
                "function": function.__name__,
                "kwargs": dict(kwargs),
            },
            name,
            at,
        ),
    )
    return module_rel, args_rel


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
        PyCommandError: With one message per rejected shape.
    """
    if isinstance(fn, functools.partial):
        raise PyCommandError(
            f"PyCommand {name!r} was given a functools.partial. Pass the "
            f"function itself and put its bound arguments in kwargs=.",
            at,
        )
    if not isinstance(fn, types.FunctionType):
        raise PyCommandError(
            f"PyCommand {name!r} needs a function written in a build script, "
            f"not {_describe(fn)} of type {type(fn).__name__}. Write a def "
            f"beside the other targets and pass what it needs in kwargs=.",
            at,
        )
    if fn.__name__ == "<lambda>":
        raise PyCommandError(
            f"PyCommand {name!r} was given a lambda. Its source cannot be "
            f"extracted on its own: write it as a def.",
            at,
        )
    if fn.__closure__ is not None:
        free = fn.__code__.co_freevars
        raise PyCommandError(
            f"PyCommand {name!r} reads {', '.join(free)} from the function it "
            f"is nested in. Only the function's own source travels to build "
            f"time, so there is nothing to read "
            f"{'them' if len(free) > 1 else 'it'} from. Pass "
            f"{'them' if len(free) > 1 else 'it'} in kwargs= and take "
            f"{'them' if len(free) > 1 else 'it'} as "
            f"{'arguments' if len(free) > 1 else 'an argument'}.",
            at,
        )
    if _class_scoped(fn):
        raise PyCommandError(
            f"PyCommand {name!r} was given {_describe(fn)}, defined in a "
            f"class body. Only a plain function can be extracted: move the "
            f"def out of the class.",
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
    found: str, value: object, from_default: bool, imports: list[str]
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
        return (
            f"{found} is a parameter's default value, and a default is "
            f"evaluated again where the generated module defines the "
            f"function: write the parameter without a default and pass "
            f"{found} in kwargs=."
        )
    if isinstance(value, types.ModuleType):
        imports.append(f"import {value.__name__}")
        return None
    if callable(value) or isinstance(value, type):
        return (
            f"Import {found} inside the function body, the way this script imports it."
        )
    return f"Pass {found} in kwargs= and take it as an argument."


def _reject_script_globals(
    fn: types.FunctionType, node: ast.FunctionDef, name: str, at: SourceLocation
) -> None:
    """Refuse a body that reads a name only the build script defines.

    A name that is not an identifier is not one a build script could have
    written: pytest rewrites the asserts of a test module and its injected
    ``@pytest_ar`` would otherwise be reported as a global of the body.

    Raises:
        PyCommandError: Naming those globals and what to type instead.
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
        raise PyCommandError(
            f"PyCommand {name!r} uses __file__, which at build time names the "
            f"generated module rather than this script. Pass the path it "
            f'means in kwargs=, project.root_dir / "...", and take it as an '
            f"argument.",
            at,
        )

    imports: list[str] = []
    remedies = [
        remedy
        for found in suspect
        if (
            remedy := _global_remedy(
                found, fn.__globals__[found], found in defaults, imports
            )
        )
        is not None
    ]
    if imports:
        written = ", ".join(f'"{line}"' for line in dict.fromkeys(imports))
        remedies.insert(
            0, f"Write {written} at the top of the function body, not of the script."
        )
    those = "those names" if len(suspect) > 1 else "that name"
    raise PyCommandError(
        f"PyCommand {name!r} uses {', '.join(suspect)} from the build script, "
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
    project: Project, env: Environment, module_rel: Path, name: str, at: SourceLocation
) -> None:
    """Record that *module_rel* is taken, refusing a second claim on it.

    The registry hangs off the top-level project rather than off this module,
    so a second project in the same process starts clean.

    Two environments decorating one function through a factory land on the
    same source line, so the environment is what tells the two claims apart,
    and giving one of them a ``build_prefix`` is the fix the factory shape
    calls for.

    Raises:
        PyCommandError: If another PyCommand already wrote that file.
    """
    taken = _claimed.setdefault(project.top, {})
    first = taken.get(module_rel)
    if first is not None:
        first_at, first_env = first
        advice = (
            "Give one environment its own build_prefix, or pass name= to one of them."
            if first_env is not env
            else "Pass name= to one of them."
        )
        raise PyCommandError(
            f"PyCommand {name!r}{_env_label(env)} would overwrite "
            f"{module_rel.as_posix()}, already written by the PyCommand"
            f"{_env_label(first_env)} at {first_at}. {advice}",
            at,
        )
    taken[module_rel] = (at, env)


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
        PyCommandError: Naming where it sits and what to write instead.
    """
    seen: set[int] = set()

    def walk(value: object, where: str) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        described = _describe_description_object(value)
        if described is not None:
            where_it_is, remedy = described
            raise PyCommandError(
                f"PyCommand {name!r}: {where} is {where_it_is}, and the build "
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
        walk(value, f"kwargs[{key!r}]")


def _payload_bytes(payload: dict[str, Any], name: str, at: SourceLocation) -> bytes:
    """The sidecar pickle's bytes, at a fixed protocol.

    The protocol is pinned so an interpreter upgrade does not rewrite every
    sidecar and rebuild the world once for nothing.

    Raises:
        PyCommandError: Naming the arguments that cannot be pickled.
    """
    try:
        return pickle.dumps(payload, protocol=5)
    except (pickle.PicklingError, TypeError, AttributeError) as exc:
        bad = ", ".join(_unpicklable(payload["kwargs"])) or "one of its values"
        raise PyCommandError(
            f"PyCommand {name!r} cannot pickle kwargs {bad}: {exc}. "
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

    A path, never ``-m pcons.util.pycommand``: the ``-m`` form executes
    ``pcons/__init__.py`` first, about 54 ms of imports on every edge, and
    ``pcons.workers.python_server.script_argv`` hands back any argv whose
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


def py_command(
    env: Environment,
    *,
    target: str | Path | list[str | Path],
    source: Target | str | Path | Sequence[Target | str | Path] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    name: str | None = None,
    depends: str | Path | Sequence[str | Path] | None = None,
    python: str | None = None,
    restat: bool = False,
    write_if_different: bool = False,
    cwd: str | Path | None = None,
    launcher: Sequence[str] | None = None,
    env_vars: Mapping[str, str] | None = None,
    worker: Any = None,
) -> Callable[[Callable[..., object]], Target]:
    """The decorator ``Environment.PyCommand`` returns.

    The generated module and the argument pickle are node tokens of the
    command, which is what makes the generator spell them as the execution
    directory sees them and makes the edge rebuild when either changes. A
    node token does not join ``$SOURCES``, so the user's own sources keep
    index 0 and are all the runner passes on to the function.

    Args:
        env: The environment the edge builds in.
        target: Output file or files, as ``env.Command`` takes them.
        source: Input files, or None.
        kwargs: Keyword arguments for the build-time call.
        name: Edge name, defaulting to the first target's stem.
        depends: Extra rebuild triggers that are not sources.
        python: Interpreter to run, defaulting to the one running pcons.
        restat: See ``env.Command``.
        write_if_different: See ``env.Command``.
        cwd: See ``env.Command``.
        launcher: See ``env.Command``.
        env_vars: See ``env.Command``.
        worker: See ``env.Command``.

    Returns:
        A decorator that emits the function and returns the edge's Target.
    """
    project = env._project

    def decorate(fn: Callable[..., object]) -> Target:
        derived = name or _derive_name(target)
        module_rel, args_rel = emit(
            fn, project=project, env=env, name=derived, kwargs=kwargs or {}
        )
        interpreter = (python or sys.executable).replace("\\", "/")
        return env.Command(
            target=target,
            source=source,
            name=derived,
            command=[
                interpreter,
                _runner_path(),
                project.node(module_rel),
                project.node(args_rel),
                "--n-targets",
                str(len(_as_list(target))),
                "$TARGETS",
                "$SOURCES",
            ],
            depends=depends,
            restat=restat,
            write_if_different=write_if_different,
            cwd=cwd,
            launcher=launcher,
            env_vars=env_vars,
            worker=worker,
        )

    return decorate
