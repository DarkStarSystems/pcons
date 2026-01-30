# SPDX-License-Identifier: MIT
"""MkDocs macros hook — exposes {{ version }} in markdown."""

import re
import subprocess
from pathlib import Path


def _get_version() -> str:
    """Get version string, with git info for unreleased builds.

    On a tagged release:  "0.6.0"
    Past a tag:           "0.6.0.dev3 (g9abe7cc, 2026-01-30)"
    No tags at all:       "0.6.0.dev (9abe7cc, 2026-01-30)"
    Git unavailable:      "0.6.0"
    """
    # Parse version from pcons/__init__.py without importing
    init_file = Path(__file__).parent.parent / "pcons" / "__init__.py"
    version = "unknown"
    for line in init_file.read_text().splitlines():
        m = re.match(r'^__version__\s*=\s*["\']([^"\']+)["\']', line)
        if m:
            version = m.group(1)
            break

    # Try git describe to detect unreleased commits
    try:
        desc = subprocess.check_output(
            ["git", "describe", "--tags", "--long", "--always"],
            cwd=Path(__file__).parent.parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # git describe --long gives "v0.6.0-3-g9abe7cc" or just "9abe7cc"
        m = re.match(r"v?[\d.]+-(\d+)-g([0-9a-f]+)", desc)
        if m:
            commits_past = int(m.group(1))
            short_hash = m.group(2)
            if commits_past > 0:
                # Get commit date
                date = subprocess.check_output(
                    ["git", "log", "-1", "--format=%cs"],
                    cwd=Path(__file__).parent.parent,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()
                return f"{version}.dev{commits_past} ({short_hash}, {date})"
        # else: exactly on a tag, just use __version__
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return version


def define_env(env):
    """Define template variables for mkdocs-macros."""
    env.variables["version"] = _get_version()
