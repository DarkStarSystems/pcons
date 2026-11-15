# SPDX-License-Identifier: MIT
"""Build script for app - built as part of the parent project.

This demonstrates a subdir that depends on a sibling subdir (libfoo). It has
no standalone path of its own, unlike libfoo: a project is rooted at the
directory its build script sits in, and libfoo is outside app's.

libfoo arrives from the parent, which includes both directories and passes
the target down with `add_subdirectory("app", imports={"libfoo": ...})`.
That is the route for anything a directory needs from its parent or from a
sibling; the other way round, what a parent wants from what a subdirectory
made, is `add_subdirectory()`'s return value.

Its own include directory goes on its own copy of the parent's environment.
Like `sources=`, `env.cc.includes.append("include")` is read from this
script's directory, though the environment was made in the parent.
"""

from pcons import context

project = context.current_project
# A clone, so app_config.h is visible to app alone.
env = project.default_environment.clone()
env.cc.includes.append("include")

libfoo = project.imports["libfoo"]

app = project.Program("app", env)
app.add_sources(["src/main.c"])
app.link(libfoo)
