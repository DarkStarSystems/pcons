# SPDX-License-Identifier: MIT
"""Package description for external dependencies.

This module defines the PackageDescription class which represents
an external library or package that can be used in a pcons build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]


@dataclass
class ComponentDescription:
    """Description of a package component.

    Components represent optional parts of a package (e.g., "filesystem"
    component of Boost, "widgets" component of Qt).

    Attributes:
        name: Component name.
        include_dirs: Additional include directories for this component.
        library_dirs: Additional library directories for this component.
        libraries: Libraries for this component.
        defines: Preprocessor definitions for this component.
        compile_flags: Compile flags for this component.
        link_flags: Link flags for this component.
        dependencies: Other components this component depends on.
    """

    name: str
    include_dirs: list[str] = field(default_factory=list)
    library_dirs: list[str] = field(default_factory=list)
    libraries: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    compile_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        result: dict[str, Any] = {}
        if self.include_dirs:
            result["include_dirs"] = self.include_dirs
        if self.library_dirs:
            result["library_dirs"] = self.library_dirs
        if self.libraries:
            result["libraries"] = self.libraries
        if self.defines:
            result["defines"] = self.defines
        if self.compile_flags:
            result["compile_flags"] = self.compile_flags
        if self.link_flags:
            result["link_flags"] = self.link_flags
        if self.dependencies:
            result["dependencies"] = self.dependencies
        return result

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ComponentDescription:
        """Create from dictionary (TOML deserialization)."""
        return cls(
            name=name,
            include_dirs=data.get("include_dirs", []),
            library_dirs=data.get("library_dirs", []),
            libraries=data.get("libraries", []),
            defines=data.get("defines", []),
            compile_flags=data.get("compile_flags", []),
            link_flags=data.get("link_flags", []),
            dependencies=data.get("dependencies", []),
        )


@dataclass
class PackageDescription:
    """Description of an external package.

    This class represents all the information needed to use an external
    library in a pcons build. It can be serialized to/from TOML for
    caching discovered package information.

    Attributes:
        name: Package name (e.g., "zlib", "openssl", "boost").
        version: Package version string.
        include_dirs: Include directories (-I flags).
        library_dirs: Library directories (-L flags).
        libraries: Libraries to link (-l flags, without the -l prefix).
        defines: Preprocessor definitions (-D flags, without the -D prefix).
        compile_flags: Additional compile flags.
        link_flags: Additional link flags.
        frameworks: macOS frameworks to link (-framework flags, names only).
        framework_dirs: macOS framework search directories (-F flags).
        dependencies: Other packages this package depends on.
        components: Named components of this package.
        prefix: Installation prefix (root directory of the package).
        found_by: How the package was found (e.g., "pkg-config", "system").

    Example:
        # Create a package description
        pkg = PackageDescription(
            name="zlib",
            version="1.2.13",
            include_dirs=["/usr/include"],
            libraries=["z"],
        )

        # Save to TOML
        pkg.to_toml(Path("zlib.pcons-pkg.toml"))

        # Load from TOML
        pkg2 = PackageDescription.from_toml(Path("zlib.pcons-pkg.toml"))

        # macOS package with frameworks
        pkg = PackageDescription(
            name="metal",
            frameworks=["Metal", "Foundation"],
        )
    """

    name: str
    version: str = ""
    include_dirs: list[str] = field(default_factory=list)
    library_dirs: list[str] = field(default_factory=list)
    libraries: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    compile_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    framework_dirs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    components: dict[str, ComponentDescription] = field(default_factory=dict)
    prefix: str = ""
    found_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for TOML serialization."""
        result: dict[str, Any] = {
            "package": {
                "name": self.name,
            }
        }

        if self.version:
            result["package"]["version"] = self.version
        if self.prefix:
            result["package"]["prefix"] = self.prefix
        if self.found_by:
            result["package"]["found_by"] = self.found_by
        if self.dependencies:
            result["package"]["dependencies"] = self.dependencies

        # Paths section
        paths: dict[str, Any] = {}
        if self.include_dirs:
            paths["include_dirs"] = self.include_dirs
        if self.library_dirs:
            paths["library_dirs"] = self.library_dirs
        if paths:
            result["paths"] = paths

        # Link section
        link: dict[str, Any] = {}
        if self.libraries:
            link["libraries"] = self.libraries
        if self.link_flags:
            link["flags"] = self.link_flags
        if self.frameworks:
            link["frameworks"] = self.frameworks
        if self.framework_dirs:
            link["framework_dirs"] = self.framework_dirs
        if link:
            result["link"] = link

        # Compile section
        compile_section: dict[str, Any] = {}
        if self.defines:
            compile_section["defines"] = self.defines
        if self.compile_flags:
            compile_section["flags"] = self.compile_flags
        if compile_section:
            result["compile"] = compile_section

        # Components section
        if self.components:
            result["components"] = {
                name: comp.to_dict() for name, comp in self.components.items()
            }

        return result

    def to_toml(self, path: Path) -> None:
        """Write package description to a TOML file.

        Args:
            path: Path to write the TOML file to.
        """
        import tomli_w  # Lazy import - only needed when writing TOML

        data = self.to_dict()
        path.write_bytes(tomli_w.dumps(data).encode("utf-8"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageDescription:
        """Create from dictionary (TOML deserialization).

        Args:
            data: Dictionary from TOML parsing.

        Returns:
            PackageDescription instance.
        """
        package = data.get("package", {})
        paths = data.get("paths", {})
        link = data.get("link", {})
        compile_section = data.get("compile", {})
        components_data = data.get("components", {})

        components = {
            name: ComponentDescription.from_dict(name, comp_data)
            for name, comp_data in components_data.items()
        }

        return cls(
            name=package.get("name", ""),
            version=package.get("version", ""),
            prefix=package.get("prefix", ""),
            found_by=package.get("found_by", ""),
            dependencies=package.get("dependencies", []),
            include_dirs=paths.get("include_dirs", []),
            library_dirs=paths.get("library_dirs", []),
            libraries=link.get("libraries", []),
            link_flags=link.get("flags", []),
            frameworks=link.get("frameworks", []),
            framework_dirs=link.get("framework_dirs", []),
            defines=compile_section.get("defines", []),
            compile_flags=compile_section.get("flags", []),
            components=components,
        )

    @classmethod
    def from_toml(cls, path: Path) -> PackageDescription:
        """Load package description from a TOML file.

        Args:
            path: Path to the TOML file.

        Returns:
            PackageDescription instance.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            tomllib.TOMLDecodeError: If the file is not valid TOML.
        """
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return cls.from_dict(data)

    def get_include_flags(self) -> list[str]:
        """Get include directory flags (-I...)."""
        return [f"-I{d}" for d in self.include_dirs]

    def get_library_dir_flags(self) -> list[str]:
        """Get library directory flags (-L...)."""
        return [f"-L{d}" for d in self.library_dirs]

    def get_library_flags(self) -> list[str]:
        """Get library flags (-l...)."""
        return [f"-l{lib}" for lib in self.libraries]

    def get_define_flags(self) -> list[str]:
        """Get preprocessor definition flags (-D...)."""
        return [f"-D{d}" for d in self.defines]

    def get_compile_flags(self) -> list[str]:
        """Get all compile flags."""
        flags: list[str] = []
        flags.extend(self.get_include_flags())
        flags.extend(self.get_define_flags())
        flags.extend(self.compile_flags)
        return flags

    def get_framework_flags(self) -> list[str]:
        """Get framework flags (-framework ..., macOS only)."""
        flags: list[str] = []
        for fw in self.frameworks:
            flags.extend(["-framework", fw])
        return flags

    def get_framework_dir_flags(self) -> list[str]:
        """Get framework directory flags (-F..., macOS only)."""
        return [f"-F{d}" for d in self.framework_dirs]

    def get_link_flags(self) -> list[str]:
        """Get all link flags."""
        flags: list[str] = []
        flags.extend(self.get_library_dir_flags())
        flags.extend(self.get_library_flags())
        flags.extend(self.get_framework_dir_flags())
        flags.extend(self.get_framework_flags())
        flags.extend(self.link_flags)
        return flags

    def get_component(self, name: str) -> ComponentDescription | None:
        """Get a component by name.

        Args:
            name: Component name.

        Returns:
            ComponentDescription if found, None otherwise.
        """
        return self.components.get(name)

    def merge_component(self, component: ComponentDescription) -> PackageDescription:
        """Return a new description with component settings merged.

        This is useful when a user requests a specific component of a
        package - the component's settings are merged with the base
        package settings.

        Args:
            component: Component to merge.

        Returns:
            New PackageDescription with merged settings.
        """
        return PackageDescription(
            name=self.name,
            version=self.version,
            prefix=self.prefix,
            found_by=self.found_by,
            dependencies=self.dependencies + component.dependencies,
            include_dirs=self.include_dirs + component.include_dirs,
            library_dirs=self.library_dirs + component.library_dirs,
            libraries=self.libraries + component.libraries,
            defines=self.defines + component.defines,
            compile_flags=self.compile_flags + component.compile_flags,
            link_flags=self.link_flags + component.link_flags,
            frameworks=self.frameworks,  # Components don't have frameworks
            framework_dirs=self.framework_dirs,  # Components don't have framework dirs
            components=self.components,
        )
