# SPDX-License-Identifier: MIT
"""A generator this build compiles, run over however many inputs there are.

This is the normal form of a code-generation rule: a tool the build just
built, run over a variable number of data files. Two things make it work:

1. **`tool=` names the program.** `$TOOL` is written the way the shell running
   the build executes it: `./collate` for a POSIX shell, which looks a bare
   name up on `$PATH` rather than in the build directory, and a backslashed
   path for `cmd.exe`, which reads a `/` as a switch. The tool is also a
   dependency, so it's built first and a change to it reruns the rule.

2. **`$SOURCES`, not indices.** The number of `.def` files is a property of
   the project, not of this rule, so the command names all of them. Adding a
   file to `defs` changes nothing here.

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

generated = env.Command(
    target=gen_dir / "entries.c",
    tool=collate,
    source=def_files,
    command="$TOOL $TARGET $SOURCES",
    write_if_different=True,
)

demo = project.Program("demo", env, sources=["src/main.c", str(gen_dir / "entries.c")])
demo.depends(generated)

project.Default(demo)
