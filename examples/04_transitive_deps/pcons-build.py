#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example: multi-source program with shared headers.

This example shows:
- Compiling multiple source files into a single program
- Using private.include_dirs for shared headers
"""

from pcons import Project

project = Project("transitive_deps")
env = project.Environment(toolchain="c")

simulator = project.Program(
    "simulator",
    env,
    sources=[
        "src/math_lib.c",
        "src/physics_lib.c",
        "src/main.c",
    ],
)
simulator.private.include_dirs.append("include")
