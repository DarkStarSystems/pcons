# SPDX-License-Identifier: MIT
"""Built-in builders for pcons.

This package contains the built-in builders that are registered with the
BuilderRegistry. All builders (built-in and user-defined) register through
the same system, ensuring user-defined builders are on equal footing with
built-ins.

Built-in builders:
- Install, InstallAs, InstallDir: File installation builders (pcons.tools.install)
- Tarfile, Zipfile: Archive builders (pcons.tools.archive)
- Program, StaticLibrary, SharedLibrary, ObjectLibrary: Compile/link builders
- HeaderOnlyLibrary: Interface library builder
- Command: Custom command builder
"""

from __future__ import annotations


def register_builtin_builders() -> None:
    """Register all built-in builders with the BuilderRegistry.

    This is called during pcons initialization to ensure all built-in
    builders are available on Project instances.
    """
    # Import builder modules to trigger their registration
    # Each module uses the @builder decorator to register its builders
    from pcons.builders import compile  # noqa: F401

    # Install and Archive builders are now in pcons.tools (merged with tools)
    from pcons.tools import archive, install  # noqa: F401

    # Contrib builders: platform-specific installer/packaging helpers
    _register_contrib_builders()


def _register_contrib_builders() -> None:
    """Register contrib builders (platform-specific installers, bundles)."""
    from pcons.contrib.bundle import create_flat_bundle, create_macos_bundle
    from pcons.contrib.installers.macos import (
        create_component_pkg,
        create_dmg,
        create_pkg,
    )
    from pcons.contrib.installers.windows import create_appx, create_msix
    from pcons.core.builder_registry import BuilderRegistry

    BuilderRegistry.register(
        "Pkg",
        create_target=create_pkg,
        target_type="installer",
        requires_env=True,
        description="Create a macOS product archive (.pkg) installer",
        platforms=["darwin"],
    )
    BuilderRegistry.register(
        "ComponentPkg",
        create_target=create_component_pkg,
        target_type="installer",
        requires_env=True,
        description="Create a macOS component package using pkgbuild",
        platforms=["darwin"],
    )
    BuilderRegistry.register(
        "Dmg",
        create_target=create_dmg,
        target_type="installer",
        requires_env=True,
        description="Create a macOS .dmg disk image",
        platforms=["darwin"],
    )
    BuilderRegistry.register(
        "Msix",
        create_target=create_msix,
        target_type="installer",
        requires_env=True,
        description="Create a Windows MSIX package",
        platforms=["win32"],
    )
    BuilderRegistry.register(
        "Appx",
        create_target=create_appx,
        target_type="installer",
        requires_env=True,
        description="Create a Windows AppX package (legacy MSIX format)",
        platforms=["win32"],
    )
    BuilderRegistry.register(
        "MacosBundle",
        create_target=create_macos_bundle,
        target_type="installer",
        requires_env=True,
        description="Create a macOS .bundle or .plugin structure",
        platforms=["darwin"],
    )
    BuilderRegistry.register(
        "FlatBundle",
        create_target=create_flat_bundle,
        target_type="installer",
        requires_env=True,
        description="Create a flat directory bundle (cross-platform)",
    )
