# SPDX-License-Identifier: MIT
"""Test builder: declare a test in the build description.

The Test builder produces a Target with ``target_type="test"`` whose
``_builder_data["spec"]`` holds a fully-resolved :class:`TestSpec` after
the resolver runs. The Ninja generator emits a ``test-build`` phony so
``ninja test-build`` compiles every program a test runs against, and a
``test`` phony that invokes ``pcons test``. The TestSpecs are also
serialized to ``<build_dir>/tests.json`` for the runner to consume.

A Test target has no output files of its own — it is purely declarative,
similar to ``InstallSymlink``. Its dependency on the program target is
what makes ``test-build`` work.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.core.target import Target
from pcons.core.test import TestSpec
from pcons.util.source_location import get_caller_location

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.util.source_location import SourceLocation


# Characters that Target name validation accepts. We map anything else
# to "_" so that user-friendly test names (Catch2 sentences, doctest
# scenarios with spaces, gtest names with colons) don't crash the build.
# The user-visible name on the spec is left intact — only the *internal*
# Ninja-target name is sanitized.
_TARGET_NAME_BAD_CHARS = re.compile(r"[^\w./+-]")


def _make_internal_target_name(project: Project, user_name: str) -> str:
    """Compute a unique, Ninja-safe target name for a test.

    Steps:
      1. Replace every char outside ``[\\w./+-]`` with ``_`` so names like
         ``"server connects"`` or ``"NetSuite::ssl"`` become valid.
      2. Prefix with ``test_`` so ``project.Test("hello", ...)`` doesn't
         collide with ``project.Program("hello", ...)`` — the common case.
      3. Suffix with a counter if needed so duplicates don't crash.
    """
    sanitized = _TARGET_NAME_BAD_CHARS.sub("_", user_name)
    base_name = f"test_{sanitized}"
    target_name = base_name
    counter = 1
    while project.get_target(target_name) is not None:
        target_name = f"{base_name}_{counter}"
        counter += 1
    return target_name


class TestNodeFactory:
    """Factory that finalizes a TestSpec during resolution.

    At ``create_target`` time, we only have a partial spec — the
    ``program`` field may still be an unresolved Target whose
    ``output_nodes`` will not be populated until the resolver runs.
    Phase 1 of resolution visits targets in topological order, so by
    the time we resolve a Test target, its program target's outputs
    are guaranteed to be available.
    """

    def __init__(self, project: Project) -> None:
        self.project = project

    def resolve(
        self,
        target: Target,
        env: Environment | None,  # noqa: ARG002 — required by the protocol
    ) -> None:
        """Build the final TestSpec from the partial spec."""
        partial = target._builder_data.get("spec_partial")
        if partial is None:
            return

        program = partial["program"]
        program_path: str
        if isinstance(program, Target):
            if not program.output_nodes:
                logger.warning(
                    "Test '%s' references target '%s' which has no outputs",
                    partial["name"],
                    program.name,
                )
                return
            program_path = self._format_path(program.output_nodes[0].path)
        else:
            program_path = self._format_path(Path(program))

        cwd_value: Path | None = None
        if partial["cwd"] is not None:
            cwd_value = Path(partial["cwd"])

        spec = TestSpec(
            name=partial["name"],
            command=[program_path, *partial["args"]],
            cwd=cwd_value,
            env=dict(partial["env"]),
            labels=tuple(partial["labels"]),
            timeout=partial["timeout"],
            should_fail=partial["should_fail"],
            serial=partial["serial"],
            disabled=partial["disabled"],
            data=tuple(Path(p) for p in partial["data"]),
            depends_on=tuple(partial.get("depends_on", ())),
            discover=partial.get("discover"),
            defined_at=partial["defined_at"],
        )
        target._builder_data["spec"] = spec
        # Drop the partial — keeps __repr__/print_targets tidy.
        target._builder_data.pop("spec_partial", None)

    def resolve_pending(self, target: Target) -> None:  # noqa: ARG002
        """Test targets have no pending sources to resolve."""

    def _format_path(self, path: Path) -> str:
        """Render a program path relative to the build directory if possible.

        The runner is invoked from the build directory (either by ``ninja
        test`` via a phony rule, or by the user running ``pcons test``
        from there), so a build-dir-relative path is the natural form
        and works on every platform without further translation.
        """
        try:
            rel = path.resolve().relative_to(self.project.build_dir.resolve())
            return str(rel).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")


@builder("Test", target_type="test", factory_class=TestNodeFactory)
class TestBuilder:
    """Declare a test to be run by ``pcons test`` (or ``ninja test``).

    See :class:`pcons.core.test.TestSpec` for the full set of fields.
    """

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        program: Target | Path | str,
        *,
        args: Sequence[str] = (),
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        labels: Sequence[str] = (),
        timeout: float | None = None,
        should_fail: bool = False,
        serial: bool = False,
        disabled: bool = False,
        data: Sequence[Path | str] = (),
        depends_on: Sequence[str] = (),
        discover: str | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a Test target.

        Args:
            project: The project to add the target to.
            name: Test name. Shown by the runner, used for ``-R`` filters.
                Need not be unique with other target names — internally
                the test target is named ``test_<name>``.
            program: The thing to run. A Target (typically from
                ``project.Program``), a path, or a string command name.
            args: Arguments passed after the program.
            cwd: Working directory for the test process. Defaults to the
                build directory.
            env: Extra environment variables for the test process.
            labels: Tags used for filtering. Open-ended; common choices
                are "unit", "integration", "slow", "fuzz", "gpu".
            timeout: Seconds before the runner kills the test. ``None``
                means no timeout.
            should_fail: If True, the runner inverts the pass/fail
                interpretation of the exit code (used for XFAIL tests).
            serial: If True, runner will not parallelize this test with
                others (useful for tests that contend on shared
                resources like ports or GPUs).
            disabled: If True, the test is recorded but always skipped.
            data: Files the test reads at run time. Informational in v1
                — not enforced by the build graph yet.

        Returns:
            The Test target. It has no output files; depends on
            ``program`` if that was a Target so ``test-build`` works.

        Example:
            ::

                math_test = project.Program("math_test", env,
                                            sources=["test_math.c"])
                project.Test("math.unit", math_test,
                             args=["--quick"], labels=["unit"],
                             timeout=30)
        """
        if not isinstance(name, str) or not name:
            raise TypeError(
                f"Test name must be a non-empty string, got {type(name).__name__}."
            )
        if discover is not None and discover not in ("gtest", "doctest", "catch2"):
            raise ValueError(
                f"Test {name!r}: discover={discover!r} is not a known protocol. "
                "Use 'gtest', 'doctest', or 'catch2' — or None to disable."
            )

        target_name = _make_internal_target_name(project, name)
        target = Target(
            target_name,
            target_type="test",
            defined_at=defined_at or get_caller_location(),
        )
        target._project = project
        target._builder_name = "Test"

        # Partial spec — the factory finalizes the program path during resolve.
        target._builder_data = {
            "spec_partial": {
                "name": name,
                "program": program,
                "args": [str(a) for a in args],
                "cwd": cwd,
                "env": dict(env) if env else {},
                "labels": tuple(labels),
                "timeout": timeout,
                "should_fail": should_fail,
                "serial": serial,
                "disabled": disabled,
                "data": tuple(data),
                "depends_on": tuple(str(d) for d in depends_on),
                "discover": discover,
                "defined_at": str(target.defined_at) if target.defined_at else "",
            },
        }

        # Depend on the program target so the topological sort resolves
        # it first (its output_nodes must exist before the factory runs)
        # and so the Ninja `test-build` phony pulls it in transitively.
        if isinstance(program, Target):
            target.dependencies.append(program)

        project.add_target(target)
        return target
