# SPDX-License-Identifier: MIT
"""Pytest configuration and shared fixtures."""

from pathlib import Path
from typing import Any

import pytest
from hypothesis import settings

# The usage-requirement/target-option registries hold plain strings, so the
# restore below cannot tell a pcons import side effect from a test's own
# registration the way the builder restore does. Instead, import the one
# pcons module that registers into them (install_name) BEFORE the first
# snapshot, so it is in every baseline — the import will not run again, and
# toolchains now import lazily, so nothing else forces it.
import pcons.toolchains.unix  # noqa: E402, F401
from pcons import commands as user_commands
from pcons import modules
from pcons.core import invocation
from pcons.core.builder_registry import BuilderRegistry
from pcons.core.cache import reset_cache
from pcons.core.debug import reset_debug
from pcons.core.preset import _PRESET_REGISTRY
from pcons.core.project import Project
from pcons.core.target import _KNOWN_TARGET_OPTIONS, _KNOWN_USAGE_REQUIREMENTS
from pcons.generators.generator import BaseGenerator
from pcons.toolchains.gcc import (
    GccArchiver,
    GccCCompiler,
    GccCxxCompiler,
    GccLinker,
    GccToolchain,
)

# Hypothesis profiles for the property tests in tests/fuzz/. "dev" is the
# default: small and derandomized, so a normal `make test` costs little and
# never fails on an example an unrelated run happened to draw. The nightly
# CI job passes --hypothesis-profile=nightly to actually go looking. That
# flag is applied by the Hypothesis pytest plugin after this file is
# imported, so it wins over the load_profile() below.
settings.register_profile("dev", max_examples=50, deadline=None, derandomize=True)
settings.register_profile("nightly", max_examples=2000, deadline=None, print_blob=True)
settings.load_profile("dev")


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


_RegistrySnapshot = tuple[dict[str, Any], dict[str, Any], set[str], dict[str, str]]


def _snapshot_registries() -> _RegistrySnapshot:
    """Snapshot the mutable containers backing the process-global registries:
    BuilderRegistry, the contributed-preset registry, and the usage-requirement
    and target-option name sets. Registrations made during a test can then be
    undone afterwards. Copies the containers (not just rebinding names) so
    later mutation of the live containers doesn't affect the snapshot.
    """
    return (
        dict(BuilderRegistry._builders),
        dict(_PRESET_REGISTRY),
        set(_KNOWN_USAGE_REQUIREMENTS),
        dict(_KNOWN_TARGET_OPTIONS),
    )


def _restore_registries(snapshot: _RegistrySnapshot) -> None:
    """Restore the global registries to a prior `_snapshot_registries()` result.

    With one exception: a builder that a *pcons* module registered during the
    test survives. Toolchains import lazily and register their builders as an
    import side effect (Qt's QtProgram, for example), and imports do not run
    again — stripping such a registration would lose it for the rest of the
    process, failing every later test that uses it. Only registrations made
    by test code itself are dropped.
    """
    builders, presets, usage_reqs, target_opts = snapshot
    imported = {
        name: reg
        for name, reg in BuilderRegistry._builders.items()
        if name not in builders
        and getattr(reg.create_target, "__module__", "").startswith("pcons.")
    }
    BuilderRegistry._builders.clear()
    BuilderRegistry._builders.update(builders)
    BuilderRegistry._builders.update(imported)
    _PRESET_REGISTRY.clear()
    _PRESET_REGISTRY.update(presets)
    _KNOWN_USAGE_REQUIREMENTS.clear()
    _KNOWN_USAGE_REQUIREMENTS.update(usage_reqs)
    _KNOWN_TARGET_OPTIONS.clear()
    _KNOWN_TARGET_OPTIONS.update(target_opts)


@pytest.fixture(autouse=True)
def clear_tool_env_vars(monkeypatch):
    """Isolate tests from the developer's tool-selection env vars.

    CC/CXX/etc. are authoritative when set (they select compilers and
    steer toolchain auto-detection), so an exported CXX in a dev shell
    would perturb every Environment-creating test. Tests that exercise
    the mechanism set the vars explicitly.
    """
    for var in ("CC", "CXX", "FC", "AR", "SWIFTC", "CUDACXX", "RC"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def clear_pcons_env_vars(monkeypatch):
    """Isolate tests from pcons' own runtime environment variables.

    PCONS_BUILD_DIR/SOURCE_DIR/VARS/VARIANT/GENERATOR (and the bare VARIANT/
    GENERATOR that get_variant/Generator honor) steer build-dir resolution, the
    persisted cache, and variant/generator selection. An exported one - e.g. set
    by an IDE integration in the dev's shell - would silently perturb every
    run_script-driven test, sending build files to the wrong directory. Tests
    that exercise these set them explicitly via monkeypatch after this fixture.
    """
    for var in (
        "PCONS_BUILD_DIR",
        "PCONS_SOURCE_DIR",
        "PCONS_VARS",
        "PCONS_VARIANT",
        "PCONS_GENERATOR",
        "PCONS_RECONFIGURE",
        "VARIANT",
        "GENERATOR",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def clear_project_tree():
    """Ensure global Project/generator/registry state is isolated per test.

    Snapshots the process-global registries (see _snapshot_registries) before
    each test and restores them afterwards, so a test that registers a
    builder, preset, usage requirement or target option (directly, or as a
    side effect of a non-hermetic module load) can't leak state into later
    tests.

    The enabled debug subsystems go with them: they are process-wide, and a
    test that turns tracing on would otherwise put DEBUG lines in every later
    test's captured stderr.

    The declared-command registry and the loaded add-on modules are
    process-wide in the same way: a test that declares `pcons run` commands, or
    loads a module that declares some, would otherwise leave them for whichever
    test lists or dispatches next.
    """
    Project._clear_tree()
    BaseGenerator._clear_pending()
    invocation.clear()
    reset_cache()
    reset_debug()
    user_commands.clear()
    modules.clear_modules()
    registries = _snapshot_registries()
    yield
    _restore_registries(registries)
    Project._clear_tree()
    BaseGenerator._clear_pending()
    invocation.clear()
    reset_cache()
    reset_debug()
    user_commands.clear()
    modules.clear_modules()


@pytest.fixture(autouse=True)
def no_source_tree_leak(request):
    """Fail any test that leaks generated files into the source tree.

    A Project created without an explicit root_dir infers its root from the
    caller's source file — for a test, that's a real directory under tests/ —
    and generation then plants the compile_commands.json root symlink there.
    Catch it at the offending test rather than as a mystery artifact later
    (a dangling one once broke rsync-based Windows test syncing).
    """
    yield
    leak = Path(request.node.path).parent / "compile_commands.json"
    if leak.is_symlink() or leak.exists():
        leak.unlink()
        pytest.fail(
            "test leaked compile_commands.json into the source tree; "
            "pass an explicit root_dir (e.g. tmp_path) to Project()"
        )


@pytest.fixture
def test_project(tmp_path):
    """Create a default project for testing."""
    project = Project(name="test_project", root_dir=tmp_path)
    return project
