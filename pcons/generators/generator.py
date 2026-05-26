# SPDX-License-Identifier: MIT
"""Generator protocol for build file generation.

Generators take a configured Project and produce build system files
(e.g., Ninja, Makefiles, IDE project files).

NOTE: Build scripts may use the ``Generator()`` factory from the
top-level ``pcons`` package to register which generator(s) to use::

    from pcons import Generator, Project
    project = Project("myapp", build_dir="build")
    # ... define targets ...
    Generator().generate(project)   # requests Ninja by default

Actual generation is deferred: it runs when ``BaseGenerator._generate_pending()``
is called — either by the CLI after executing the build script, or via the
atexit handler when the script is run directly with ``python pcons-build.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pcons.core.node import FileNode

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


@runtime_checkable
class Generator(Protocol):
    """Protocol for build file generators.

    A Generator takes a configured Project and writes build files.
    The output directory is derived from project.build_dir.
    Different generators produce different formats (Ninja, Make,
    IDE projects, etc.).
    """

    @property
    def name(self) -> str:
        """Generator name (e.g., 'ninja', 'make', 'compile_commands')."""
        ...

    def generate(self, project: Project) -> None:
        """Generate build files for a project.

        Args:
            project: The configured project to generate for.
        """
        ...


class BaseGenerator:
    """Base class for generators with common functionality."""

    _supports_compile_commands: bool = False

    _is_build_generator: bool = False
    """Whether this generator produces build files (vs. auxiliary files like compile_commands.json).
    Used to determine whether to mark project as generated.
    """

    __pending = dict[int, list[Callable[[], None]]]()
    """Pending generate requests"""

    __atexit_registered = False
    """Whether the atexit handler has been registered"""

    def __init__(self, name: str) -> None:
        """Initialize a generator.

        Args:
            name: Generator name.
        """
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def generate(self, project: Project, *, compile_commands: bool = True) -> None:
        """Register a deferred generate for this project.

        Enqueues the generation work to run later — either when
        ``_generate_pending()`` is called by the CLI, or at process exit
        via the atexit handler when the script is run directly.

        The output directory is computed from ``project.build_dir``. The
        project is auto-resolved if not already resolved at the time the
        generation actually runs.

        For build generators (Ninja, Make, Xcode), also auto-generates
        ``compile_commands.json`` for IDE integration unless disabled.

        Args:
            project: The configured project to generate for.
            compile_commands: If True (default) and this generator supports
                it, automatically generate ``compile_commands.json`` alongside
                the build files.
        """

        def _generate_later():
            if not project._resolved:
                project.resolve()
            output_dir = self._resolve_output_dir(project)
            self._generate_impl(project, output_dir)

            if compile_commands and self._supports_compile_commands:
                from pcons.generators.compile_commands import (
                    CompileCommandsGenerator,
                )

                cc_gen = CompileCommandsGenerator()
                cc_gen._generate_impl(project, cc_gen._resolve_output_dir(project))

            # Write the test manifest if the project declares any tests.
            # Generator-agnostic: every backend gets it for free. Skipped
            # silently when there are no Test targets.
            from pcons.core.test import write_test_manifest

            write_test_manifest(project, output_dir)

        # Register the actual generation, will be actually executed either by CLI or atexit
        BaseGenerator.__pending.setdefault(id(project), []).append(_generate_later)

        if self._is_build_generator:
            # Mark project as generated when a build generator is registered
            project._mark_generated()

        if not BaseGenerator.__atexit_registered:
            # Register the atexit handler to run pending generates
            import atexit

            atexit.register(BaseGenerator._generate_pending, _is_atexit=True)
            BaseGenerator.__atexit_registered = True

    @staticmethod
    def _clear_pending() -> None:
        """Clear all pending generates and drop the atexit hook (for testing).

        Tests that call ``generate()`` register a process-wide atexit handler.
        Clearing the queue without also unregistering it would leave the hook
        to fire at interpreter shutdown against an already-torn-down project
        tree, so cleanup must remove both.
        """
        BaseGenerator.__pending.clear()
        if BaseGenerator.__atexit_registered:
            import atexit

            atexit.unregister(BaseGenerator._generate_pending)
            BaseGenerator.__atexit_registered = False

    @staticmethod
    def _generate_pending(
        project: Project | None = None, *, _is_atexit: bool = False
    ) -> None:
        """Execute and clear pending generate requests for a project.

        If *project* is ``None``, the top-level project is used.  When called
        explicitly (e.g. by the CLI), errors propagate to the caller, which
        turns them into a nonzero exit status.  When fired from the atexit
        hook, a missing top-level project (e.g. one torn down by tests) is a
        silent no-op, but a *real* generation error is reported to stderr and
        forces a nonzero process exit — Python would otherwise ignore an
        exception raised at shutdown and still exit 0.  Safe to call when
        nothing is pending (no-op).

        Args:
            project: The project whose pending generate requests should be executed.
                     Defaults to ``Project.top_level()``.
        """
        try:
            if project is None:
                from pcons.core.project import Project as _Project

                try:
                    project = _Project.top_level()
                except ValueError:
                    if _is_atexit:
                        # Project tree was already torn down (e.g. tests
                        # cleared it); nothing left to generate.
                        return
                    raise

            # ensure project generation is pending, no-op if already marked as generated
            project.generate()

            pending = BaseGenerator.__pending.pop(id(project), [])
            for func in pending:
                func()

            if BaseGenerator.__atexit_registered:
                # Unregister the atexit handler if there are no more pending generates to avoid running it unnecessarily at exit
                import atexit

                atexit.unregister(BaseGenerator._generate_pending)
                BaseGenerator.__atexit_registered = False
        except Exception as e:
            import sys
            import traceback

            if _is_atexit:
                # Interpreter shutdown: Python *ignores* exceptions raised from
                # atexit handlers (and sys.exit too) and would still exit 0, so
                # re-raising here would silently hide a real generation failure.
                # Report it and force a nonzero exit. (The benign "no project"
                # case returns earlier and never reaches here.)
                import os

                print(
                    f"Error during generator execution at exit: {e}\n"
                    "Traceback (most recent call last):",
                    file=sys.stderr,
                )
                traceback.print_exc()
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(1)
            raise

    def _resolve_output_dir(self, project: Project) -> Path:
        """Compute the output directory from the project.

        If build_dir is absolute, use it directly; otherwise
        resolve it relative to root_dir.

        Args:
            project: The project to get the output dir for.

        Returns:
            Absolute or resolved output directory path.
        """
        if project.build_dir.is_absolute():
            return project.build_dir
        return project.root_dir / project.build_dir

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Implementation of generate. Subclasses must override."""
        raise NotImplementedError

    def _get_target_build_nodes(self, target: Target) -> list[FileNode]:
        """Get all buildable file nodes from a target.

        This extracts nodes that have build information from resolved targets.

        Args:
            target: The target to get nodes from.

        Returns:
            List of FileNodes that have build information.
        """
        nodes: list[FileNode] = []

        # Add object nodes and output nodes
        for obj_node in target.intermediate_nodes:
            if isinstance(obj_node, FileNode):
                nodes.append(obj_node)
        for out_node in target.output_nodes:
            if isinstance(out_node, FileNode):
                nodes.append(out_node)
        # For interface targets (like Install), also check target.nodes
        if target.target_type == "interface":
            for target_node in target.nodes:
                if isinstance(target_node, FileNode):
                    has_build = getattr(target_node, "_build_info", None) is not None
                    if has_build:
                        nodes.append(target_node)

        return nodes

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"


class MultiGenerator:
    """Runs multiple generators in sequence."""

    def __init__(self, generators: Sequence[Generator]) -> None:
        self._generators = list(generators)

    @property
    def name(self) -> str:
        return ":".join(g.name for g in self._generators)

    def generate(self, project: Project) -> None:
        for gen in self._generators:
            gen.generate(project)

    def __repr__(self) -> str:
        return f"MultiGenerator({self.name!r})"
