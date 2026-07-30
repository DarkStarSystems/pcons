#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A Qt Widgets application, the high-level way.

QtProgram() accepts .ui and .qrc files directly in sources and finds
Q_OBJECT classes itself (automoc, scanned when pcons generates — never
at build time). Every generated file is an ordinary, visible ninja edge
with a depfile, so incremental builds are exact and `ninja -t commands`
shows precisely what runs. There is no equivalent of CMake's opaque
<target>_autogen step or its mocs_compilation.cpp aggregate TU.
"""

from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("qt_widgets")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)  # Qt 6 requires C++17 or later

qt = find_qt(project, env, modules=["Widgets"])

app = project.QtProgram(
    "qt_widgets",
    env,
    sources=[
        "src/main.cpp",
        "src/mainwindow.cpp",
        "src/mainwindow.ui",
        "resources.qrc",
    ],
    link=[qt.Widgets],
)
