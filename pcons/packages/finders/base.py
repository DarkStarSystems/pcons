# SPDX-License-Identifier: MIT
"""Base class for package finders.

Package finders are responsible for locating external libraries
and creating PackageDescription objects for them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pcons.packages.description import PackageDescription


class BaseFinder(ABC):
    """Abstract base class for package finders.

    Subclasses implement specific discovery mechanisms:
    - PkgConfigFinder: Uses pkg-config
    - SystemFinder: Searches standard system paths
    - ConanFinder: Uses Conan package manager
    - VcpkgFinder: Uses vcpkg package manager

    Example:
        finder = PkgConfigFinder()
        zlib = finder.find("zlib")
        if zlib:
            print(f"Found zlib {zlib.version}")
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the finder name (e.g., 'pkg-config', 'system')."""
        ...

    @abstractmethod
    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        """Try to find a package.

        Args:
            package_name: Name of the package to find.
            version: Optional version requirement (e.g., ">=1.0", "1.2.3").
            components: Optional list of components to find.

        Returns:
            PackageDescription if found, None otherwise.
        """
        ...

    def is_available(self) -> bool:
        """Check if this finder is available on the system.

        Override this to check for required tools (e.g., pkg-config).

        Returns:
            True if the finder can be used, False otherwise.
        """
        return True


class FinderChain:
    """Chain multiple finders together.

    Tries each finder in order until one succeeds.

    Example:
        chain = FinderChain([
            PkgConfigFinder(),
            SystemFinder(),
        ])
        zlib = chain.find("zlib")
    """

    def __init__(self, finders: list[BaseFinder]) -> None:
        """Create a finder chain.

        Args:
            finders: List of finders to try in order.
        """
        self._finders = [f for f in finders if f.is_available()]

    def find(
        self,
        package_name: str,
        version: str | None = None,
        components: list[str] | None = None,
    ) -> PackageDescription | None:
        """Find a package using the chain of finders.

        Args:
            package_name: Name of the package to find.
            version: Optional version requirement.
            components: Optional list of components.

        Returns:
            PackageDescription if found by any finder, None otherwise.
        """
        for finder in self._finders:
            result = finder.find(package_name, version, components)
            if result is not None:
                return result
        return None

    @property
    def finders(self) -> list[BaseFinder]:
        """Get the list of available finders."""
        return self._finders
