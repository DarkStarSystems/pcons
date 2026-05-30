#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating InstallDir for recursive directory copying.

This example shows:
- Using project.InstallDir() to copy an entire directory tree
- The depfile mechanism for tracking source files

InstallDir uses ninja's depfile feature for incremental rebuilds:
if any file in the source directory changes, the copy is re-run.
"""

from pcons import Project

# =============================================================================
# Build Script
# =============================================================================

# Create project (no toolchain needed for this example)
project = Project("install_dir")

# Directories
src_dir = project.root_dir
build_dir = project.build_dir

# Install the assets directory to the build output
# This copies the entire 'assets' directory tree to 'PCONS_INSTALL_PREFIX/assets'
installed_assets = project.InstallDir(".", src_dir / "assets")

# Set as default target
project.Default(installed_assets)

# Resolve so output_nodes are populated for Alias
project.resolve()

# Create alias after resolve() so output_nodes are populated
project.Alias("install", installed_assets)

project.generate()

print(f"Generated {build_dir}")
