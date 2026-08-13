# SPDX-License-Identifier: MIT
"""The CLIs that moved to click must not drift back to argparse.

Scoped to the three deliberately, not applied repo-wide: nine build-edge
helper scripts under pcons/ are meant to keep argparse, and each says why
above its import. Widening this test would flag all of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

CONVERTED = [
    "pcons/packages/fetch/cli.py",
    "pcons/test_runner.py",
    "pcons/_gen_stubs.py",
]


def _imported_modules(source: str) -> set[str]:
    """Every module name the source imports, however it spells the import."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("relpath", CONVERTED)
def test_converted_cli_does_not_import_argparse(relpath: str) -> None:
    # Parsed rather than grepped: these files mention argparse in comments
    # explaining what the conversion fixed, and that must stay allowed.
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert "argparse" not in _imported_modules(source)


@pytest.mark.parametrize("relpath", CONVERTED)
def test_converted_cli_imports_click(relpath: str) -> None:
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert "click" in _imported_modules(source)
