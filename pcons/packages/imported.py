# SPDX-License-Identifier: MIT
"""Imported targets: pre-built external libraries found by package finders."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.target import Target, UsageRequirements

if TYPE_CHECKING:
    from typing import Any

    from pcons.packages.description import PackageDescription


def requirements_from_package(package: Any) -> UsageRequirements:
    """Translate a package description into :class:`UsageRequirements`.

    The single home of the package→requirements vocabulary mapping
    (``libraries``→``link_libs``, ``library_dirs``→``link_dirs``, ...),
    used by both :class:`ImportedTarget` and ``env.use()``. Fields the
    package doesn't have are treated as empty, so any duck-typed
    description works. Frameworks stay structured (``frameworks``/
    ``framework_dirs``); consumers lower them as needed.
    """
    reqs = UsageRequirements()
    for inc_dir in getattr(package, "include_dirs", ()) or ():
        reqs.include_dirs.append(Path(inc_dir))
    for define in getattr(package, "defines", ()) or ():
        reqs.defines.append(define)
    for flag in getattr(package, "compile_flags", ()) or ():
        reqs.compile_flags.append(flag)
    for lib in getattr(package, "libraries", ()) or ():
        reqs.link_libs.append(lib)
    for lib_dir in getattr(package, "library_dirs", ()) or ():
        reqs.link_dirs.append(Path(lib_dir))
    for flag in getattr(package, "link_flags", ()) or ():
        reqs.link_flags.append(flag)
    for fw in getattr(package, "frameworks", ()) or ():
        reqs.frameworks.append(fw)
    for fw_dir in getattr(package, "framework_dirs", ()) or ():
        reqs.framework_dirs.append(str(fw_dir))
    return reqs


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
        # Find packages via project (preferred)
        zlib = project.find_package("zlib")
        openssl = project.find_package("openssl")
        app = project.Program("myapp", env, sources=["main.c"])
        app.link(zlib, openssl)  # flags propagate automatically

        # Header-only lib without a .pc file — create manually
        httplib = ImportedTarget.from_package(PackageDescription(
            name="cpp-httplib",
            include_dirs=["/usr/include"],
            defines=["CPPHTTPLIB_OPENSSL_SUPPORT"],
        ))
        httplib.link(openssl)  # transitive: consumers of httplib get openssl too
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
        """Populate public usage requirements from the package description.

        Frameworks are lowered to ``-F``/``-framework`` link-flag pairs
        here because the resolve path consumes ``link_flags``.
        """
        reqs = requirements_from_package(package)
        for name in (
            "include_dirs",
            "defines",
            "compile_flags",
            "link_libs",
            "link_dirs",
            "link_flags",
        ):
            dst = getattr(self.public, name)
            for value in getattr(reqs, name):
                dst.append(value)
        for fw_dir in reqs.framework_dirs:
            self.public.link_flags.extend(["-F", str(fw_dir)])
        for fw in reqs.frameworks:
            self.public.link_flags.extend(["-framework", fw])

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
