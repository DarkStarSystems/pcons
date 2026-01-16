# SPDX-License-Identifier: MIT
"""Generator protocol for build file generation.

Generators take a configured Project and produce build system files
(e.g., Ninja, Makefiles, IDE project files).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pcons.core.project import Project


@runtime_checkable
class Generator(Protocol):
    """Protocol for build file generators.

    A Generator takes a configured Project and writes build files
    to the output directory. Different generators produce different
    formats (Ninja, Make, IDE projects, etc.).
    """

    @property
    def name(self) -> str:
        """Generator name (e.g., 'ninja', 'make', 'compile_commands')."""
        ...

    def generate(self, project: Project, output_dir: Path) -> None:
        """Generate build files for a project.

        Args:
            project: The configured project to generate for.
            output_dir: Directory to write output files to.
        """
        ...


class BaseGenerator:
    """Base class for generators with common functionality."""

    def __init__(self, name: str) -> None:
        """Initialize a generator.

        Args:
            name: Generator name.
        """
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def generate(self, project: Project, output_dir: Path) -> None:
        """Generate build files. Subclasses must implement."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"
