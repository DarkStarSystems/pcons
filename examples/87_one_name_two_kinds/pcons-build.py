# SPDX-License-Identifier: MIT
"""One name for two kinds of target, and an exact output filename.

A program and a shared library may both be called ``smudge``: they write
``smudge`` and ``libsmudge.so``, which never collide, and their objects go in
``obj.smudge/`` and ``obj.smudge.shared/``: the directory carries the kind of
target that owns it. Both compile ``src/kind.c``, each with its own define, so
the two objects are genuinely different files.

The library is a plugin, and the host it loads into dictates the filename.
``output_filename`` says it in one line: no ``lib`` prefix, no platform
suffix, exactly ``smudge.ofx``.
"""

from pcons import Project

project = Project("one_name_two_kinds")

src_dir = project.root_dir / "src"
env = project.Environment(toolchain="c")

app = project.Program("smudge", env, sources=[src_dir / "main.c", src_dir / "kind.c"])
app.private.defines.append("KIND=1")

plugin = project.SharedLibrary(
    "smudge", env, sources=[src_dir / "plugin.c", src_dir / "kind.c"]
)
plugin.private.defines.append("KIND=2")
plugin.output_filename = "smudge.ofx"

# Two targets of one name, so no name selects one: keep the Target the
# builder returned, as every line above does.
project.Default(app, plugin)
