# SPDX-License-Identifier: MIT
"""A library whose public header is generated.

``metrics.public.include_dirs`` reaches every target that links ``metrics``,
so the generator that fills that directory has to run before those targets
compile too, not only before ``metrics`` does. ``metrics.depends(limits)``
says it once and pcons carries it to the consumers, so ``app`` never has to
repeat what is really the library's business.

The same edge is what a build-time scanner of that directory inherits: a Qt
target that mocs headers found on its include path lists the directory, and
without the ordering it lists it while the generator is still writing, which
costs a second and a third build pass before the tree settles.

Usage:
  pcons
  ./build/app
"""

import sys
from pathlib import Path

from pcons import Project

project = Project("generated_public_headers")
env = project.Environment(toolchain="c")

gen_dir = Path(project.root_dir) / project.build_dir / "gen"

limits = env.Command(
    target="gen/metrics_limits.h",
    source="gen_limits.py",
    command=[sys.executable, "$SOURCE", "$TARGET"],
)

metrics = project.StaticLibrary("metrics", env, sources=["src/metrics.c"])
metrics.depends(limits)
metrics.public.include_dirs.append(gen_dir)

app = project.Program("app", env, sources=["app/main.c"])
app.link(metrics)
