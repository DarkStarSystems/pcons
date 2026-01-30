#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pcons"]
# ///
"""Example: Using Conan packages with pcons.

This example demonstrates how to use ConanFinder to find Conan packages
and apply their settings (includes, defines, link flags) to your build
environment using the simple env.use() API.

Requirements:
    - Conan 2.x installed (or available via uvx)

Usage:
    uvx pcons              # Generate and build (conan install runs automatically)
    ./build/hello_fmt      # Run the program
"""

import os
from pathlib import Path

from pcons import Generator, Project, find_c_toolchain, get_variant
from pcons.configure.config import Configure
from pcons.generators.compile_commands import CompileCommandsGenerator
from pcons.packages.finders import ConanFinder

# =============================================================================
# Configuration
# =============================================================================

VARIANT = get_variant("release")

project_dir = Path(os.environ.get("PCONS_SOURCE_DIR", Path(__file__).parent))
build_dir = Path(os.environ.get("PCONS_BUILD_DIR", project_dir / "build"))

# =============================================================================
# Setup
# =============================================================================

config = Configure(build_dir=build_dir)
toolchain = find_c_toolchain()

if not config.get("configured") or os.environ.get("PCONS_RECONFIGURE"):
    toolchain.configure(config)
    config.set("configured", True)
    config.save()

project = Project("conan_example", root_dir=project_dir, build_dir=build_dir)

# =============================================================================
# Find Conan packages
# =============================================================================

# Create finder - compiler version is auto-detected
conan = ConanFinder(
    config,
    conanfile=project_dir / "conanfile.txt",
    output_folder=build_dir / "conan",
)

# Sync profile with toolchain - this generates the Conan profile file
conan.sync_profile(toolchain, build_type=VARIANT.capitalize())

# Install packages - cmake_layout subfolders are auto-searched
packages = conan.install()

# Get fmt package
fmt_pkg = packages.get("fmt")
if not fmt_pkg:
    raise RuntimeError(
        "fmt package not found - try running:\n"
        "  conan install . --output-folder=build/conan --build=missing"
    )

# =============================================================================
# Environment Setup
# =============================================================================

env = project.Environment(toolchain=toolchain)
env.set_variant(VARIANT)
env.cxx.flags.append("-std=c++17")

# Use C++ compiler as linker for C++ programs (gets correct runtime libraries)
env.link.cmd = env.cxx.cmd

# =============================================================================
# Apply package settings - use env.use() for simple integration
# =============================================================================

# Apply all package settings (includes, defines, libs, etc.) with one call
env.use(fmt_pkg)

# =============================================================================
# Build target
# =============================================================================
hello = project.Program("hello_fmt", env)
hello.add_sources([project_dir / "src" / "main.cpp"])

project.Default(hello)

# =============================================================================
# Generate build files
# =============================================================================

project.resolve()

Generator().generate(project)
CompileCommandsGenerator().generate(project)

print(f"Generated {build_dir}")
