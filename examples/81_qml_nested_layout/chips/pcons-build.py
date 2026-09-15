# SPDX-License-Identifier: MIT
"""A second QML module, declared from a subdirectory.

qml_files is relative to this script's directory, the way sources= is and
the way qt_add_qml_module resolves QML_FILES against
CMAKE_CURRENT_SOURCE_DIR. So "qml/Chip.qml" lands at
qrc:/qt/qml/PconsNested/Chips/qml/Chip.qml: no "chips/" in the resource
path, and moving this directory would not move a single QML URL.
"""

from pcons import context
from pcons.toolchains.qt import find_qt

project = context.current_project
env = project.default_environment

qt = find_qt(project, env, modules=["Qml"])

chips_ui = project.QtQmlModule(
    "chips_ui",
    env,
    uri="PconsNested.Chips",
    qml_files=["qml/Chip.qml"],
    link=[qt.Qml],
)
