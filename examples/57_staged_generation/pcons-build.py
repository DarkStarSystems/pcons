#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Staged generation: the build discovers what to build, mid-build.

The plugin set here is data that only a *compiled program* can produce, so it
isn't known when the build description first runs. Real projects hit this
whenever a definition language, an IDL, or a schema decides the target list.

pcons describes the graph up front and never creates targets during the build,
so it stages instead:

  pass 1  describe what produces the manifest (compile list-plugins, run it)
          ↓  ninja builds those, then re-runs pcons because build.ninja
             declares the manifest as one of its own inputs
  pass 2  the manifest exists, so ``when_generated`` fires and the per-plugin
          targets join the graph; ninja reloads and builds them

All of it happens inside one ``ninja`` invocation, from a clean tree.

Two pieces make it work:
  * the self-regeneration edge every generated build file now carries, whose
    inputs include anything the build script read (``generated_input`` /
    ``add_configure_dependency``);
  * ``write_if_different=True``, so a generator that rewrites all its outputs
    every run doesn't invalidate everything downstream of them.
"""

import sys
from pathlib import Path

from pcons import Project

project = Project("staged_generation")
env = project.Environment(toolchain="c")

python = sys.executable.replace("\\", "/")
gen_dir = project.build_dir / "gen"
plugins_list = gen_dir / "plugins-list.txt"

# --- Stage 1: build the tool that decides what the plugins are -------------

lister = project.Program("list-plugins", env, sources=["src/list-plugins.c"])

manifest = env.Command(
    target=plugins_list,
    source=[lister],  # $SOURCE is the program we just built
    depends=["plugins.def"],
    command="$SOURCE $SRCDIR/plugins.def $TARGET",
    write_if_different=True,
)

# --- Stage 2: everything the manifest decides ------------------------------
# The block runs only once the manifest exists. Either way pcons records it as
# an input of build.ninja, so the build system re-runs pcons as soon as the
# manifest appears or changes -- no second command to remember.


@project.when_generated(plugins_list)
def _plugins(manifest_path: Path) -> None:
    names = manifest_path.read_text().split()
    generated = [gen_dir / f"S_{name}.c" for name in names]

    sources = env.Command(
        target=[*generated, gen_dir / "plugins.h"],
        source=[plugins_list],
        depends=["src/gen-plugins.py"],
        command=f"{python} $SRCDIR/src/gen-plugins.py $SOURCE",
        name="gen_plugin_sources",
        # The generator rewrites every file every run. Without this, adding
        # one plugin would recompile all of them.
        write_if_different=True,
    )

    # The generated sources are outputs of the command above, so ninja
    # already knows to run it first. Only main.c needs to be told about the
    # generated header -- per source, so that regenerating the registry
    # doesn't recompile plugins that didn't change.
    main = project.node("src/main.c")
    main.depends(sources.output_nodes[-1])  # gen/plugins.h

    demo = project.Program(
        "demo", env, sources=["src/main.c", *(str(p) for p in generated)]
    )
    # Private, not on the env: an env-wide include would change the compile
    # line of every other target too, including ones built in pass 1.
    demo.private.include_dirs.append(gen_dir)
    project.Default(demo)


project.Default(manifest)
