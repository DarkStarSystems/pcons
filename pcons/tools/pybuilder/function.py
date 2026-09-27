# SPDX-License-Identifier: MIT
"""The decorated function: what it may be, and the module it becomes.

:func:`validate` reads it once, at decoration, and refuses any function the
generated module can't carry. :func:`emit_module` claims that module's path
and hands back its text.
"""

from __future__ import annotations

import ast
import dis
import functools
import inspect
import textwrap
import types
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.invocation import RUN_NAME
from pcons.tools.pybuilder.errors import PyBuilderError, _and_list, _describe
from pcons.tools.pybuilder.files import _claim, _gen_dir, _sanitized
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from collections.abc import Callable

    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.util.source_location import SourceLocation

_SAFE_GLOBALS = frozenset({"__name__", "__doc__", "__builtins__"})


@dataclass(frozen=True, eq=False)
class ValidatedFunction:
    """A function that can be carried to build time, and the module for it.

    What :func:`validate` settles depends on the function alone, so it is
    settled once even when many edges run the same function. Where
    :func:`emit_module` and :func:`emit_args` put their files depends on the
    environment and on the edge, so they run per call.

    ``eq=False`` on purpose: the claim registry tells one function's module
    from another's by identity, and two validations of one ``def`` inside a
    factory are two functions that a generated ``__eq__`` would call equal. Identity is
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
        PyBuilderError: If the source cannot be read, or is not a plain
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
        PyBuilderError: If the source cannot be read, or is not a plain
            ``def``.
    """
    try:
        text = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError) as exc:
        raise PyBuilderError(
            f"PyBuilder cannot read the source of {_describe(fn)}: {exc}. "
            f"The function must be written out in a build script.",
            at,
        ) from exc
    node = ast.parse(text).body[0]
    if not isinstance(node, ast.FunctionDef):
        raise PyBuilderError(
            f"PyBuilder needs a plain function: {_not_a_def(fn, node)}", at
        )
    return "".join(text.splitlines(keepends=True)[node.lineno - 1 :]), node


