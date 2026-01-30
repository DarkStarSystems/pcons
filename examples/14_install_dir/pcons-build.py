#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating InstallDir for recursive directory copying.

This example shows:
- Using project.InstallDir() to copy an entire directory tree
- The depfile mechanism for tracking source files

InstallDir uses ninja's depfile feature for incremental rebuilds:
if any file in the source directory changes, the copy is re-run.
"""

import os
from pathlib import Path

from pcons import Generator, Project

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent

# Create project (no toolchain needed for this example)
project = Project("install_dir", build_dir=build_dir)

# Install the assets directory to the build output
# This copies the entire 'assets' directory tree to 'build/dist/assets'
# Note: destination is relative to build_dir, so "dist" becomes "build/dist"
installed_assets = project.InstallDir("dist", src_dir / "assets")

# Set as default target
project.Default(installed_assets)

# Resolve all targets
project.resolve()

# Generate build file
generator = Generator()
generator.generate(project)

print(f"Generated {build_dir}")
