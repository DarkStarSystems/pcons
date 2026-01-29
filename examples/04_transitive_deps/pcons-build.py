#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example: multi-source program with shared headers.

This example shows:
- Compiling multiple source files into a single program
- Using private.include_dirs for shared headers
"""

import os

from pcons import Generator, Project, find_c_toolchain

project = Project(
    "transitive_deps", build_dir=os.environ.get("PCONS_BUILD_DIR", "build")
)
env = project.Environment(toolchain=find_c_toolchain())

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

Generator().generate(project)