def validate(fn: Callable[..., object], *, project: Project) -> ValidatedFunction:
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
        PyBuilderError: If the function cannot be carried to build time.
    """
    at = get_caller_location()
    name = _decoration_name(fn)
    function = _plain_function(fn, name, at)
    _reject_uncallable_signature(function, at)
    _reject_reserved_parameters(function, at)
    source, node = _extract(function, at)
    _reject_script_globals(function, node, name, at)
    return ValidatedFunction(function, _module_text(source, project, at), at)


def _decoration_name(fn: Callable[..., object]) -> str:
    """How a message names what the decorator was handed.

    The edge has no name yet at decoration, and the function is what the
    reader is looking at.
    """
    return getattr(fn, "__name__", None) or type(fn).__name__


@functools.cache
def _reserved_names() -> frozenset[str]:
    """The parameter names :meth:`PyBuilder.__call__` spends on the edge.

    Read from that signature rather than listed beside it, so a name added to
    the call is reserved by the same edit and the two cannot disagree.
    ``self`` and the ``**kwargs`` that carry the function's own arguments are
    not keyword-only parameters, so they fall out on their own.
    """
    # The builder validates functions with this, so it can't be imported
    # when this module is; by the first decoration it has been.
    from pcons.tools.pybuilder.builder import PyBuilder

    return frozenset(
        name
        for name, parameter in inspect.signature(PyBuilder.__call__).parameters.items()
        if parameter.kind is parameter.KEYWORD_ONLY
    )


def _reject_reserved_parameters(
    function: types.FunctionType, at: SourceLocation
) -> None:
    """Refuse a parameter whose name the call already spends on the edge.

    Raises:
        PyBuilderError: Naming the parameters to rename.
    """
    taken = sorted(set(inspect.signature(function).parameters) & _reserved_names())
    if not taken:
        return
    plural = len(taken) > 1
    raise PyBuilderError(
        f"PyBuilder {function.__name__}() has {_and_list(taken)} as "
        f"{'parameter names' if plural else 'a parameter name'}, and the call "
        f"spends {'those names' if plural else 'that name'} on the edge "
        f"itself. Rename {'them' if plural else 'it'} in the def and at the "
        f"call: the function receives targets and sources as its first two "
        f"arguments, and everything else as a keyword of the call.",
        at,
    )


def _reject_uncallable_signature(
    function: types.FunctionType, at: SourceLocation
) -> None:
    """Refuse a signature the build-time call could never satisfy.

    The runner calls ``fn(targets, sources, **kwargs)``. Two shapes make that
    impossible however the call is written, and both are properties of the
    ``def``, so they are refused where the ``def`` is rather than at the first
    call. Left to ``inspect``, each is reported in the words of the probe
    rather than of the mistake: the first as "too many positional arguments",
    the second as a missing "positional-only" argument to somebody who just
    typed that keyword.

    Raises:
        PyBuilderError: Naming which half of the call shape is impossible.
    """
    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())
    kinds = {p.kind for p in parameters}
    slots = [
        p for p in parameters if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(slots) < 2 and inspect.Parameter.VAR_POSITIONAL not in kinds:
        raise PyBuilderError(
            f"PyBuilder {function.__name__}{signature} cannot receive targets "
            f"and sources: it has "
            f"{'only one parameter' if len(slots) == 1 else 'no parameters'} "
            f"that can be filled positionally. At build time it is called as "
            f"{function.__name__}(targets, sources, **kwargs), so write the "
            f"first two as plain parameters: "
            f"def {function.__name__}(targets, sources, ...).",
            at,
        )
    late = [p.name for p in parameters[2:] if p.kind is p.POSITIONAL_ONLY]
    if late:
        plural = len(late) > 1
        raise PyBuilderError(
            f"PyBuilder {function.__name__}{signature} cannot be given "
            f"{_and_list(late)}: {'they are' if plural else 'it is'} "
            f"positional-only, and everything past targets and sources "
            f"arrives as a keyword of the call. Move the / up so it follows "
            f"sources: def {function.__name__}(targets, sources, /, "
            f"{', '.join(late)}).",
            at,
        )


def emit_module(
    function: ValidatedFunction, *, project: Project, env: Environment
) -> tuple[Path, bytes | None]:
    """Claim the generated module's path for *env*, once per path it lands on.

    The same function reaching one path again is one file two edges read, so
    the second claim succeeds and has nothing to write. A different function
    on that path is two functions of one name, which is an error.

    Args:
        function: What :func:`validate` returned.
        project: Any project of the tree; the claim registry hangs off its top.
        env: The environment whose build directory holds the module.

    Returns:
        The module's path, relative to the build directory and anchored the
        way a node path is, and the bytes to write there, or None when this
        function already claimed that path. The path is neither a disk path
        nor what ``env.Command`` takes as a source: write to ``root / path``,
        and hand the builder ``project._node(path)``, or a subdirectory's
        offset is applied twice.

    Raises:
        PyBuilderError: If another builder already claimed that file.
    """
    module_rel = _gen_dir(env) / f"{function.module_stem}.py"
    claimed = _claim(
        project,
        env,
        module_rel,
        function.function.__name__,
        function.at,
        owner=function,
    )
    if not claimed:
        return module_rel, None
    return module_rel, function.module_text.encode("utf-8")


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
        PyBuilderError: With one message per rejected shape.
    """
    if isinstance(fn, functools.partial):
        raise PyBuilderError(
            "PyBuilder was given a functools.partial. Pass the function itself "
            "and give its bound arguments to the call: "
            "builder(target=..., bound=value).",
            at,
        )
    if not isinstance(fn, types.FunctionType):
        raise PyBuilderError(
            f"PyBuilder needs a function written in a build script, not "
            f"{_describe(fn)} of type {type(fn).__name__}. Write a def beside "
            f"the other targets and pass what it needs at the call: "
            f"builder(target=..., value=...).",
            at,
        )
    if fn.__name__ == "<lambda>":
        raise PyBuilderError(
            "PyBuilder was given a lambda. Its source cannot be extracted on "
            "its own: write it as a def.",
            at,
        )
    if fn.__closure__ is not None:
        free = sorted(fn.__code__.co_freevars)
        plural = len(free) > 1
        raise PyBuilderError(
            f"PyBuilder {name}() reads {_and_list(free)} from the function it "
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
        raise PyBuilderError(
            f"PyBuilder was given {_describe(fn)}, defined in a class body. "
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
        PyBuilderError: Naming those globals and what to type instead.
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
        raise PyBuilderError(
            f"PyBuilder {name}() uses __file__, which at build time names the "
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
    raise PyBuilderError(
        f"PyBuilder {name}() uses {_and_list(suspect)} from the build script, "
        f"and only the function's own source travels to build time, so "
        f"nothing defines {those} there. " + " ".join(remedies),
        at,
    )


def _origin(project: Project, at: SourceLocation) -> str:
    """Which script the generated module came from.

    The file only, never the line: a line number would change whenever a line
    is inserted above the decoration, rewriting a module whose function did
    not change and re-running the edge that reads it, which is the one thing
    writing only changed bytes exists to avoid. Relative to the project root
    where possible and the bare file name otherwise, so the generated bytes say
    the same thing in every checkout.
    """
    path = Path(at.filename)
    try:
        return path.relative_to(project.top_path_resolver.project_root).as_posix()
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
