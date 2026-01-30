#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating archive builders and installers.

This example shows:
- Building a C program using the target-centric API
- Creating tar archives with project.Tarfile()
- Installing archives with project.Install()
- Creating build aliases with project.Alias()

The 'install' target creates source and binary tarballs and copies them
to the Installers/ directory.
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Directories
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", "build"))
src_dir = Path(__file__).parent

# Find a C toolchain
toolchain = find_c_toolchain()

# Create project
project = Project("archive_install", build_dir=build_dir)
env = project.Environment(toolchain=toolchain)

# Build hello program using target-centric API
hello = project.Program("hello", env)
hello.add_sources([src_dir / "hello.c"])

# Add warning flags
if toolchain.name in ("msvc", "clang-cl"):
    hello.private.compile_flags.extend(["/W4"])
else:
    hello.private.compile_flags.extend(["-Wall", "-Wextra"])

# Set as default target
project.Default(hello)

# --- Installer targets (not built by default) ---

# Tarball of source files and headers
# Note: output paths are relative to build_dir (consistent with Install, InstallDir)
src_tarball = project.Tarfile(
    env,
    output="hello-src.tar.gz",
    sources=[src_dir / "hello.c", src_dir / "hello.h"],
    compression="gzip",
)

# Tarball of the built binary (pass the Target - sources are resolved later)
bin_tarball = project.Tarfile(
    env,
    output="hello-bin.tar.gz",
    sources=[hello],
    compression="gzip",
)

# Install target: copy tarballs to ./Installers directory
install_target = project.Install("Installers", [src_tarball, bin_tarball])

# Resolve all targets
project.resolve()

# Create alias after resolve() so output_nodes are populated
project.Alias("install", install_target)

# Generate build file
generator = Generator()
generator.generate(project)

print(f"Generated {build_dir}")
