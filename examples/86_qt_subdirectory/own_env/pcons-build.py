# SPDX-License-Identifier: MIT
"""A subdirectory that brings its own Project, Environment and Qt.

Standalone subdir Qt project. No refs to the enclosing project,
so this script also builds on its own (``cd own_env && pcons``).
find_qt() caches the install it locates on this project;
Qt children get their own.
"""

from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("own_env")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Core"])

project.QtProgram(
    "own_env_app",
    env,
    sources=["src/main.cpp", "src/counter.h"],
    link=[qt.Core],
)
