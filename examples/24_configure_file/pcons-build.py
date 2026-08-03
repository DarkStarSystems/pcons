#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating configure checks and configure_file().

This example demonstrates:
- Probing the compiler with ToolChecks (header, type-size, and flag checks)
- Caching check results in the build dir so re-runs skip the probes
- Feeding check results into configure_file() to generate config.h
- CMake-style #cmakedefine and @VAR@ substitution
- Using the generated header in a C program
"""

from pcons import Configure, Project, configure_file
from pcons.configure import ToolChecks

project = Project("configure_file_example")
env = project.Environment(toolchain="c")

# Probe the configured compiler. Results are cached in the build dir,
# so repeat runs answer from the cache without invoking the compiler.
config = Configure(build_dir="build")
checks = ToolChecks(config, env, "cc")

have_stdint = checks.check_header("stdint.h").success  # everywhere
have_frobnicate = checks.check_header("frobnicate.h").success  # nowhere
ptr_size = checks.check_type_size("void*")

# Flag checks let a build enable options only where the compiler supports them
if checks.check_flag("-Wall").success:
    env.cc.flags += ["-Wall"]

config.save()  # persist check results for the next run

# Generate config.h from template at configure time
configure_file(
    "src/config.h.in",
    "build/config.h",
    {
        "VERSION": "1.2.3",
        "HAVE_STDINT_H": "1" if have_stdint else "",
        "HAVE_FROBNICATE_H": "1" if have_frobnicate else "",
        "SIZEOF_VOIDP": str(ptr_size),
    },
)

# Add build dir to include path so #include "config.h" works
env.cc.includes.append(project.build_dir)

project.Program("configure_demo", env, sources=["src/main.c"])
