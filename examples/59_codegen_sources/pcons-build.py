#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A generator this build compiles, run over however many inputs there are.

This is the normal shape of a code-generation rule: one `source=` list holding
*a tool the build just built* followed by *a variable number of data files*.
Two things have to hold for it to work, and both are easy to get wrong:

1. **Declared order is preserved.** `${SOURCES[0]}` is the tool because the
   tool was written first — not whichever input happened to be a plain path.
   (pcons used to append Target sources after path sources, so `${SOURCES[0]}`
   was a `.def` file and the build tried to execute it.)

2. **Slices.** The number of `.def` files is a property of the project, not of
   this rule, so the command says "the rest of them" rather than listing
   indices: `${SOURCES[1:]}`. Adding a file to `defs` changes nothing here.

Any `${...}` pcons doesn't recognize is an error rather than a literal passed
through to the build tool.
"""

from pcons import Project

project = Project("codegen_sources")

env = project.Environment(toolchain="c")
gen_dir = project.build_dir / "gen"

# The generator, built like anything else.
collate = project.Program("collate", env, sources=["src/collate.c"])

# Every .def file in the project, however many that is. A glob is a question
# asked at configure time, so the directory it read has to be a configure
# dependency -- otherwise adding a .def file changes nothing until something
# else happens to re-run pcons.
def_dir = project.root_dir / "defs"
project.add_configure_dependency(def_dir)
def_files = sorted(p.relative_to(project.root_dir) for p in def_dir.glob("*.def"))

# ${SOURCES[0]} is the tool; ${SOURCES[1:]} is every .def file.
generated = env.Command(
    target=gen_dir / "entries.c",
    source=[collate, *def_files],
    command="${SOURCES[0]} $TARGET ${SOURCES[1:]}",
    name="collate_defs",
    write_if_different=True,
)

demo = project.Program("demo", env, sources=["src/main.c", str(gen_dir / "entries.c")])
demo.depends(generated)

project.Default(demo)
