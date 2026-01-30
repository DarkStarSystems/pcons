#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating platform-specific installer creation.

This example shows how to create:
- macOS: .pkg installer and .dmg disk image
- Windows: .msix package

The installer targets are created based on the current platform.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain
from pcons.contrib import platform

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent / "src"

# Find a C toolchain
toolchain = find_c_toolchain()

# Create project
project = Project("installer_example", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Build the application
app = project.Program("hello", env)
app.add_sources([src_dir / "main.c"])

# Create platform-specific installers
installer_targets = []

if platform.is_macos():
    from pcons.contrib.installers import macos

    # Create a .pkg installer
    pkg = macos.create_pkg(
        project,
        env,
        name="HelloApp",
        version="1.0.0",
        identifier="com.example.hello",
        sources=[app],
        install_location="/usr/local/bin",
        min_os_version="10.13",
    )
    installer_targets.append(pkg)

    # Create a .dmg disk image
    dmg = macos.create_dmg(
        project,
        env,
        name="HelloApp",
        sources=[app],
        applications_symlink=False,  # No symlink needed for CLI tools
    )
    installer_targets.append(dmg)

elif platform.is_windows():
    from pcons.contrib.installers import windows

    # Create an MSIX package
    msix = windows.create_msix(
        project,
        env,
        name="HelloApp",
        version="1.0.0.0",
        publisher="CN=Example Publisher",
        sources=[app],
        display_name="Hello App",
        description="A simple hello world application",
    )
    installer_targets.append(msix)

# Create an alias for building all installers (if any)
if installer_targets:
    project.Alias("installers", *installer_targets)

# Set the app as the default target
project.Default(app)

# Resolve and generate
project.resolve()

generator = Generator()
generator.generate(project)

print(f"Generated {build_dir}")
if installer_targets:
    print("Run 'ninja installers' to build installer packages")
