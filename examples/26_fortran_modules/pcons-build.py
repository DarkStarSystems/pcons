#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a Fortran program with modules.

This example demonstrates:
- Selecting a Fortran toolchain by name: toolchain="fortran"
- Building a Fortran program that uses a Fortran MODULE
- Ninja dyndep for correct module dependency ordering
"""

from pcons import Project

project = Project("fortran_modules")
env = project.Environment(toolchain="fortran")

project.Program("hello", env, sources=["src/greetings.f90", "src/main.f90"])
