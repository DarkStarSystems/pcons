#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A generator that has to run from the source root.

Build tools run from the build directory, and pcons writes every path in a
command relative to it. That is wrong for the generator here, which looks up
its input at the fixed relative path ``data/items.txt`` -- a shape that turns
up in any project ported from a build system that ran from the top of the
tree.

``cwd=`` moves the command, and moves its paths with it: ``$SOURCE`` and
``$TARGET`` come out relative to the working directory the command asked for,
so nothing else in the rule has to change. Writing ``cd .. &&`` into the
command instead would look equivalent and quietly break the
``write_if_different`` wrapper, whose two halves have to run in the same
directory (they would then restore nothing, and every downstream target would
rebuild on every run).

Touching data/items.txt below re-runs the generator; because it writes the
same bytes, the .o and the program are not rebuilt. test.toml asserts that.
"""

from pcons import Project

project = Project("command_cwd")
env = project.Environment(toolchain="c")

gen_dir = project.build_dir / "gen"

# The generator, built like anything else -- it lands in the build directory.
make_items = project.Program("make-items", env, sources=["src/make-items.c"])

items = env.Command(
    target=gen_dir / "items.c",
    source=[make_items],  # $SOURCE is the program we just built
    depends=["data/items.txt"],  # read by the tool, not named on its command line
    command="$SOURCE $TARGET",
    cwd=project.root_dir,  # where the tool expects to find data/items.txt
    # The tool rewrites its output every run, so without this a touched input
    # would recompile and relink everything downstream.
    write_if_different=True,
)

demo = project.Program("demo", env, sources=["src/main.c", str(gen_dir / "items.c")])
demo.depends(items)

project.Default(demo)
