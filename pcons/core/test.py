# SPDX-License-Identifier: MIT
"""Test specification and manifest writer.

Tests are declared in build scripts via ``project.Test(...)`` and serialized
to ``<build_dir>/tests.json`` at configure time. A separate runner (the
``pcons test`` subcommand) reads the manifest and executes the tests.

This split mirrors CMake's build/ctest separation: the build system is
responsible only for *describing* tests, never for running them.

The JSON schema is versioned. Adding new fields is backward-compatible
because the runner ignores keys it does not understand; bumping the
``version`` is reserved for incompatible changes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target

logger = logging.getLogger(__name__)


MANIFEST_VERSION = 1
MANIFEST_FILENAME = "tests.json"


@dataclass(frozen=True)
class TestSpec:
    """A serializable description of one test.

    The ``command`` list is fully expanded at configure time: the first
    element is the program (already resolved to its build-dir-relative
    path if it came from a Target) and the rest are arguments.

    ``cwd`` is either None (= run from the build directory) or an
    absolute path; the runner passes it through to ``subprocess.run``
    unchanged.

    Attributes:
        name: User-supplied test name (need not be unique with other targets).
        command: Program + args. First element is the program path.
        cwd: Working directory for the test, or None for the build dir.
        env: Additional environment variables for the test process.
        labels: Tags for filtering (e.g., "unit", "fuzz", "slow").
        timeout: Seconds before the runner kills the test, or None.
        should_fail: If True, a non-zero exit code is a pass.
        serial: If True, runner won't parallelize this test with others.
        disabled: If True, runner reports the test as skipped.
        data: Extra files the test reads at runtime (informational only).
        defined_at: Source location string for diagnostics.
    """

    # Tell pytest to skip collection — the class name starts with "Test"
    # which would otherwise trip its auto-discovery heuristic.
    __test__ = False

    name: str
    command: list[str]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    labels: tuple[str, ...] = ()
    timeout: float | None = None
    should_fail: bool = False
    serial: bool = False
    disabled: bool = False
    data: tuple[Path, ...] = ()
    depends_on: tuple[str, ...] = ()
    # If set, the runner runs the binary's "list test cases" flag at run
    # time and expands this entry into one entry per discovered case.
    # One of "gtest", "doctest", "catch2", or None.
    discover: str | None = None
    defined_at: str = ""

    def to_jsonable(self) -> dict:
        """Return a plain dict suitable for json.dumps()."""
        d = asdict(self)
        d["cwd"] = str(self.cwd) if self.cwd is not None else None
        d["labels"] = list(self.labels)
        d["data"] = [str(p) for p in self.data]
        d["depends_on"] = list(self.depends_on)
        return d


# Keys on a Test target's partial spec that can be tweaked after creation.
# `name` and `defined_at` are intentionally excluded: changing them after
# the fact would make filter output and error messages confusing without
# adding much value over just creating the test correctly upfront.
_MUTABLE_TEST_KEYS = frozenset(
    {
        "args",
        "cwd",
        "env",
        "labels",
        "timeout",
        "should_fail",
        "serial",
        "disabled",
        "data",
        "depends_on",
        "discover",
        "program",
    }
)


def set_test_property(target: Target, key: str, value: Any) -> None:
    """Update a single property on a Test target.

    Mirrors CMake's ``set_tests_properties(NAME ... PROPERTIES key value)``.
    Useful when a property depends on context that isn't known at the
    ``project.Test(...)`` call site — e.g., bumping a timeout for a slow
    test, or tagging a group of tests with a label in a loop.

    Args:
        target: The Test target to update (returned by ``project.Test()``).
        key: Property name. One of: ``args``, ``cwd``, ``env``, ``labels``,
            ``timeout``, ``should_fail``, ``serial``, ``disabled``,
            ``data``, ``program``.
        value: New value. Coerced the same way ``project.Test()`` does at
            creation time (lists for ``args``, tuples for ``labels`` and
            ``data``, dict for ``env``).

    Raises:
        TypeError: ``target`` is not a Test target.
        RuntimeError: ``target`` has already been resolved (call before
            ``project.generate()`` / ``project.resolve()``).
        KeyError: ``key`` is not a valid test property name.
    """
    if target.target_type != "test":
        raise TypeError(
            f"set_test_property: {target.name!r} is not a Test target "
            f"(target_type={target.target_type!r})."
        )
    if target._resolved:
        raise RuntimeError(
            f"Cannot modify test {target.name!r} after resolve(). "
            "Call set_test_property() before project.generate()."
        )
    if key not in _MUTABLE_TEST_KEYS:
        raise KeyError(
            f"Unknown test property {key!r}. "
            f"Valid properties: {sorted(_MUTABLE_TEST_KEYS)}."
        )
    partial = target._builder_data.get("spec_partial")
    if partial is None:
        # Defensive: the factory drops spec_partial after resolution, but
        # we already guarded with _resolved above. This catches the rare
        # case of a hand-built test target missing the partial dict.
        raise RuntimeError(
            f"Test {target.name!r} has no partial spec to update — "
            "is this really a target created by project.Test()?"
        )
    if key == "labels":
        value = tuple(value)
    elif key == "env":
        value = dict(value)
    elif key == "args":
        value = [str(a) for a in value]
    elif key == "data":
        value = tuple(value)
    elif key == "depends_on":
        value = tuple(str(d) for d in value)
    partial[key] = value


def set_test_properties(*targets: Target, **properties: Any) -> None:
    """Update multiple properties on one or more Test targets.

    A convenience wrapper around :func:`set_test_property` that mirrors
    CMake's ``set_tests_properties()`` (one call, many tests, many
    properties).

    Example::

        slow_tests = [t1, t2, t3]
        set_test_properties(*slow_tests, timeout=600, labels=["slow"])

    Args:
        *targets: Test targets to update.
        **properties: Property names and values. See
            :func:`set_test_property` for the valid keys.
    """
    for target in targets:
        for key, value in properties.items():
            set_test_property(target, key, value)


def collect_test_specs(project: Project) -> list[TestSpec]:
    """Return all resolved TestSpec objects from a project's test targets.

    Targets created by the Test builder store their final spec in
    ``target._builder_data["spec"]`` during resolution. This helper
    walks the project's targets and pulls them out in definition order.
    """
    specs: list[TestSpec] = []
    for target in project.targets:
        if target.target_type != "test":
            continue
        spec = target._builder_data.get("spec") if target._builder_data else None
        if isinstance(spec, TestSpec):
            specs.append(spec)
    return specs


def write_test_manifest(project: Project, output_dir: Path) -> Path | None:
    """Serialize the project's tests to ``<output_dir>/tests.json``.

    Returns the manifest path if one was written, or None when the
    project has no tests (no file is created in that case, to avoid
    leaving stale empty manifests around).
    """
    specs = collect_test_specs(project)
    if not specs:
        return None

    manifest_path = output_dir / MANIFEST_FILENAME
    data = {
        "version": MANIFEST_VERSION,
        "project": project.name,
        "build_dir": str(project.build_dir).replace("\\", "/"),
        "tests": [spec.to_jsonable() for spec in specs],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")
    logger.info("Wrote test manifest: %s (%d tests)", manifest_path, len(specs))
    return manifest_path
