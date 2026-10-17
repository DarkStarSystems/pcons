# SPDX-License-Identifier: MIT
"""Build script for a Fortran program that includes files.

This example demonstrates:
- A preprocessed source (.F90) using #include, found beside the source
- Fortran's own INCLUDE line, found on the include path
- Editing an included file recompiles exactly the sources that include it
"""

from pcons import Project

project = Project("fortran_includes")
env = project.Environment(toolchain="fortran")
env.fc.includes.append(project.root_dir / "include")

project.Program("hello", env, sources=["src/greetings.f90", "src/main.F90"])
