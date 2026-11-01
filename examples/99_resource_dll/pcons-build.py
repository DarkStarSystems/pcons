# SPDX-License-Identifier: MIT
"""A resource-only DLL: translated strings a program loads at run time.

A satellite DLL of strings has resources and no code, so it exports nothing,
and link.exe writes no import library for a DLL that exports nothing. Set
``import_library`` to False so the link declares only the DLL; otherwise
Ninja waits for an import library that never appears and relinks the DLL on
every build. With no code there's no entry point either, hence /NOENTRY.

Nothing can link against such a DLL, so the program loads it as data and
reads its string table.

Windows only.
"""

from pcons import Project

project = Project("resource_dll")
env = project.Environment(toolchain=["msvc", "clang-cl"])

strings = project.SharedLibrary("strings_fr", env, sources=["src/strings_fr.rc"])
strings.set_option("import_library", False)
strings.private.link_flags.append("/NOENTRY")

app = project.Program("greet", env, sources=["src/main.c"])
app.private.link_libs.append("user32")
app.depends(strings)
