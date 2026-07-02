# SPDX-License-Identifier: MIT
"""Tests proving the autouse test-isolation fixture cleans up global registry
state (see `tests/conftest.py::clear_project_tree`).

Without this cleanup, a test that registers a builder or a contributed preset
(directly, or as the side effect of a non-hermetic module load) leaks that
registration into every later test in the process — a latent, hard-to-debug
source of intermittent CI failures.

These tests exercise the exact snapshot/restore helpers the autouse fixture
uses, so they demonstrate the real fixture behavior deterministically, rather
than relying on pytest's item ordering across separate test functions (which
is not guaranteed under `pytest -n auto`).
"""

from __future__ import annotations

from pcons.core.builder_registry import BuilderRegistry
from pcons.core.preset import _PRESET_REGISTRY
from tests.conftest import _restore_registries, _snapshot_registries


def _dummy_create_target(project: object) -> None:
    return None


def test_snapshot_restore_removes_builder_registered_during_test() -> None:
    """A builder registered after the snapshot is gone once restored."""
    assert BuilderRegistry.get("__leak_test_builder__") is None

    snapshot = _snapshot_registries()
    BuilderRegistry.register(
        "__leak_test_builder__",
        create_target=_dummy_create_target,
        target_type="interface",
    )
    assert BuilderRegistry.get("__leak_test_builder__") is not None

    _restore_registries(snapshot)

    assert BuilderRegistry.get("__leak_test_builder__") is None


def test_snapshot_restore_removes_preset_registered_during_test() -> None:
    """A preset registered after the snapshot is gone once restored."""
    assert "__leak_test__/preset" not in _PRESET_REGISTRY

    snapshot = _snapshot_registries()
    from pcons.core.preset import register_preset

    register_preset("__leak_test__/preset", lambda toolchain: None)
    assert "__leak_test__/preset" in _PRESET_REGISTRY

    _restore_registries(snapshot)

    assert "__leak_test__/preset" not in _PRESET_REGISTRY


def test_snapshot_restore_preserves_builtin_builders() -> None:
    """Restoring must bring back the pre-test snapshot, not wipe to empty —
    built-in builders like Program must still be registered afterwards."""
    snapshot = _snapshot_registries()
    BuilderRegistry.register(
        "__leak_test_builder_2__",
        create_target=_dummy_create_target,
        target_type="interface",
    )
    _restore_registries(snapshot)

    assert BuilderRegistry.get("Program") is not None
