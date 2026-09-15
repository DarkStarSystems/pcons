# SPDX-License-Identifier: MIT
"""QML modules whose files sit in subdirectories.

Each qml_files entry is also the file's path inside the module resource,
the way qt_add_qml_module does it: "qml/pages/Detail.qml" is reachable at
qrc:/qt/qml/PconsNested/qml/pages/Detail.qml, and the qmldir names that
same path. Two files with one base name in different directories are a
hard error rather than one silently replacing the other.

The entries are relative to the declaring script's own directory, so the
second module in chips/ spells "qml/Chip.qml" and gets
qrc:/qt/qml/PconsNested/Chips/qml/Chip.qml. Where the build script sits
in the source tree is not part of the resource layout.

Every resource directory below the module root also gets a qmldir of its
own, holding one "prefer" line back to the root. Without it a file down
there resolves an unqualified type name against its own directory only:
qml/pages/Detail.qml names Badge, which lives in qml/widgets/, and the
application prints it. That line is the whole reason it can.
"""

from pcons import Project, add_subdirectory, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("qml_nested")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Qml"])

ui = project.QtQmlModule(
    "nested_ui",
    env,
    uri="PconsNested",
    qml_files=[
        "qml/Main.qml",
        "qml/pages/Detail.qml",
        "qml/widgets/Badge.qml",
    ],
    link=[qt.Qml],
)

chips = add_subdirectory("chips", env=env)

app = project.QtProgram("qml_nested", env, sources=["src/main.cpp"], link=[qt.Qml])
app.link(ui)
app.link(chips.chips_ui)
