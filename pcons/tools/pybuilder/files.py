# SPDX-License-Identifier: MIT
"""The files a PyBuilder edge reads: their paths, and who claimed each.

The generated module and each edge's argument pickle go in the
environment's ``pybuilder`` directory, and the runner is copied once per
build directory. Two builders that would write one file are refused here.
"""

from __future__ import annotations

import functools
import re
import weakref
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder import anchor_target_paths
from pcons.tools.pybuilder.errors import PyBuilderError
from pcons.util import pybuilder as runner

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.tools.pybuilder.function import ValidatedFunction
    from pcons.util.source_location import SourceLocation

GEN_DIR = "pybuilder"
MODULE_PREFIX = "pcons_pybuilder_"
RUNNER_DIR = "pcons-runner"
RUNNER_NAME = "pcons-runner.py"

_claimed: weakref.WeakKeyDictionary[
    Project, dict[Path, tuple[SourceLocation, Environment, ValidatedFunction | None]]
] = weakref.WeakKeyDictionary()


def emit_args(
    *,
    project: Project,
    env: Environment,
    label: str,
    first_output: Path,
    at: SourceLocation,
) -> Path:
    """Claim the path of one edge's argument pickle, once the edge has outputs.

    One edge is one pickle, so this path is exclusive: nothing may share it,
    not even the function that claimed the module beside it. It's named
    after the edge's first output, which an emitter may only decide at
    resolve, so this runs then, inside the edge's resolution.

    Args:
        project: Any project of the tree; the claim registry hangs off its top.
        env: The environment whose build directory holds the pickle.
        label: The edge's label, used in a collision message and as the
            fallback file stem when the output's own path cannot be used.
        first_output: The node path of the edge's first output.
        at: The call that made the edge, blamed for a collision.

    Returns:
        The pickle's path, anchored the way :func:`emit_module` returns one.

    Raises:
        PyBuilderError: If another edge already claimed that file.
    """
    relpath = _pickle_relpath(env, first_output, label)
    args_rel = _gen_dir(env) / f"{relpath.as_posix()}.args.pkl"
    _claim(project, env, args_rel, label, at, owner=None)
    return args_rel


def _gen_dir(env: Environment) -> Path:
    """Where both generated files go, anchored the way a node path is."""
    return anchor_target_paths(env, [Path(GEN_DIR)])[0]


def _pickle_relpath(env: Environment, first_output: Path, label: str) -> Path:
    """The pickle's path relative to the gen dir, from the first output.

    The output's own node path, so two named environments sharing one build
    directory land on different pickles even when their targets share a
    name, and a target in a subdirectory keeps it, ``out/report.txt``
    landing at ``pybuilder/out/report.txt.args.pkl``.

    Falls back to the sanitized edge label when the output cannot be
    expressed relative to the gen dir's parent: an absolute target outside
    the environment's own build directory, or one that climbs out of it with
    ``..``. Both are rare and already unusual targets; the fallback keeps
    the pickle inside the gen dir rather than reasoning further about where
    it should land.
    """
    try:
        relative = first_output.relative_to(_gen_dir(env).parent)
    except ValueError:
        relative = None
    if relative is None or ".." in relative.parts:
        return Path(_sanitized(label))
    return relative


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
    owner: ValidatedFunction | None,
) -> bool:
    """Record that *path* is taken, and say whether the caller should write.

    The registry hangs off the top-level project rather than off this module,
    so a second project in the same process starts clean.

    *owner* is what may legally reach one path twice. A module's owner is the
    :class:`ValidatedFunction` behind it, so one builder emitting into two
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
        PyBuilderError: If somebody else already claimed that file.
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
    subject = f"PyBuilder {name}()" if owner is not None else f"PyBuilder edge {name!r}"
    wrote = "the PyBuilder" if owner is not None else "the edge"
    raise PyBuilderError(
        f"{subject}{_env_label(env)} would overwrite {path.as_posix()}, "
        f"already written by {wrote}{_env_label(first_env)} at {first_at}. "
        f"{advice}",
        at,
    )


def _collision_advice(
    owner: ValidatedFunction | None,
    first: ValidatedFunction | None,
    *,
    same_env: bool,
) -> str:
    """What to do about two claims on one path, in the words that apply.

    A pickle is named after the edge's first target, so different targets
    part them. A module belongs to one function: when two environments share
    a build directory, only a build_prefix parts them, and when one
    environment claims twice, the answer turns on whether it is one function
    or two. The module text is what tells those apart. A factory that writes
    the ``def`` inside itself makes a fresh function object every call, so
    comparing the objects would report a name clash where there is one
    function and no clash at all.
    """
    fixes: list[str] = []
    if not same_env:
        fixes.append("give one environment its own build_prefix")
    if owner is None or first is None:
        fixes.append("give the edges different targets")
    elif owner.module_text != first.module_text:
        fixes.append("rename one of the functions")
    elif same_env:
        fixes.append("decorate the function once and call the builder twice")
    sentence = ", or ".join(fixes)
    return f"{sentence[:1].upper()}{sentence[1:]}."


def _runner_rel(project: Project) -> Path:
    """Where the runner's copy goes, anchored the way a node path is.

    One copy per build directory, whatever environment or subdirectory the
    edge belongs to, because every edge of one build runs the same runner.

    The copy has a directory to itself. Running it puts that directory first
    on ``sys.path``, and a generated module is named after its function, so
    a function called ``pickle`` beside the runner would shadow the standard
    module for the runner and for every body's imports. A generated module
    lands directly in its environment's generated directory, never in a
    subdirectory of it, and ``pcons-runner`` is not a module stem.

    A path, never ``-m pcons.util.pybuilder``: the ``-m`` form executes
    ``pcons/__init__.py`` first, importing the generators, toolchains and
    packages on every edge for several times the interpreter's own start-up,
    and ``pcons.workers.python_server.script_argv`` hands back any argv whose
    first argument starts with ``-``, which would make ``worker=`` a no-op.
    """
    return project.top_path_resolver.build_dir / GEN_DIR / RUNNER_DIR / RUNNER_NAME


@functools.cache
def _runner_bytes() -> bytes:
    """The runner's source, which the build directory gets a copy of."""
    return Path(runner.__file__).read_bytes()
