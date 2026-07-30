#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Qt translations: compile .ts catalogs with lrelease and embed them.

QtTranslations() compiles each catalog to a binary .qm and embeds it
under :/i18n/. Refreshing the catalogs from sources is `ninja lupdate`
— a utility target that is deliberately never part of the default build
(it writes into the source tree).
"""

from pcons import Project, find_c_toolchain
from pcons.toolchains.qt import find_qt

project = Project("qt_translations")
env = project.Environment(toolchain=find_c_toolchain())
env.cxx.set_standard(17)

qt = find_qt(project, env, modules=["Core"])

tr = project.QtTranslations(
    "i18n",
    env,
    ts_files=["i18n/app_de.ts"],
    lupdate_sources=["src/main.cpp"],
)

app = project.QtProgram(
    "qt_translations", env, sources=["src/main.cpp"], link=[qt.Core]
)
app.link(tr)
