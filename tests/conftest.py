# SPDX-License-Identifier: MIT
"""Pytest configuration and shared fixtures."""

import pytest

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
