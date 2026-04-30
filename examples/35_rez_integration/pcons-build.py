#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example: build a C++ program against a rez-resolved package.

Pcons reads the active rez resolve and picks up every resolved package's
include/lib settings — no per-package plumbing in the build script.

Run with:

    rez-env hello_lib -- uvx pcons
    ./build/rez_demo

The reverse direction — rez driving pcons via the ``pcons`` build_system
plugin — lives under ``rez_packages/hello_app/``.
"""

import sys

from pcons import Generator, Project, find_c_toolchain
from pcons.integrations.rez import is_in_rez_resolve, rez_environment

project = Project("rez_demo")

env = project.Environment(toolchain=find_c_toolchain())
env.cxx.flags.append("-std=c++17")
env.link.cmd = env.cxx.cmd

if is_in_rez_resolve():
    rez_environment(env)
else:
    print(
        "Run this example inside a rez-env shell, e.g.:\n"
        "    rez-env hello_lib -- uvx pcons"
    )
    sys.exit(0)

app = project.Program("rez_demo", env, sources=["src/main.cpp"])
project.Default(app)

Generator().generate(project)
