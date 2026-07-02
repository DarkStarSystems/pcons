# SPDX-License-Identifier: MIT
"""Pytest configuration and shared fixtures."""

from typing import Any

import pytest

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.preset import _PRESET_REGISTRY
from pcons.core.project import Project
from pcons.generators.generator import BaseGenerator
from pcons.toolchains.gcc import (
    GccArchiver,
    GccCCompiler,
    GccCxxCompiler,
    GccLinker,
    GccToolchain,
)


@pytest.fixture
def gcc_toolchain():
    """Create a pre-configured GCC toolchain for testing.

    Populates _tools so that Environment(toolchain=...) registers all tools
    (cc, cxx, ar, link) and command templates expand correctly.
    """
    toolchain = GccToolchain()
    toolchain._tools = {
        "cc": GccCCompiler(),
        "cxx": GccCxxCompiler(),
        "ar": GccArchiver(),
        "link": GccLinker(),
    }
    toolchain._configured = True
    return toolchain


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory with standard structure."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    return tmp_path


@pytest.fixture
def sample_c_source(tmp_project):
    """Create a simple C source file for testing."""
    src_file = tmp_project / "src" / "main.c"
    src_file.write_text(
        """\
#include <stdio.h>

int main(void) {
    printf("Hello, pcons!\\n");
    return 0;
}
"""
    )
    return src_file


def _snapshot_registries() -> tuple[dict[str, Any], dict[str, Any]]:
    """Snapshot the mutable dicts backing the global BuilderRegistry and the
    contributed-preset registry, so registrations made during a test can be
    undone afterwards. Copies the containers (not just rebinding names) so
    later mutation of the live dicts doesn't affect the snapshot.
    """
    return dict(BuilderRegistry._builders), dict(_PRESET_REGISTRY)


def _restore_registries(snapshot: tuple[dict[str, Any], dict[str, Any]]) -> None:
    """Restore the global registries to a prior `_snapshot_registries()` result."""
    builders, presets = snapshot
    BuilderRegistry._builders.clear()
    BuilderRegistry._builders.update(builders)
    _PRESET_REGISTRY.clear()
    _PRESET_REGISTRY.update(presets)


@pytest.fixture(autouse=True)
def clear_project_tree():
    """Ensure global Project/generator/registry state is isolated per test.

    Snapshots the BuilderRegistry and the contributed-preset registry before
    each test and restores them afterwards, so a test that registers a
    builder or preset (directly, or as a side effect of a non-hermetic module
    load) can't leak state into later tests.
    """
    Project._clear_tree()
    BaseGenerator._clear_pending()
    registries = _snapshot_registries()
    yield
    _restore_registries(registries)
    Project._clear_tree()
    BaseGenerator._clear_pending()


@pytest.fixture
def test_project(tmp_path):
    """Create a default project for testing."""
    project = Project(name="test_project", root_dir=tmp_path)
    return project
