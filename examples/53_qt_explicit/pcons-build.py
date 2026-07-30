#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Qt code generation with the explicit low-level builders.

This example drives moc and rcc by hand — the Meson-style API. It shows
exactly what the high-level QtProgram() wrapper automates:

- env.qt.Moc(sources="counter.h")  -> moc_counter.cpp (compiled as a TU)
- env.qt.Moc(sources="main.cpp")   -> main.moc (#included by main.cpp)
- env.qt.Rcc(sources="messages.qrc") -> qrc_messages.cpp (Q_INIT_RESOURCE)

Every generated file is a plain, visible ninja edge with a depfile:
`ninja -t commands` shows each moc invocation, and touching any header
that a moc'ed file includes re-runs exactly the affected edges.
"""

from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("qt_explicit")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)  # Qt 6 requires C++17 or later

qt = find_qt(project, env, modules=["Core"])

# moc must see Qt's include paths and defines to parse headers correctly.
# (QtProgram fills these automatically; here we do it by hand.)
env.qt.mocincludes = [str(p) for p in qt.Core.public.include_dirs]
env.qt.mocdefines = list(qt.Core.public.defines)

moc_counter = env.qt.Moc(sources="src/counter.h")
main_moc = env.qt.Moc(sources="src/main.cpp")
resources = env.qt.Rcc(sources="messages.qrc", name="messages")

# main.cpp does `#include "main.moc"`, so its compile needs the generated
# file (dependency) and its directory on the include path.
main_cpp = project.node("src/main.cpp")
main_cpp.depends(main_moc)
env.cxx.includes.append(str(project.build_dir / "qt.gen" / "src"))

app = project.Program(
    "qt_explicit",
    env,
    sources=[main_cpp, "src/counter.cpp", moc_counter[0], resources[0]],
)
app.link(qt.Core)
