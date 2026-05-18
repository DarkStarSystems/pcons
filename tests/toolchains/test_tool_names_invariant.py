# SPDX-License-Identifier: MIT
"""Invariant: every built-in toolchain's `TOOL_NAMES` class var matches the
actual tool names installed into `_tools` after setup.

The Environment typing stub (`pcons/core/_environment_stubs.py`) is generated
by walking `BaseToolchain.__subclasses__()` and unioning every subclass's
`TOOL_NAMES`. The freshness check (`tests/test_builder_stubs_fresh.py`)
catches drift between TOOL_NAMES and the generated stub, but it does NOT
catch the case where TOOL_NAMES disagrees with what the toolchain actually
installs at runtime — a toolchain that adds a tool to `_tools` without also
adding it to TOOL_NAMES will be silently invisible to IDEs, and a toolchain
that advertises a tool in TOOL_NAMES without installing it produces a stub
attribute that always AttributeErrors at use.

This test runs `ToolchainEntry.create_toolchain()` for each registered
built-in toolchain (which populates `_tools` from `tool_classes` without
needing the underlying compiler to be installed) and asserts that the keys
match TOOL_NAMES exactly.
"""

from __future__ import annotations

# Force-import every shipped toolchain so each one self-registers with
# `toolchain_registry`. The list MUST stay in sync with
# `_collect_tool_names()` in `pcons/_gen_stubs.py` — the test will fail
# loudly if a toolchain is here but absent from the generator, or vice
# versa, because the generated stub will drift.
import pcons.toolchains.clang_cl  # noqa: F401
import pcons.toolchains.cuda  # noqa: F401
import pcons.toolchains.cython  # noqa: F401
import pcons.toolchains.emscripten  # noqa: F401
import pcons.toolchains.gcc  # noqa: F401
import pcons.toolchains.gfortran  # noqa: F401
import pcons.toolchains.llvm  # noqa: F401
import pcons.toolchains.msvc  # noqa: F401
import pcons.toolchains.wasi  # noqa: F401
from pcons.tools.toolchain import toolchain_registry


def _unique_registered_toolchains():
    """Yield each ToolchainEntry once (the registry maps multiple aliases
    to the same entry)."""
    seen: set[type] = set()
    for entry in toolchain_registry._toolchains.values():
        if entry.toolchain_class in seen:
            continue
        seen.add(entry.toolchain_class)
        yield entry


def test_every_builtin_toolchain_tool_names_matches_installed_tools() -> None:
    failures: list[str] = []
    checked: list[str] = []
    for entry in _unique_registered_toolchains():
        toolchain = entry.create_toolchain()
        installed = set(toolchain._tools.keys())
        declared = set(entry.toolchain_class.TOOL_NAMES)
        checked.append(entry.toolchain_class.__name__)
        if installed != declared:
            failures.append(
                f"{entry.toolchain_class.__name__}: "
                f"TOOL_NAMES={sorted(declared)} but "
                f"installed _tools={sorted(installed)} "
                f"(extra in TOOL_NAMES: {sorted(declared - installed)}; "
                f"missing from TOOL_NAMES: {sorted(installed - declared)})"
            )
    # Sanity: make sure we actually iterated over every shipped toolchain,
    # so a registration regression doesn't silently turn this into a no-op.
    expected = {
        "ClangClToolchain",
        "CudaToolchain",
        "CythonToolchain",
        "EmscriptenToolchain",
        "GccToolchain",
        "GfortranToolchain",
        "LlvmToolchain",
        "MsvcToolchain",
        "WasiToolchain",
    }
    assert expected.issubset(set(checked)), (
        f"Test did not cover every shipped toolchain. "
        f"Missing: {sorted(expected - set(checked))}"
    )
    assert not failures, "TOOL_NAMES out of sync with installed tools:\n" + "\n".join(
        failures
    )
