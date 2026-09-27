# SPDX-License-Identifier: MIT
"""Build-time runner for ``env.PyBuilder`` edges.

pcons writes the decorated function's source to a generated module and its
keyword arguments to a pickle beside it, along with the ``sys.path`` the
build script had when it decorated the function, copies this file to
``pybuilder/pcons-runner/pcons-runner.py`` in the build directory, then emits a
build edge shaped like::

    python pybuilder/pcons-runner/pcons-runner.py <module.py> <args.pkl>
        [--depfile <path>] --n-targets N <target>... <source>...

``--depfile`` is there for an edge whose builder was declared
``discovers=True``: the function returns the files it read, and this writes
them as a make-style depfile, which the build tool reads as ``deps = gcc``.

The function name is not on the command line, it travels in the pickle, so the
generated module and its arguments have one source of truth and one file to
rewrite when either changes.

Nothing here imports pcons. This module runs once per build edge, and a build
must not depend on the tree that described it still being importable.

The edge names this file by path, never as ``-m pcons.util.pybuilder``, and
that is what keeps the claim above true of the process as well as of the file.
The ``-m`` form executes ``pcons/__init__.py`` first, which drags in the
generators, toolchains and packages on every edge and costs several times the
interpreter's own start-up, and the persistent Python worker hands back any
argv whose first argument starts with ``-`` to be spawned fresh, so
``worker=`` would buy nothing.

The pickle is written by the build itself and read back by the build. It is not
a general entry point and must not be pointed at a file from anywhere else.
"""

from __future__ import annotations

import importlib.util
import os
import pickle
import sys
from types import ModuleType
from typing import Any

PROTOCOL_VERSION = 1

USAGE = (
    "Usage: python pcons-runner.py <module.py> <args.pkl> "
    "[--depfile <path>] --n-targets N <target>... <source>..."
)


def _stale(args_path: str, detail: str) -> ValueError:
    """The one answer to every unusable payload: regenerate it.

    Args:
        args_path: The payload file's path.
        detail: What is wrong with it.

    Returns:
        The error to raise.
    """
    return ValueError(
        f"{args_path} {detail}. Re-run pcons to regenerate the build files."
    )


def _load_payload(args_path: str) -> dict[str, Any]:
    """Read the sidecar pickle holding the function name and its arguments.

    Args:
        args_path: Path to the ``.args.pkl`` file pcons generated.

    Returns:
        The payload mapping, with at least ``module``, ``function``,
        ``kwargs`` and ``path``.

    Raises:
        ValueError: If the file is not a payload this runner understands.
    """
    with open(args_path, "rb") as handle:
        payload = pickle.load(handle)
    if not isinstance(payload, dict):
        raise _stale(
            args_path, f"holds a {type(payload).__name__}, not a pybuilder payload"
        )
    version = payload.get("version")
    if version != PROTOCOL_VERSION:
        raise _stale(
            args_path,
            f"was written for pybuilder protocol {version!r}, but this pcons "
            f"speaks {PROTOCOL_VERSION}",
        )
    missing = sorted({"module", "function", "kwargs", "path"} - set(payload))
    if missing:
        raise _stale(args_path, f"is missing {', '.join(missing)}")
    return payload


def _load_module(module_path: str, name: str) -> ModuleType:
    """Import the generated module from its path, under *name*.

    Registered under *name* before it runs, which is what lets the body define
    a class the function then pickles, and unregistered again if it fails to
    run, so a persistent worker is never left holding a half-built module.

    Args:
        module_path: Path to the generated ``.py`` file.
        name: Module name to register it under.

    Returns:
        The executed module.

    Raises:
        ImportError: If Python has no loader for that path.
    """
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load a pybuilder module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def run(
    module_path: str,
    args_path: str,
    targets: list[str],
    sources: list[str],
    depfile: str | None = None,
) -> None:
    """Load the generated module and call the recorded function.

    Before loading, ``sys.path`` is replaced wholesale with the payload's
    ``path``, when it is not None, so the function imports what its build
    script could import, and nothing that only happens to be on this
    process's own path.

    Any exception the function raises propagates untouched, so the traceback
    points at the generated file and the build tool sees a failure.

    Args:
        module_path: Path to the generated ``.py`` file.
        args_path: Path to the sidecar pickle.
        targets: Output paths, as the execution directory sees them.
        sources: Input paths, as the execution directory sees them.
        depfile: Where to write the files the function says it read, for an
            edge whose builder declared ``discovers=True``. None for every
            other edge, where a return value is an error.

    Raises:
        AttributeError: If the module holds no function of the recorded name.
        TypeError: If the function returns something this edge can't use.
        ValueError: If it returns a key this protocol doesn't define.
    """
    payload = _load_payload(args_path)
    if payload["path"] is not None:
        sys.path[:] = payload["path"]
    module = _load_module(module_path, payload["module"])
    function = getattr(module, payload["function"])
    result = function(targets, sources, **payload["kwargs"])
    if depfile is not None:
        inputs = discovered_inputs(result, payload["function"])
        write_depfile(depfile, targets[0], inputs)
    elif result is not None:
        raise TypeError(
            f"{payload['function']}() returned {type(result).__name__}. A "
            f"PyBuilder function returns None, unless its builder is "
            f'declared discovers=True: then it may return {{"inputs": '
            f"[...]}}, the files it read."
        )


