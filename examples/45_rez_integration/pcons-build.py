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

from pcons import Project
from pcons.integrations.rez import is_in_rez_resolve, rez_environment

# Before the project, not after: a script that stops here describes no build,
# and pcons expects a script that describes one to run it.
if not is_in_rez_resolve():
    print(
        "Run this example inside a rez-env shell, e.g.:\n"
        "    rez-env hello_lib -- uvx pcons"
    )
    sys.exit(0)

project = Project("rez_demo")

env = project.Environment(toolchain="c")
env.cxx.flags.append("-std=c++17")
env.link.cmd = env.cxx.cmd

rez_environment(env)

app = project.Program("rez_demo", env, sources=["src/main.cpp"])
project.Default(app)
