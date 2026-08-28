# SPDX-License-Identifier: MIT
"""Environment variables for one command alone.

A tool that reads its configuration from the environment — a signing server
URL, a license key, a locale — needs that variable set for its own command
and no other. Setting it in the build script via ``os.environ`` does the
opposite: every command in the build sees it, and only when pcons itself runs
the generation, so a direct ``ninja`` run loses it.

``env_vars=`` writes the variables into the generated build file instead, in
front of the one command they belong to: ``env NAME=VALUE`` on POSIX, a small
pcons helper on Windows (which has no ``env``). Either back-end, either
platform, the variable arrives at that command and nowhere else.
"""

import sys

from pcons import Project

project = Project("command_env")
env = project.Environment()

python = sys.executable.replace("\\", "/")
# Writes what it sees: the value of GREETING, or "unset".
show = (
    "import os, pathlib, sys; "
    "pathlib.Path(sys.argv[1]).write_text(os.environ.get('GREETING', 'unset'))"
)

with_var = env.Command(
    name="with-var",
    target=project.build_dir / "with_var.txt",
    command=[python, "-c", show, "$TARGET"],
    env_vars={"GREETING": "from-env-vars"},
)

# The same command without env_vars= proves the variable travels with the
# one edge, not the environment or the build.
without_var = env.Command(
    name="without-var",
    target=project.build_dir / "without_var.txt",
    command=[python, "-c", show, "$TARGET"],
)

# Naming any default target replaces the automatic set, so both go here: a
# command target is not built unless something asks for it.
project.Default(with_var, without_var)
