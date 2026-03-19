#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a Fortran program with modules.

This example demonstrates:
- Using find_fortran_toolchain() to automatically select gfortran
- Building a Fortran program that uses a Fortran MODULE
- Ninja dyndep for correct module dependency ordering
"""

import os

from pcons import Generator, Project, find_fortran_toolchain

project = Project(
    "fortran_modules", build_dir=os.environ.get("PCONS_BUILD_DIR", "build")
)
env = project.Environment(toolchain=find_fortran_toolchain())

project.Program("hello", env, sources=["src/greetings.f90", "src/main.f90"])

Generator().generate(project)
