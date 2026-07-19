#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates:
- Using find_c_toolchain() to automatically select a compiler
- Creating a Program target with sources
- Automatic resolution and generation
"""

from pcons import Project, find_c_toolchain, get_var, install_dir

project = Project("hello_c")
if (preferred_toolchain := get_var("TOOLCHAIN", None)) is not None:
    preferred_toolchain = [preferred_toolchain]
else:
    preferred_toolchain = None
env = project.Environment(toolchain=find_c_toolchain(prefer=preferred_toolchain))

hello = project.Program("hello", env, sources=["src/hello.c"])

# Install target: copy binary to $PCONS_INSTALL_PREFIX/bin directory.
# install_dir() picks the conventional subdir ("bin") from the toolchain.
bins = project.Install(install_dir(env, "program"), [hello], name="install-hello")

# "ninja install" (or "make install") runs the install target
project.Alias("install", bins)
