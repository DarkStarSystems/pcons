# SPDX-License-Identifier: MIT
"""Smoke tests to verify basic functionality."""

import pcons


def test_version():
    """Verify pcons has a version."""
    assert pcons.__version__ is not None
    assert "0." in pcons.__version__


def test_import_core():
    """Verify core modules are importable."""
    # These imports verify the module structure is correct
    from pcons import (
        configure,  # noqa: F401
        core,  # noqa: F401
        generators,  # noqa: F401
        packages,  # noqa: F401
        tools,  # noqa: F401
    )
