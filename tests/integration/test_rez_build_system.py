# SPDX-License-Identifier: MIT
"""Tests for pcons.integrations.rez.build_system.PconsBuildSystem.

Skipped unless ``rez`` is importable. End-to-end ``rez-build`` exercise
lives in the ``examples/35_rez_integration`` example test (gated on
``REZ_USED_RESOLVE``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("rez")

from pcons.integrations.rez import build_system as bs  # noqa: E402


def test_name_is_pcons() -> None:
    assert bs.PconsBuildSystem.name() == "pcons"


def test_is_valid_root_with_script(tmp_path: Path) -> None:
    (tmp_path / "pcons-build.py").write_text("from pcons import Project\n")
    assert bs.PconsBuildSystem.is_valid_root(str(tmp_path)) is True


def test_is_valid_root_without_script(tmp_path: Path) -> None:
    assert bs.PconsBuildSystem.is_valid_root(str(tmp_path)) is False


def test_register_plugin_returns_class() -> None:
    assert bs.register_plugin() is bs.PconsBuildSystem


def test_inherits_from_rez_build_system() -> None:
    from rez.build_system import BuildSystem

    assert issubclass(bs.PconsBuildSystem, BuildSystem)


def test_bind_cli_adds_pcons_options() -> None:
    """bind_cli should register --pcons-generator and --pcons-jobs."""
    parser = argparse.ArgumentParser()
    group = parser.add_argument_group("pcons")
    bs.PconsBuildSystem.bind_cli(parser, group)

    args = parser.parse_args(["--pcons-generator", "make", "--pcons-jobs", "4"])

    assert args.pcons_generator == "make"
    assert args.pcons_jobs == 4


def test_bind_cli_defaults() -> None:
    """Defaults: ninja generator, jobs=None (auto)."""
    parser = argparse.ArgumentParser()
    group = parser.add_argument_group("pcons")
    bs.PconsBuildSystem.bind_cli(parser, group)

    args = parser.parse_args([])

    assert args.pcons_generator == "ninja"
    assert args.pcons_jobs is None


def test_bind_cli_rejects_unknown_generator() -> None:
    """Generator must be one of ninja|make."""
    parser = argparse.ArgumentParser()
    group = parser.add_argument_group("pcons")
    bs.PconsBuildSystem.bind_cli(parser, group)

    with pytest.raises(SystemExit):
        parser.parse_args(["--pcons-generator", "xcode"])


def test_pcons_cli_prefers_context_pcons() -> None:
    """When pcons is on PATH inside the rez resolve, use that."""
    ctx = MagicMock()
    ctx.which.return_value = "/rez/bin/pcons"

    cmd = bs.PconsBuildSystem._pcons_cli(ctx)

    assert cmd == ["/rez/bin/pcons"]
    ctx.which.assert_called_once_with("pcons", fallback=False)


def test_pcons_cli_falls_back_to_python_dash_m() -> None:
    """If pcons isn't in the rez resolve, run python -m pcons.

    sys.executable is used so we hit the same Python that loaded this
    plugin module — that's the venv where rez-with-pcons lives.
    """
    ctx = MagicMock()
    ctx.which.return_value = None

    cmd = bs.PconsBuildSystem._pcons_cli(ctx)

    assert cmd == [sys.executable, "-m", "pcons"]
