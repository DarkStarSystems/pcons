# SPDX-License-Identifier: MIT
"""Imported targets for external dependencies.

This module provides ImportedTarget, which represents an external library
that was found by a package finder. Unlike regular targets that are built
from source, imported targets represent pre-built libraries.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.target import Target

if TYPE_CHECKING:
    from pcons.packages.description import PackageDescription


class ImportedTarget(Target):
    """A target representing an external dependency.

    ImportedTarget wraps a PackageDescription and provides the interface
    expected by the build system. When a target depends on an imported
    target, the appropriate compile and link flags are automatically
    added.

    Attributes:
        name: Target name (usually the package name).
        package: Package description with all the details.
        is_imported: Always True for imported targets.
        requested_components: Which components were requested.

    Example:
        # Find a package
        zlib = finder.find("zlib")

        # Create an imported target
        target = ImportedTarget.from_package(zlib)

        # Use as a dependency
        env.cc.flags += target.compile_flags
        env.link.libs += target.link_flags
    """

    __slots__ = ("package", "is_imported", "requested_components")

    def __init__(
        self,
        name: str,
        *,
        package: PackageDescription | None = None,
        requested_components: list[str] | None = None,
    ) -> None:
        """Create an imported target.

        Args:
            name: Target name (usually the package name).
            package: Package description with all the details.
            requested_components: Which components were requested.
        """
        super().__init__(name)
        self.package = package
        self.is_imported = True
        self.requested_components = requested_components or []

        # Populate public requirements from package so they propagate to dependents
        if package is not None:
            self._populate_public_from_package(package)

    def _populate_public_from_package(self, package: PackageDescription) -> None:
        """Populate public usage requirements from package description.

        This ensures that when a target links to this ImportedTarget,
        the package's include dirs, libraries, and flags are properly
        propagated through the standard usage requirements mechanism.
        """
        # Include directories
        for inc_dir in package.include_dirs:
            path = Path(inc_dir) if isinstance(inc_dir, str) else inc_dir
            self.public.include_dirs.append(path)

        # Defines
        for define in package.defines:
            self.public.defines.append(define)

        # Compile flags
        for flag in package.compile_flags:
            self.public.compile_flags.append(flag)

        # Link libraries (just the names, not -l prefixed)
        for lib in package.libraries:
            self.public.link_libs.append(lib)

        # Library directories go into link_flags as -L prefixed
        # (UsageRequirements doesn't have link_dirs, only link_flags)
        for lib_dir in package.library_dirs:
            self.public.link_flags.append(f"-L{lib_dir}")

        # Framework directories and frameworks (macOS)
        for fw_dir in package.framework_dirs:
            self.public.link_flags.extend(["-F", fw_dir])
        for fw in package.frameworks:
            self.public.link_flags.extend(["-framework", fw])

        # Other link flags (-Wl,-rpath, etc.)
        for flag in package.link_flags:
            self.public.link_flags.append(flag)

    @classmethod
    def from_package(
        cls,
        package: PackageDescription,
        components: list[str] | None = None,
    ) -> ImportedTarget:
        """Create an imported target from a package description.

        Args:
            package: The package description.
            components: Optional list of components to include.

        Returns:
            ImportedTarget instance.
        """
        # If components requested, merge them
        merged_pkg = package
        if components:
            for comp_name in components:
                comp = package.get_component(comp_name)
                if comp is not None:
                    merged_pkg = merged_pkg.merge_component(comp)

        return cls(
            name=package.name,
            package=merged_pkg,
            requested_components=components,
        )

    @property
    def compile_flags(self) -> list[str]:
        """Get compile flags for this target."""
        if self.package is None:
            return []
        return self.package.get_compile_flags()

    @property
    def link_flags(self) -> list[str]:
        """Get link flags for this target."""
        if self.package is None:
            return []
        return self.package.get_link_flags()

    @property
    def include_dirs(self) -> list[Path]:
        """Get include directories."""
        if self.package is None:
            return []
        return [Path(d) for d in self.package.include_dirs]

    @property
    def library_dirs(self) -> list[Path]:
        """Get library directories."""
        if self.package is None:
            return []
        return [Path(d) for d in self.package.library_dirs]

    @property
    def libraries(self) -> list[str]:
        """Get library names."""
        if self.package is None:
            return []
        return self.package.libraries

    @property
    def defines(self) -> list[str]:
        """Get preprocessor definitions."""
        if self.package is None:
            return []
        return self.package.defines

    @property
    def version(self) -> str:
        """Get package version."""
        if self.package is None:
            return ""
        return self.package.version

    def __repr__(self) -> str:
        comp_str = ""
        if self.requested_components:
            comp_str = f", components={self.requested_components}"
        return f"ImportedTarget({self.name!r}, version={self.version!r}{comp_str})"
