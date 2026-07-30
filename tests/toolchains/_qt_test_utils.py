# SPDX-License-Identifier: MIT
"""Shared helpers for the Qt toolchain tests.

All Qt unit tests run without a Qt installation: the qt toolchain is
constructed with fake tool paths and the generated build.ninja is
inspected as text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pcons.generators.generator import BaseGenerator
from pcons.generators.ninja import NinjaGenerator
from pcons.toolchains.qt.toolchain import QtTool, QtToolchain

if TYPE_CHECKING:
    from pcons.core.environment import Environment
    from pcons.core.project import Project

FAKE_TOOLS = {
    "moc": "/fake/bin/moc",
    "uic": "/fake/bin/uic",
    "rcc": "/fake/bin/rcc",
    "qmltyperegistrar": "/fake/bin/qmltyperegistrar",
    "lrelease": "/fake/bin/lrelease",
    "lupdate": "/fake/bin/lupdate",
}


def fake_qt_toolchain(**tool_paths: str) -> QtToolchain:
    """A configured qt toolchain with fake (or given) tool paths."""
    toolchain = QtToolchain()
    toolchain._tools = {"qt": QtTool(**{**FAKE_TOOLS, **tool_paths})}
    toolchain._configured = True
    return toolchain


def qt_only_env(project: Project) -> Environment:
    """Environment whose primary toolchain is the fake qt toolchain."""
    return project.Environment(toolchain=fake_qt_toolchain())


def cxx_env_with_qt(project: Project) -> Environment:
    """A real C++ toolchain env with the fake qt toolchain added.

    Skips the calling test when no C++ compiler is available.
    """
    from pcons.toolchains import find_c_toolchain

    try:
        cxx_toolchain = find_c_toolchain()
    except RuntimeError:
        pytest.skip("no C++ toolchain available")
    env = project.Environment(toolchain=cxx_toolchain)
    env.add_toolchain(fake_qt_toolchain())
    return env


def generate_ninja(project: Project) -> str:
    """Generate build.ninja and return its content (slashes normalized)."""
    NinjaGenerator().generate(project)
    BaseGenerator._generate_pending(project)
    return (project.build_dir / "build.ninja").read_text().replace("\\", "/")
