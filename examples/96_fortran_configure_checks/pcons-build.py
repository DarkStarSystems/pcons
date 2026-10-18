# SPDX-License-Identifier: MIT
"""Build script for a Fortran program configured by probing its compiler.

This example demonstrates:
- Keeping only the flags this Fortran compiler accepts, like Meson's
  get_supported_arguments(): each compiler has its own spelling
- Compiling a test program to find out what the compiler supports, and
  passing the answer to the source as a define
"""

from pcons import Configure, Project
from pcons.configure.checks import ToolChecks

project = Project("fortran_configure_checks")
env = project.Environment(toolchain="fortran")

config = Configure(build_dir=project.build_dir)
checks = ToolChecks(config, env, "fc")

# A backtrace on a runtime error: gfortran's -fbacktrace, ifx's -traceback.
for flag in ("-fbacktrace", "-traceback"):
    if checks.check_flag(flag).success:
        env.fc.flags.append(flag)

# Quad precision isn't on every compiler and target.
quad = checks.try_compile(
    """
    integer, parameter :: qp = selected_real_kind(33)
    real(qp) :: x
    end
    """
)
env.fc.defines.append(f"WITH_QP={int(quad.success)}")
config.save()

project.Program("precision", env, sources=["src/main.F90"])
