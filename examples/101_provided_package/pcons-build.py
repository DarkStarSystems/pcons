# SPDX-License-Identifier: MIT
"""One vendored dependency answering another's find_package().

`third_party/zlite` and `third_party/pngish` are two vendored projects, and
pngish's own build script finds zlite with `project.find_package("zlite")`,
as it would find an installed copy. This build makes zlite from source, so
pngish should get that target, not look for one on the system.

`project.provide_package("zlite", ...)` says so: from then on, every
`find_package("zlite")` in the build returns the in-tree target, ahead of any
finder, and pngish's script stays as its authors wrote it. The `version=`
given here is checked against what pngish asks for (">=1.2").

It's CMake's `FetchContent` `OVERRIDE_FIND_PACKAGE`, or the `CACHE ... FORCE`
of a find module's variables that preceded it.
"""

from pcons import Project, add_subdirectory

project = Project("provided_package")
env = project.Environment(toolchain="c")

zlite = add_subdirectory("third_party/zlite")
project.provide_package("zlite", zlite.lib, version="1.3.1")
pngish = add_subdirectory("third_party/pngish")

app = project.Program("app", env, sources=["src/main.c"])
app.link(pngish.lib)
