#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a mixed C++ + Fortran program (C++ primary).

This example demonstrates:
- C++ as the primary toolchain (clang++/g++ drives the link)
- Calling a Fortran subroutine from C++ via BIND(C)
- Automatic Fortran runtime injection (-lgfortran)
"""

from pcons import Project

project = Project("cxx_calls_fortran")

# C++ is primary - g++/clang++ will drive the link.
# The Fortran toolchain is added as secondary to compile the Fortran source.
env = project.Environment(toolchain="c++")
env.add_toolchain("fortran")  # gfortran for Fortran compilation

project.Program("hello", env, sources=["src/main.cpp", "src/math_utils.f90"])
