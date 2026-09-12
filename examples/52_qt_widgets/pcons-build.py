# SPDX-License-Identifier: MIT
"""A Qt Widgets application, the high-level way.

QtProgram() accepts .ui and .qrc files directly in sources and finds
Q_OBJECT classes itself. Which files need moc is a fact about their
contents, so one visible automoc edge per target runs the scan and moc
while the build runs, behind a single mocs_compilation.cpp; uic and rcc
stay one plain ninja edge per file. Everything carries a depfile, so
incremental builds are exact and `ninja -t commands` shows what runs.
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
