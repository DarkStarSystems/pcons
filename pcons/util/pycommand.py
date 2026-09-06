# SPDX-License-Identifier: MIT
"""Build-time runner for ``env.PyCommand`` edges.

pcons writes the decorated function's source to a generated module and its
keyword arguments to a pickle beside it, then emits a build edge shaped like::

    python <pcons>/util/pycommand.py <module.py> <args.pkl> --n-targets N
        <target>... <source>...

The function name is not on the command line, it travels in the pickle, so the
generated module and its arguments have one source of truth and one file to
rewrite when either changes.

Nothing here imports pcons. This module runs once per build edge, and a build
must not depend on the tree that described it still being importable.

The edge names this file by path, never as ``-m pcons.util.pycommand``, and
that is what keeps the claim above true of the process as well as of the file.
The ``-m`` form executes ``pcons/__init__.py`` first, which drags in the
generators, toolchains and packages for about 54 ms per edge, and the
persistent Python worker hands back any argv whose first argument starts with
``-`` to be spawned fresh, so ``worker=`` would buy nothing.

The pickle is written by the build itself and read back by the build. It is not
a general entry point and must not be pointed at a file from anywhere else.
"""

from __future__ import annotations

import importlib.util
import pickle
import sys
from types import ModuleType
from typing import Any

PROTOCOL_VERSION = 1

USAGE = (
    "Usage: python pycommand.py <module.py> <args.pkl> "
    "--n-targets N <target>... <source>..."
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
        The payload mapping, with at least ``module``, ``function`` and
        ``kwargs``.

    Raises:
        ValueError: If the file is not a payload this runner understands.
    """
    with open(args_path, "rb") as handle:
        payload = pickle.load(handle)
    if not isinstance(payload, dict):
        raise _stale(
            args_path, f"holds a {type(payload).__name__}, not a pycommand payload"
        )
    version = payload.get("version")
    if version != PROTOCOL_VERSION:
        raise _stale(
            args_path,
            f"was written for pycommand protocol {version!r}, but this pcons "
            f"speaks {PROTOCOL_VERSION}",
        )
    missing = sorted({"module", "function", "kwargs"} - set(payload))
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
        raise ImportError(f"Cannot load a pycommand module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def run(
    module_path: str, args_path: str, targets: list[str], sources: list[str]
) -> None:
    """Load the generated module and call the recorded function.

    Any exception the function raises propagates untouched, so the traceback
    points at the generated file and the build tool sees a failure.

    Args:
        module_path: Path to the generated ``.py`` file.
        args_path: Path to the sidecar pickle.
        targets: Output paths, as the execution directory sees them.
        sources: Input paths, as the execution directory sees them.

    Raises:
        AttributeError: If the module holds no function of the recorded name.
    """
    payload = _load_payload(args_path)
    module = _load_module(module_path, payload["module"])
    function = getattr(module, payload["function"])
    function(sources, targets, **payload["kwargs"])


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments after the module name, defaulting to ``sys.argv[1:]``.

    Returns:
        Zero on success. A usage error returns 1 without running anything.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4 or args[2] != "--n-targets" or not args[3].isdecimal():
        print(USAGE, file=sys.stderr)
        return 1

    n_targets = int(args[3])
    paths = args[4:]
    if n_targets > len(paths):
        print(
            f"pcons pycommand: --n-targets {n_targets} but only "
            f"{len(paths)} paths were given",
            file=sys.stderr,
        )
        return 1

    run(args[0], args[1], paths[:n_targets], paths[n_targets:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
