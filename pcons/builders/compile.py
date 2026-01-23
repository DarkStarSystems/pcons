# SPDX-License-Identifier: MIT
"""Compile/link builders for programs and libraries.

This module provides builders for compiled targets:
- Program: Create executable programs
- StaticLibrary: Create static libraries (.a, .lib)
- SharedLibrary: Create shared libraries (.so, .dylib, .dll)
- ObjectLibrary: Compile sources without linking
- HeaderOnlyLibrary: Interface library with no sources
- Command: Custom command builder
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.builder_registry import builder
from pcons.core.node import Node
from pcons.core.target import Target, TargetType
from pcons.util.source_location import get_caller_location

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project
    from pcons.util.source_location import SourceLocation


@builder("Program", target_type=TargetType.PROGRAM, requires_env=True)
class ProgramBuilder:
    """Create a program (executable) target."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: list[str | Path | Node] | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a Program target.

        Args:
            project: The project to add the target to.
            name: Target name (e.g., "myapp").
            env: Environment to use for building.
            sources: Source files for the program.
            defined_at: Source location where this was defined (auto-captured).

        Returns:
            A new Target configured as a program.
        """
        target = Target(
            name,
            target_type=TargetType.PROGRAM,
            defined_at=defined_at or get_caller_location(),
        )
        target._env = env
        target._project = project
        target._builder_name = "Program"

        if sources:
            source_nodes = _normalize_sources(project, sources)
            target.add_sources(source_nodes)

        project.add_target(target)
        return target


@builder("StaticLibrary", target_type=TargetType.STATIC_LIBRARY, requires_env=True)
class StaticLibraryBuilder:
    """Create a static library target."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: list[str | Path | Node] | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a StaticLibrary target.

        Args:
            project: The project to add the target to.
            name: Target name (e.g., "mylib").
            env: Environment to use for building.
            sources: Source files for the library.
            defined_at: Source location where this was defined (auto-captured).

        Returns:
            A new Target configured as a static library.
        """
        target = Target(
            name,
            target_type=TargetType.STATIC_LIBRARY,
            defined_at=defined_at or get_caller_location(),
        )
        target._env = env
        target._project = project
        target._builder_name = "StaticLibrary"

        if sources:
            source_nodes = _normalize_sources(project, sources)
            target.add_sources(source_nodes)

        project.add_target(target)
        return target


@builder("SharedLibrary", target_type=TargetType.SHARED_LIBRARY, requires_env=True)
class SharedLibraryBuilder:
    """Create a shared library target."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: list[str | Path | Node] | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a SharedLibrary target.

        Args:
            project: The project to add the target to.
            name: Target name (e.g., "mylib").
            env: Environment to use for building.
            sources: Source files for the library.
            defined_at: Source location where this was defined (auto-captured).

        Returns:
            A new Target configured as a shared library.
        """
        target = Target(
            name,
            target_type=TargetType.SHARED_LIBRARY,
            defined_at=defined_at or get_caller_location(),
        )
        target._env = env
        target._project = project
        target._builder_name = "SharedLibrary"

        if sources:
            source_nodes = _normalize_sources(project, sources)
            target.add_sources(source_nodes)

        project.add_target(target)
        return target


@builder("ObjectLibrary", target_type=TargetType.OBJECT, requires_env=True)
class ObjectLibraryBuilder:
    """Create an object library target (compiles but doesn't link)."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        sources: list[str | Path | Node] | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create an ObjectLibrary target.

        Args:
            project: The project to add the target to.
            name: Target name.
            env: Environment to use for building.
            sources: Source files to compile.
            defined_at: Source location where this was defined (auto-captured).

        Returns:
            A new Target configured as an object library.
        """
        target = Target(
            name,
            target_type=TargetType.OBJECT,
            defined_at=defined_at or get_caller_location(),
        )
        target._env = env
        target._project = project
        target._builder_name = "ObjectLibrary"

        if sources:
            source_nodes = _normalize_sources(project, sources)
            target.add_sources(source_nodes)

        project.add_target(target)
        return target


@builder("HeaderOnlyLibrary", target_type=TargetType.INTERFACE)
class HeaderOnlyLibraryBuilder:
    """Create a header-only (interface) library target."""

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        include_dirs: list[str | Path] | None = None,
        defined_at: SourceLocation | None = None,
    ) -> Target:
        """Create a HeaderOnlyLibrary target.

        Args:
            project: The project to add the target to.
            name: Target name (e.g., "my_headers").
            include_dirs: Include directories to propagate to dependents.
            defined_at: Source location where this was defined (auto-captured).

        Returns:
            A new Target configured as an interface library.
        """
        target = Target(
            name,
            target_type=TargetType.INTERFACE,
            defined_at=defined_at or get_caller_location(),
        )
        target._builder_name = "HeaderOnlyLibrary"

        if include_dirs:
            for inc_dir in include_dirs:
                target.public.include_dirs.append(Path(inc_dir))

        project.add_target(target)
        return target


@builder("Command", target_type=TargetType.COMMAND, requires_env=True)
class CommandBuilder:
    """Create a custom command target.

    This is a convenience wrapper that follows the target-centric API pattern.
    """

    @staticmethod
    def create_target(
        project: Project,
        name: str,
        env: Environment,
        *,
        target: str | Path | list[str | Path],
        source: str | Path | list[str | Path] | None = None,
        command: str | list[str] = "",
    ) -> Target:
        """Create a Command target.

        Args:
            project: The project to add the target to.
            name: Target name for `ninja <name>`.
            env: Environment to use.
            target: Output file(s).
            source: Input file(s).
            command: The shell command to run.

        Returns:
            A new Target configured as a command.
        """
        # Delegate to env.Command which handles all the complexity
        return env.Command(target=target, source=source, command=command, name=name)


def _normalize_sources(
    project: Project,
    sources: list[str | Path | Node],
) -> list[Node]:
    """Convert source paths/strings to nodes.

    Uses project's node() for deduplication.

    Args:
        project: Project for node deduplication.
        sources: List of source files.

    Returns:
        List of Node objects.
    """
    result: list[Node] = []
    for src in sources:
        if isinstance(src, Node):
            result.append(src)
        else:
            result.append(project.node(src))
    return result
