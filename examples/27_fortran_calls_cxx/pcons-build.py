# SPDX-License-Identifier: MIT
"""Build script for a mixed Fortran + C++ program (Fortran primary).

This example demonstrates:
- Fortran as the primary toolchain, with a C/C++ toolchain added
- Calling a C++ function from Fortran via BIND(C)
- Choosing the link language: a program with C++ objects links with the
  C++ driver unless it says otherwise, and this one's main is Fortran
- Automatic C++ runtime injection (-lc++ / -lstdc++)
"""

from pcons import Project

project = Project("fortran_calls_cxx")

env = project.Environment(toolchain="fortran")
env.add_toolchain("c")  # gcc/clang for C++ compilation

hello = project.Program("hello", env, sources=["src/main.f90", "src/greet.cpp"])
# The main program is Fortran, so the Fortran driver links it, and the C++
# toolchain adds the C++ runtime its objects need. gfortran and flang would
# work either way, since they put main in the program's object; ifx keeps
# main in its runtime, which only its own driver links.
hello.link_language = "fortran"
