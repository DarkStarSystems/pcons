# SPDX-License-Identifier: MIT
"""One directory, described once, built once per environment.

Nothing here names an environment. The script asks its parent for the default
one, and the parent's ``add_subdirectory("parity", env=...)`` decides what that
answers. Including this directory twice, once per environment, builds it twice:

    build/host/parity/lib/libparity.a
    build/strict/parity/lib/libparity.a

Built on its own it makes its own environment, like any other subdirectory.

The stamp below shows the other half of the pattern: a generator declared here
once per inclusion, and named, so the parent can ask for `parity::stamp@host`
without holding on to what this script returned.
"""

import sys

from pcons import Project

project = Project("parity")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.parent.default_environment

parity = project.StaticLibrary("parity", env, sources=["src/parity.c"])
parity.public.include_dirs.append("src")

# `name=` is optional on `Command`, and giving one makes the target findable:
# `get_target()`, `Default()` and `pcons build` all answer to it. The two
# inclusions both write "stamp", told apart by their environments, exactly as
# `checksum@host` and `checksum@strict` are.
python = sys.executable.replace("\\", "/")
write_line = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])"
stamp = env.Command(
    target="stamp.txt",
    command=[python, "-c", write_line, "$TARGET", env.name or "standalone"],
    name="stamp",
)
