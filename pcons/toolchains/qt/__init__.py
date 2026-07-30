# SPDX-License-Identifier: MIT
"""Qt support for pcons.

Discovery (:func:`find_qt`), the ``qt`` toolchain (moc/uic/rcc tools and
builders), and high-level Qt target builders.

Example:
    from pcons.toolchains.qt import find_qt

    qt = find_qt(project, env, modules=["Widgets"])
    app = project.QtProgram("myapp", env,
        sources=["main.cpp", "mainwindow.cpp", "mainwindow.ui", "icons.qrc"],
        link=[qt.Widgets])
"""

from pcons.toolchains.qt import builders as _builders  # registers Qt* builders
from pcons.toolchains.qt.finder import QtNotFoundError, QtPackage, find_qt
from pcons.toolchains.qt.scan import MocIncludeError
from pcons.toolchains.qt.toolchain import QtTool, QtToolchain, find_qt_toolchain

del _builders

__all__ = [
    "MocIncludeError",
    "QtNotFoundError",
    "QtPackage",
    "QtTool",
    "QtToolchain",
    "find_qt",
    "find_qt_toolchain",
]
