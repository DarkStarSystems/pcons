# SPDX-License-Identifier: MIT
"""Running clang-tidy with every compile.

``env.use_clang_tidy()`` runs clang-tidy on each C and C++ source with that
compile's own flags, then compiles it. The checks come from the ``.clang-tidy``
file beside the sources, which clang-tidy finds on its own; ``args`` carries
anything else.

Here the checks are limited to ``readability-magic-numbers`` and the source
trips it on purpose, so the build shows a diagnostic. It still succeeds:
clang-tidy exits zero for a warning, and ``--warnings-as-errors=*`` is how a
project turns that into a failed build. ``--export-fixes`` writes what it
found, so a file in the build directory proves the analysis ran.
"""

from pcons import Project

project = Project("clang_tidy_demo")
env = project.Environment(toolchain="c++")

env.use_clang_tidy(
    args=[
        "--export-fixes="
        + str(project.root_dir / project.build_dir / "tidy-fixes.yaml").replace(
            "\\", "/"
        ),
    ]
)

project.Program("hello", env, sources=[project.root_dir / "src" / "hello.cc"])
