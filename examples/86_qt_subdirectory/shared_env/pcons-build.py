# SPDX-License-Identifier: MIT
"""A subdirectory that builds in the enclosing project's environment.

The Project is its own, so its targets and generated files get their own
build directory, but the toolchain, the flags and the located Qt install
all come from the parent. Sources are read from this script's directory.
"""

from pcons import Project
from pcons.toolchains.qt import find_qt

project = Project("shared_env")
env = project.parent.default_environment

# The install the top-level script located: find_qt() looks up the tree.
qt = find_qt(project, env, modules=["Core"])

project.QtProgram(
    "shared_env_app",
    env,
    sources=["src/main.cpp", "src/greeter.h"],
    link=[qt.Core],
)
