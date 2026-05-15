# SPDX-License-Identifier: MIT
"""Verify generated builder stubs in `pcons/core/project.py` are up to date.

If this fails, run:
    python -m pcons._gen_stubs

The generator introspects `BuilderRegistry` and rewrites the marked block
inside the `Project` class. This test re-runs the generator in --check
mode so CI fails if a builder's `create_target` signature drifts from the
generated stub.
"""

from __future__ import annotations

from pcons._gen_stubs import write_or_check


def test_builder_stubs_are_fresh() -> None:
    rc = write_or_check("check")
    assert rc == 0, (
        "Generated builder stubs in pcons/core/project.py are stale. "
        "Run: python -m pcons._gen_stubs"
    )
