#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating Xcode project generation.

This example shows how to use the Xcode generator to create
an .xcodeproj that can be built with xcodebuild or opened in Xcode.

Usage:
    # Generate Xcode project
    pcons --generator=xcode

    # Then build with xcodebuild
    xcodebuild -project build/hello_xcode.xcodeproj -configuration Release

    # Or open in Xcode
    open build/hello_xcode.xcodeproj
"""

from pcons import Project, find_c_toolchain

# =============================================================================
# Build Script
# =============================================================================

# Find a C toolchain
toolchain = find_c_toolchain()

# Create project
project = Project("hello_xcode")

# Directories
src_dir = project.root_dir / "src"
build_dir = project.build_dir
env = project.Environment(toolchain=toolchain)
# Warning flags, resolved per-toolchain (/W4 on MSVC, -Wall … on GCC/Clang).
env.apply_preset("warnings")

# Create program target
hello = project.Program("hello", env)
hello.add_sources([src_dir / "main.c"])

project.generate()

print(f"Generated {build_dir}")
