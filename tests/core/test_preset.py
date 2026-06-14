# SPDX-License-Identifier: MIT
"""Tests for the declarative Preset model and Environment.apply().

These exercise the tool-agnostic core: building Preset/ToolContribution data
and applying it to an environment. Toolchain-specific preset construction is
covered in tests/toolchains/.
"""

from __future__ import annotations

import pytest

from pcons.core.environment import Environment
from pcons.core.preset import Preset, ToolContribution


def _make_env() -> Environment:
    """Environment with cc, cxx, and link tools (flags/defines lists)."""
    env = Environment()
    for name in ("cc", "cxx"):
        tool = env.add_tool(name)
        tool.set("cmd", name)
        tool.set("flags", [])
        tool.set("defines", [])
    link = env.add_tool("link")
    link.set("cmd", "link")
    link.set("flags", [])
    return env


class TestPresetData:
    """The Preset / ToolContribution dataclasses are immutable bundles."""

    def test_contribution_defaults(self) -> None:
        c = ToolContribution("cc")
        assert c.tool == "cc"
        assert c.flags == ()
        assert c.defines == ()
        assert c.cmd is None

    def test_preset_is_frozen(self) -> None:
        preset = Preset(name="release", category="variant")
        with pytest.raises(AttributeError):
            preset.name = "debug"  # type: ignore[misc]


class TestApply:
    """Environment.apply() applies contributions and records the preset."""

    def test_extends_flags_and_defines(self, test_project):
        env = _make_env()
        env.apply(
            Preset(
                name="release",
                category="variant",
                exclusive_group="build_variant",
                contributions=(
                    ToolContribution("cc", flags=("-O2",), defines=("NDEBUG",)),
                    ToolContribution("cxx", flags=("-O2",), defines=("NDEBUG",)),
                ),
            )
        )
        assert "-O2" in env.cc.flags
        assert "NDEBUG" in env.cxx.defines
        assert env.variant == "release"

    def test_cmd_contribution_replaces_command(self, test_project):
        env = _make_env()
        env.apply(
            Preset(
                name="wasm32-emscripten",
                category="target",
                arch="wasm32",
                contributions=(ToolContribution("cc", cmd="emcc"),),
            )
        )
        assert env.cc.cmd == "emcc"
        assert env.target_arch == "wasm32"

    def test_skips_absent_tools(self, test_project):
        env = Environment()
        cc = env.add_tool("cc")
        cc.set("flags", [])
        # Contribution targets a 'link' tool that doesn't exist -> no error.
        env.apply(
            Preset(
                name="p",
                category="feature",
                contributions=(
                    ToolContribution("cc", flags=("-Wall",)),
                    ToolContribution("link", flags=("-pie",)),
                ),
            )
        )
        assert "-Wall" in env.cc.flags
        assert not env.has_tool("link")

    def test_records_applied_presets(self, test_project):
        env = _make_env()
        warnings = Preset(name="warnings", category="feature")
        release = Preset(
            name="release", category="variant", exclusive_group="build_variant"
        )
        env.apply(warnings)
        env.apply(release)
        names = [p.name for p in env._applied_presets]
        assert names == ["warnings", "release"]


class TestExclusiveGroup:
    """Presets in the same exclusive group are mutually exclusive."""

    def test_second_different_variant_raises(self, test_project):
        env = _make_env()
        env.apply(
            Preset(name="debug", category="variant", exclusive_group="build_variant")
        )
        with pytest.raises(ValueError, match="already"):
            env.apply(
                Preset(
                    name="release",
                    category="variant",
                    exclusive_group="build_variant",
                )
            )

    def test_same_variant_name_allowed(self, test_project):
        """Re-applying the same-named preset (e.g. one per toolchain) is fine."""
        env = _make_env()
        p = Preset(
            name="debug",
            category="variant",
            exclusive_group="build_variant",
            contributions=(ToolContribution("cc", flags=("-g",)),),
        )
        env.apply(p)
        env.apply(p)  # no raise
        assert env.cc.flags.count("-g") == 2

    def test_no_group_means_no_exclusion(self, test_project):
        env = _make_env()
        env.apply(Preset(name="warnings", category="feature"))
        env.apply(Preset(name="sanitize", category="feature"))  # no raise


class TestCloneSurvival:
    """The applied-preset history survives environment cloning."""

    def test_clone_copies_history_independently(self, test_project):
        env = _make_env()
        env.apply(
            Preset(
                name="release",
                category="variant",
                exclusive_group="build_variant",
                contributions=(ToolContribution("cc", flags=("-O2",)),),
            )
        )
        clone = env.clone()
        assert [p.name for p in clone._applied_presets] == ["release"]

        # Applying to the clone doesn't affect the original's history.
        clone.apply(Preset(name="warnings", category="feature"))
        assert [p.name for p in env._applied_presets] == ["release"]
        assert [p.name for p in clone._applied_presets] == ["release", "warnings"]
