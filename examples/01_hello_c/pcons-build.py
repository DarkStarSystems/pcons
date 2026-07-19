#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple C program.

This example demonstrates:
- Selecting a C/C++ toolchain by name (toolchain="c" auto-detects;
  pcons TOOLCHAIN=gcc requires a specific one)
- Creating a Program target with sources
- Automatic resolution and generation
"""

from pcons import Project, get_var, install_dir

project = Project("hello_c")
env = project.Environment(toolchain=get_var("TOOLCHAIN", "c"))

hello = project.Program("hello", env, sources=["src/hello.c"])

# Install target: copy binary to $PCONS_INSTALL_PREFIX/bin directory.
# install_dir() picks the conventional subdir ("bin") from the toolchain.
bins = project.Install(install_dir(env, "program"), [hello], name="install-hello")

# "ninja install" (or "make install") runs the install target
project.Alias("install", bins)