def discovered_inputs(result: object, function: str) -> list[str]:
    """The files a discovering function says it read.

    A dict rather than a bare list, so the protocol can grow a key without
    the first one changing meaning. An unknown key is refused rather than
    ignored: a mistyped one would otherwise drop every dependency it was
    meant to declare, and the build would be wrong only sometimes.

    Args:
        result: What the function returned. None means it read nothing more.
        function: Its name, for the messages.

    Returns:
        The paths, as text.

    Raises:
        TypeError: If the return value isn't a dict, or ``inputs`` isn't a
            list of paths.
        ValueError: If the dict has a key this protocol doesn't define.
    """
    if result is None:
        return []
    if not isinstance(result, dict):
        raise TypeError(
            f"{function}() returned {type(result).__name__}. A PyBuilder "
            f'that discovers its inputs returns {{"inputs": [...]}}, the '
            f"files it read, or None."
        )
    unknown = sorted(str(key) for key in result if key != "inputs")
    if unknown:
        raise ValueError(
            f"{function}() returned {', '.join(unknown)}, which a PyBuilder's "
            f'return value doesn\'t define. Only "inputs" is, the list of '
            f"files the function read."
        )
    inputs = result.get("inputs", [])
    if isinstance(inputs, (str, bytes)) or not isinstance(inputs, (list, tuple)):
        raise TypeError(
            f'{function}() returned inputs={inputs!r}. "inputs" is a list of '
            f"the files the function read, one path each."
        )
    return [os.fspath(item) for item in inputs]


def write_depfile(depfile: str, target: str, inputs: list[str]) -> None:
    """Write the make-style depfile a build tool reads as ``deps = gcc``.

    One line, the edge's target and then what it read. Each path has its
    separators turned forward and the characters a depfile gives meaning to
    escaped, and the colon after the target is followed by a space, so a
    Windows drive letter is never read as the rule's separator. A relative
    path is read from the build directory, where the function ran.

    Written even when nothing was discovered: the edge declares a depfile,
    so the build tool expects one.

    Args:
        depfile: Where to write it.
        target: The edge's output, which names the rule.
        inputs: The files the function read.
    """
    line = " ".join([_escaped(target) + ":", *map(_escaped, inputs)])
    os.makedirs(os.path.dirname(depfile) or ".", exist_ok=True)
    with open(depfile, "w", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _escaped(path: str) -> str:
    """One path as a make-style depfile writes it."""
    return (
        path.replace("\\", "/")
        .replace("$", "$$")
        .replace("#", "\\#")
        .replace(" ", "\\ ")
    )


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments after the module name, defaulting to ``sys.argv[1:]``.

    Returns:
        Zero on success. A usage error returns 1 without running anything.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    depfile = None
    if len(args) >= 4 and args[2] == "--depfile":
        depfile = args[3]
        del args[2:4]
    if len(args) < 4 or args[2] != "--n-targets" or not args[3].isdecimal():
        print(USAGE, file=sys.stderr)
        return 1

    n_targets = int(args[3])
    paths = args[4:]
    if n_targets > len(paths):
        print(
            f"pcons pybuilder: --n-targets {n_targets} but only "
            f"{len(paths)} paths were given",
            file=sys.stderr,
        )
        return 1

    run(args[0], args[1], paths[:n_targets], paths[n_targets:], depfile=depfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
