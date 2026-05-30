#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates:
- Using find_c_toolchain() to automatically select a compiler
- Creating a Program target with sources
- Automatic resolution and generation
"""

from pcons import Project, find_c_toolchain, get_var

project = Project("hello_c")
if (preferred_toolchain := get_var("TOOLCHAIN")) is not None:
    preferred_toolchain = [preferred_toolchain]
else:
    preferred_toolchain = None
env = project.Environment(toolchain=find_c_toolchain(prefer=preferred_toolchain))

hello = project.Program("hello", env, sources=["src/hello.c"])

# Install target: copy binary to $PCONS_INSTALL_PREFIX/bin directory
bins = project.Install("bin", [hello])

# Resolve so output_nodes are populated for Alias
project.resolve()

# Create alias after resolve() so output_nodes are populated
project.Alias("install", bins)

project.generate()
