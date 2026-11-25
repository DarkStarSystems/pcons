# SPDX-License-Identifier: MIT
"""The ``[tool.pcons] version-provider`` of this example."""

from pathlib import Path


def get_version(source_dir: Path) -> str:
    """Return the version written in ``VERSION``."""
    return (source_dir / "VERSION").read_text(encoding="utf-8").strip()
