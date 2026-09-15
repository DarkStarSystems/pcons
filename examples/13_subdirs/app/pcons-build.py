# SPDX-License-Identifier: MIT
"""Build script for app - built as part of the parent project.

This demonstrates a subdir that depends on a sibling subdir (libfoo). It has
no standalone path of its own, unlike libfoo: a project is rooted at the
directory its build script sits in, and libfoo is outside app's.

Its own include directory is named absolutely, through `project.current_dir`,
where libfoo and libbar name theirs relatively. Both spellings mean this
script's own directory and both have to survive the trip into the top-level
build, which reaches a subdirectory script through `$topdir` and its offset.
"""

from pcons import context

project = context.current_project
env = project.default_environment

libfoo = project.get_target("libfoo::foo")
assert libfoo is not None, (
    "libfoo target not found - ensure libfoo's pcons-build.py is run first"
)

app = project.Program("app", env)
app.add_sources(["src/main.c"])
app.private.include_dirs.append(project.current_dir / "include")
app.link(libfoo)
