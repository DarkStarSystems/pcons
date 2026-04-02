#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script for a simple Fortran program.

This example demonstrates:
- Using find_fortran_toolchain() to automatically select gfortran
- Creating a Program target with a Fortran source file
"""

from pcons import Generator, Project, find_fortran_toolchain

project = Project("fortran_hello")
env = project.Environment(toolchain=find_fortran_toolchain())

project.Program("hello", env, sources=["src/hello.f90"])

Generator().generate(project)
