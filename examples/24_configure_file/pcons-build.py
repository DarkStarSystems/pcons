#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build script demonstrating configure_file().

This example demonstrates:
- Using configure_file() to generate a config.h from a template
- CMake-style #cmakedefine and @VAR@ substitution
- Using the generated header in a C program
"""

from pcons import Project, configure_file

project = Project("configure_file_example")
env = project.Environment(toolchain="c")

# Generate config.h from template at configure time
configure_file(
    "src/config.h.in",
    "build/config.h",
    {"VERSION": "1.2.3", "HAVE_THREADS": "1"},
)

# Add build dir to include path so #include "config.h" works
env.cc.flags += ["-Ibuild"]

project.Program("configure_demo", env, sources=["src/main.c"])
