# SPDX-License-Identifier: MIT
"""Tests for pcons.contrib.latex.find_latex_toolchain."""

from __future__ import annotations

import pytest

from pcons.contrib.latex import LatexTool, find_latex_toolchain


def test_finds_latexmk_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    toolchain = find_latex_toolchain()
    assert isinstance(toolchain.tools["latex"], LatexTool)


def test_without_latexmk_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="latexmk"):
        find_latex_toolchain()
