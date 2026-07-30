#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A QML application with C++ types, the high-level way.

QtQmlModule() bundles QML files and QML_ELEMENT C++ classes into a
module the engine can load by URI: it runs moc with JSON output, feeds
qmltyperegistrar, synthesizes the qmldir, and embeds everything under
:/qt/qml/<uri>/ — the boilerplate qt_add_qml_module hides in CMake,
without the backing-target/plugin-target confusion.
"""

from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("qt_qml")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Qml"])

ui = project.QtQmlModule(
    "demo_ui",
    env,
    uri="PconsDemo",
    qml_files=["qml/Main.qml"],
    sources=["src/backend.cpp"],
    link=[qt.Qml],
)

app = project.QtProgram("qt_qml", env, sources=["src/main.cpp"], link=[qt.Qml])
app.link(ui)
