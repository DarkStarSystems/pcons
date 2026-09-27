# SPDX-License-Identifier: MIT
"""Substitutions that are part of an argument rather than all of it.

`59_codegen_sources` covers the form of a code-generation rule: a built tool,
named with `tool=`, run over every input. This one is about the arguments,
where each marker sits inside a larger word:

- `--out=$TARGET` -- a flag welded to the path it names.
- `-i$SOURCES` -- attached to a form that expands to *several* paths,
  so it repeats: `-ione.txt -ithree.txt -itwo.txt`, not one `-i` on the
  first. bundler.c rejects a bare argument, so a wrong expansion fails the
  build instead of quietly bundling the wrong thing.
- `$$Revision$$` -- `$$` is one literal dollar, handed to the program
  untouched by ninja, make, and the shell. It is not a shell variable
  reference: build scripts are Python, so read the environment there, with
  os.environ, where pcons can see the value and record it.
"""

from pcons import Project

project = Project("command_substitution")

src_dir = project.root_dir / "src"
env = project.Environment(toolchain="c")

bundler = project.Program("bundler", env, sources=[src_dir / "bundler.c"])

# However many data files there happen to be -- the command doesn't care.
inputs = sorted(src_dir.glob("*.txt"))

bundle = env.Command(
    target=project.build_dir / "bundle.txt",
    tool=bundler,
    source=inputs,
    command="$TOOL --out=$TARGET --stamp=$$Revision$$ -i$SOURCES",
)
