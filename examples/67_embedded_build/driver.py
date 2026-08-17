#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""pcons as a library: the two styles from `docs/library.md`.

Run me directly (`python3 driver.py`); pcons never sees my command line.
"""

import sys
from pathlib import Path

from pcons import Project, find_c_toolchain
from pcons.cli import run_ninja, run_script

ROOT = Path(__file__).resolve().parent


def embedded_build() -> int:
    """Embedded style: describe a build right here — no build script at all.

    write_build_files() writes this project's build files immediately.
    """
    project = Project("embedded", root_dir=ROOT, build_dir="build-embed")
    env = project.Environment(toolchain=find_c_toolchain())
    project.Program("hello-embed", env, sources=["src/hello.c"])
    project.write_build_files()
    return run_ninja(ROOT / "build-embed")


def custom_cli() -> int:
    """Custom-CLI style: your own entry point around an ordinary pcons-build.py.

    run_script() is the CLI's own service layer: PCONS_* setup, settings
    persistence, invocation recording, cwd management, cleanup.
    """
    code, projects = run_script(ROOT / "pcons-build.py", ROOT / "build")
    if code != 0 or not projects:
        return code or 1
    return run_ninja(ROOT / "build")


if __name__ == "__main__":
    sys.exit(embedded_build() or custom_cli())
