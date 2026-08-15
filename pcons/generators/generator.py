# SPDX-License-Identifier: MIT
"""Generator protocol for build file generation.

Generators take a configured Project and produce build system files
(e.g., Ninja, Makefiles, IDE project files). Generation is deferred:
``generate()`` enqueues work that runs via ``_generate_pending()``, which
``pcons`` calls right after it has run the build script.

Generation used to also run from an atexit hook, so that a script started as
``python pcons-build.py`` produced build files without asking. That put the
whole of configure — tool detection, compiler probes, dependency scanning —
inside an interpreter shutdown callback, where a worker pool cannot be started
and a library that registers a cleanup handler on import cannot even be
imported.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pcons.core.node import FileNode

if TYPE_CHECKING:
    from pcons.core.project import Project
    from pcons.core.target import Target


def apply_context_overrides(
    tokens: list, tool_name: str, context_overrides: dict[str, object]
) -> list:
    """Replace ``$tool.var`` references in tokens with context override values.

    For nodes with no environment (the standalone install/archive tools),
    where the tool's context supplies the values subst() would otherwise have.
    Has to run on the tokens: once they are quoted for a shell, the dollar
    these patterns match on has been escaped.
    """
    from pcons.core.subst import SourcePath, TargetPath

    result: list = []
    for token in tokens:
        if isinstance(token, (SourcePath, TargetPath)) or not isinstance(token, str):
            result.append(token)
            continue

        modified = token
        for key, val in context_overrides.items():
            pattern = f"${tool_name}.{key}"
            if pattern in modified:
                val_str = (
                    " ".join(str(v) for v in val) if isinstance(val, list) else str(val)
                )
                modified = modified.replace(pattern, val_str)
        result.append(modified)
    return result


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

    @staticmethod
    def _clear_pending() -> None:
        """Drop every pending generate request, without running any (testing)."""
        BaseGenerator.__pending.clear()

    @staticmethod
    def _generate_pending(project: Project | None = None) -> None:
        """Execute and clear pending generate requests for a project.

        Defaults to the top-level project; safe to call when nothing is
        pending. Errors propagate: the caller is an entry point that knows how
        to report them and what exit status to use.
        """
        if project is None:
            from pcons.core.project import Project as _Project

            project = _Project.top_level()

        # Auxiliary generators (dot, mermaid, metadata, compile_commands)
        # are additive: requesting one must not cancel the build
        # generation. project.generate() is a no-op if a build generator
        # already ran, and respects PCONS_GENERATOR / --generator.
        project.generate()

        pending = BaseGenerator.__pending.pop(id(project), [])
        for func in pending:
            func()

        # --graph/--mermaid, once every generator has run: the graph then
        # describes the project the build files were written from, and a
        # bad destination cannot cost the user those build files.
        project._output_graphs_if_requested()

    @staticmethod
    def _collect_path_flags(project: Project) -> frozenset[str]:
        """Path-carrying flags (-I, -isystem, /LIBPATH:, ...) of every
        toolchain in the project.

        Generators rewrite the paths in these flags relative to where the
        build tool runs. Which flags carry paths is toolchain knowledge, so
        it comes from the toolchains rather than a list in the generator.
        """
        flags: set[str] = set()
        for env in project.environments:
            for toolchain in getattr(env, "toolchains", []):
                getter = getattr(toolchain, "get_path_flags", None)
                if getter is not None:
                    flags.update(getter())
        return frozenset(flags)

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
