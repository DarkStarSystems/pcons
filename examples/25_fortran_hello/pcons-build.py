#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple Fortran program.

This example demonstrates:
- Selecting a Fortran toolchain by name: toolchain="fortran"
- Creating a Program target with a Fortran source file
"""

from pcons import Project

project = Project("fortran_hello")
env = project.Environment(toolchain="fortran")

project.Program("hello", env, sources=["src/hello.f90"])
