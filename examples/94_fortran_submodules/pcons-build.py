# SPDX-License-Identifier: MIT
"""Build script for a Fortran program whose module is implemented in
submodules.

This example demonstrates:
- A module declaring its procedures, with their bodies in a submodule
- A submodule of that submodule
- Compiles ordered by what each file declares, whatever order the sources
  are listed in
"""

from pcons import Project

project = Project("fortran_submodules")
env = project.Environment(toolchain="fortran")

project.Program(
    "circles",
    env,
    sources=[
        "src/circle_format.f90",
        "src/circle_math.f90",
        "src/main.f90",
        "src/circle.f90",
    ],
)
