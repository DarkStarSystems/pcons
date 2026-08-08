# SPDX-License-Identifier: MIT
"""The examples catalogue must match the examples.

`docs/examples.md` is generated from every example's own `test.toml`
description, for the same reason the builder stubs are generated: a
hand-maintained second copy of a list that changes every release is a copy
that is wrong by the next one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "scripts" / "gen_examples_index.py"


def test_the_examples_index_is_up_to_date() -> None:
    """Add an example, regenerate: `python scripts/gen_examples_index.py`."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stderr


def test_every_example_is_listed() -> None:
    """An example with no `test.toml` description is invisible to users, and
    also untested — the harness reads the same file."""
    page = (ROOT / "docs" / "examples.md").read_text(encoding="utf-8")
    missing = [
        example.name
        for example in sorted((ROOT / "examples").iterdir())
        if example.is_dir() and not (page.count(f"examples/{example.name})"))
    ]

    assert not missing, f"examples absent from docs/examples.md: {missing}"
