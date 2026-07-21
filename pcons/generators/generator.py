# SPDX-License-Identifier: MIT
"""Generator protocol for build file generation.

Generators take a configured Project and produce build system files
(e.g., Ninja, Makefiles, IDE project files). Generation is deferred:
``generate()`` enqueues work that runs via ``_generate_pending()`` —
called by the CLI, or by an atexit hook on direct script runs.
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

    Takes a configured Project and writes build files under
    project.build_dir.
    """

    @property
    def name(self) -> str:
        """Generator name (e.g., 'ninja', 'make', 'compile_commands')."""
        ...

    def generate(self, project: Project) -> None:
        """Generate build files for a project."""
        ...


class BaseGenerator:
    """Base class for generators with common functionality."""

    _supports_compile_commands: bool = False

    _is_build_generator: bool = False
    """True for generators that produce build files (vs. auxiliary files
    like compile_commands.json)."""

    __pending = dict[int, list[Callable[[], None]]]()
    """Pending generate requests"""

    __atexit_registered = False
    """Whether the atexit handler has been registered"""

    __excepthook_installed = False
    """Whether the crash-cancellation excepthook has been installed"""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def generate(
        self,
        project: Project,
        *,
        compile_commands: bool = True,
        root_symlink: bool = True,
    ) -> None:
        """Register a deferred generate for this project.

        The project is auto-resolved when generation actually runs.

        Args:
            project: The configured project to generate for.
            compile_commands: If True (default) and this generator supports
                it, also generate ``compile_commands.json``.
            root_symlink: If True (default), maintain a
                ``compile_commands.json`` symlink at the project root so
                IDEs/clangd find it. With multiple build configurations,
                the last generation to run owns the root link.
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

                cc_gen = CompileCommandsGenerator(root_symlink=root_symlink)
                cc_gen._generate_impl(project, cc_gen._resolve_output_dir(project))

            # No-op when the project declares no Test targets.
            from pcons.core.test import write_test_manifest

            write_test_manifest(project, output_dir)

        BaseGenerator.__pending.setdefault(id(project), []).append(_generate_later)

        if self._is_build_generator:
            project._mark_generated()

        BaseGenerator._register_atexit()

    @staticmethod
    def _register_atexit() -> None:
        """Install the atexit hook that runs pending generation (idempotent).

        Also installs a sys.excepthook wrapper that cancels pending
        generation on an unhandled exception — build files must not be
        generated from a partially-executed script.
        """
        if BaseGenerator.__atexit_registered:
            return
        import atexit

        atexit.register(BaseGenerator._generate_pending, _is_atexit=True)
        BaseGenerator.__atexit_registered = True

        if not BaseGenerator.__excepthook_installed:
            import sys

            prev_hook = sys.excepthook

            def _cancel_pending_on_crash(exc_type, exc, tb):  # type: ignore[no-untyped-def]
                BaseGenerator._clear_pending()
                prev_hook(exc_type, exc, tb)

            sys.excepthook = _cancel_pending_on_crash
            BaseGenerator.__excepthook_installed = True

    @staticmethod
    def _clear_pending() -> None:
        """Clear all pending generates and drop the atexit hook (for testing).

        Both must go: a leftover hook would fire at interpreter shutdown
        against an already-torn-down project tree.
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

        Defaults to the top-level project; safe to call when nothing is
        pending. Called explicitly, errors propagate. From the atexit hook,
        a missing top-level project is a silent no-op, but a real generation
        error forces a nonzero process exit — Python ignores exceptions
        raised at shutdown and would otherwise exit 0.
        """
        try:
            if project is None:
                from pcons.core.project import Project as _Project

                try:
                    project = _Project.top_level()
                except ValueError:
                    if _is_atexit:
                        # Project tree already torn down; nothing to generate.
                        return
                    raise

            # Auxiliary generators (dot, mermaid, metadata, compile_commands)
            # are additive: requesting one must not cancel the build
            # generation. project.generate() is a no-op if a build generator
            # already ran, and respects PCONS_GENERATOR / --generator.
            project.generate()

            pending = BaseGenerator.__pending.pop(id(project), [])
            for func in pending:
                func()

            if BaseGenerator.__atexit_registered:
                # Nothing pending anymore; drop the atexit hook.
                import atexit

                atexit.unregister(BaseGenerator._generate_pending)
                BaseGenerator.__atexit_registered = False
        except Exception as e:
            import sys
            import traceback

            if _is_atexit:
                # Python ignores exceptions from atexit handlers (and
                # sys.exit) and would exit 0; report and force nonzero.
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
        """Compute the output directory: build_dir, resolved against
        root_dir if relative."""
        if project.build_dir.is_absolute():
            return project.build_dir
        return project.root_dir / project.build_dir

    def _generate_impl(self, project: Project, output_dir: Path) -> None:
        """Implementation of generate. Subclasses must override."""
        raise NotImplementedError

    def _get_target_build_nodes(self, target: Target) -> list[FileNode]:
        """Get all FileNodes with build information from a resolved target."""
        nodes: list[FileNode] = []

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
