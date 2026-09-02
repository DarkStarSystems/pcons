# SPDX-License-Identifier: MIT
"""Build script for libbar - a library nested two levels down.

Like libfoo, this builds either on its own or as part of a parent build.
Nothing here is written differently for the two cases: `project.root_dir`
and `project.build_dir` always refer to this directory and this library's
own build output, wherever it sits in a larger tree.

`env.Command` follows the same rule as `StaticLibrary`: every path below is
relative to this directory and means the same thing either way.
`gen/bar_version.c` is this script's own build directory, `gen_version.py`
and `version.txt` are the files next to this script. gen_version.py reads
version.txt itself, so the data file is named with `depends=` rather than
as a source. Embedded in the top-level build this script sits two levels
down, and the environment it takes from its parent belongs to the top-level
project, so the anchor has to be this script rather than that environment.
"""

import sys

from pcons import Project

project = Project("libbar")

if project.is_top_level:
    env = project.Environment(toolchain="c")
else:
    env = project.parent.default_environment

bar_version = env.Command(
    target="gen/bar_version.c",
    source="gen_version.py",
    command=[sys.executable, "$SOURCE", "$TARGET"],
    depends=["version.txt"],
)

libbar = project.StaticLibrary("bar", env, sources=["src/bar.c"])
libbar.add_sources([bar_version])
libbar.public.include_dirs.append("include")
