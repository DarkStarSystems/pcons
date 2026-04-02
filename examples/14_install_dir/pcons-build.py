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
# This copies the entire 'assets' directory tree to 'build/dist/assets'
# Note: destination is relative to build_dir, so "dist" becomes "build/dist"
installed_assets = project.InstallDir("dist", src_dir / "assets")

# Set as default target
project.Default(installed_assets)

project.generate()

print(f"Generated {build_dir}")
