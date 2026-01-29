# SPDX-License-Identifier: MIT
"""Installer generation modules for pcons.

This package provides helpers for creating platform-native installers:
- macOS: .pkg installers and .dmg disk images
- Windows: .msix and .msi installers (future)
- Linux: .deb and .rpm packages (future)

Usage:
    from pcons.contrib.installers import macos

    # Create a macOS .pkg installer
    pkg = macos.create_pkg(
        project, env,
        name="MyApp",
        version="1.0.0",
        identifier="com.example.myapp",
        sources=[app],
        install_location="/usr/local/bin",
    )

    # Create a macOS .dmg disk image
    dmg = macos.create_dmg(
        project, env,
        name="MyApp",
        sources=[app],
    )

Available modules:
    - macos: macOS .pkg and .dmg creation
    - windows: Windows MSIX and MSI creation (future)
    - linux: Linux .deb and .rpm creation (future)
"""

from __future__ import annotations


def list_modules() -> list[str]:
    """List available installer modules.

    Returns:
        List of module names in the installers package.
    """
    return ["macos", "windows"]  # Add "linux" when implemented
