#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a mixed Fortran + C++ program (Fortran primary).

This example demonstrates:
- Fortran as the primary toolchain (gfortran drives the link)
- Calling a C++ function from Fortran via BIND(C)
- Automatic C++ runtime injection (-lc++ / -lstdc++)
"""

import os

from pcons import Generator, Project, find_c_toolchain, find_fortran_toolchain

project = Project(
    "fortran_calls_cxx", build_dir=os.environ.get("PCONS_BUILD_DIR", "build")
)

# Fortran is primary - gfortran will drive the link.
# The C/C++ toolchain is added as secondary to compile the C++ source.
env = project.Environment(toolchain=find_fortran_toolchain())
env.add_toolchain(find_c_toolchain())  # gcc/clang for C++ compilation

project.Program("hello", env, sources=["src/main.f90", "src/greet.cpp"])

Generator().generate(project)
