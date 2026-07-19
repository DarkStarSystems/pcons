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

from pcons import Project

# =============================================================================
# Build Script
# =============================================================================


# Create project
project = Project("archive_install")

# Directories
src_dir = project.root_dir
env = project.Environment(toolchain="c")
# Warning flags, resolved per-toolchain (/W4 on MSVC, -Wall … on GCC/Clang).
env.apply_preset("warnings")

# Build hello program using target-centric API
hello = project.Program("hello", env)
hello.add_sources([src_dir / "hello.c"])

# Set as default target
project.Default(hello)

# --- Installer targets (not built by default) ---

# Tarball of source files and headers
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
install_target = project.Install(
    project.root_dir / "Installers",
    [src_tarball, bin_tarball],
    name="install-tarballs",
)

project.Alias("install", install_target)
