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
        # target_arch has a single writer (the arch knob); a target-category
        # preset carries arch as metadata only.
        assert getattr(env, "target_arch", None) is None

    def test_arch_preset_records_target_arch(self, test_project):
        env = _make_env()
        env.apply(
            Preset(
                name="arm64",
                category="arch",
                arch="arm64",
                contributions=(ToolContribution("cc", flags=("-arch", "arm64")),),
            )
        )
        assert env.target_arch == "arm64"

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

    def test_cmd_to_absent_tool_raises(self, test_project):
        """A cmd swap is a retargeting mechanism; dropping it silently
        un-crosses the build (docs/presets.md, "Preset application")."""
        env = Environment()
        cxx = env.add_tool("cxx")
        cxx.set("flags", [])
        with pytest.raises(ValueError, match="command"):
            env.apply(
                Preset(
                    name="cross",
                    category="target",
                    contributions=(ToolContribution("cc", cmd="emcc"),),
                )
            )
        # Atomic: nothing was recorded.
        assert env.applied_presets == ()

    def test_zero_effect_preset_raises(self, test_project):
        """A preset none of whose contributions land is an error, not a
        silent no-op."""
        env = Environment()
        cc = env.add_tool("cc")
        cc.set("flags", [])
        with pytest.raises(ValueError, match="no effect"):
            env.apply(
                Preset(
                    name="linkstuff",
                    category="feature",
                    contributions=(ToolContribution("link", flags=("-pie",)),),
                )
            )
        assert env.applied_presets == ()

    def test_empty_contributions_preset_is_deliberate_noop(self, test_project):
        """No contributions at all = the realizer's declared no-op; allowed
        (e.g. wasm32 arch, which needs no flags)."""
        env = Environment()
        env.apply(Preset(name="wasm32", category="arch", arch="wasm32"))
        assert env.target_arch == "wasm32"
        assert [p.name for p in env.applied_presets] == ["wasm32"]

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


class TestFanoutDedup:
    """Identical resolved presets apply once across the toolchain fan-out.

    Toolchains share tools (cc/cxx/link); without dedup, two toolchains
    resolving the same preset would double flags like -Werror or -arch
    (docs/presets.md, "Preset application").
    """

    def test_identical_presets_apply_once_in_fanout(self, test_project):
        env = _make_env()
        p = Preset(
            name="werror",
            category="feature",
            contributions=(ToolContribution("cc", flags=("-Werror",)),),
        )
        with env._dedup_fanout():
            env.apply(p)
            env.apply(p)
        assert list(env.cc.flags).count("-Werror") == 1

    def test_differing_realizations_both_apply(self, test_project):
        """Same name, different contributions (e.g. gcc's cc/cxx warnings vs
        gfortran's fc warnings) are distinct resolutions — both apply."""
        env = _make_env()
        with env._dedup_fanout():
            env.apply(
                Preset(
                    name="warnings",
                    category="feature",
                    contributions=(ToolContribution("cc", flags=("-Wall",)),),
                )
            )
            env.apply(
                Preset(
                    name="warnings",
                    category="feature",
                    contributions=(ToolContribution("cxx", flags=("-Wall",)),),
                )
            )
        assert "-Wall" in env.cc.flags
        assert "-Wall" in env.cxx.flags

    def test_user_reapplication_stays_additive(self, test_project):
        """Deliberate re-application outside a fan-out remains additive."""
        env = _make_env()
        p = Preset(
            name="werror",
            category="feature",
            contributions=(ToolContribution("cc", flags=("-Werror",)),),
        )
        env.apply(p)
        env.apply(p)
        assert list(env.cc.flags).count("-Werror") == 2


class TestExclusiveGroup:
    """Presets in the same exclusive group act as one knob: applying
    another replaces the one already applied (docs/presets.md)."""

    def test_switching_variant_replaces(self, test_project):
        env = _make_env()
        env.apply(
            Preset(
                name="debug",
                category="variant",
                exclusive_group="build_variant",
                contributions=(
                    ToolContribution("cc", flags=("-O0", "-g"), defines=("DEBUG",)),
                ),
            )
        )
        env.apply(
            Preset(
                name="release",
                category="variant",
                exclusive_group="build_variant",
                contributions=(
                    ToolContribution("cc", flags=("-O2",), defines=("NDEBUG",)),
                ),
            )
        )
        assert "-O0" not in env.cc.flags
        assert "-g" not in env.cc.flags
        assert "DEBUG" not in env.cc.defines
        assert "-O2" in env.cc.flags
        assert "NDEBUG" in env.cc.defines
        assert env.variant == "release"
        assert [p.name for p in env.applied_presets] == ["release"]

    def test_rejected_group_preset_leaves_current_one_applied(self, test_project):
        """Application is atomic: a group preset that fails validation must
        not un-apply the group member already in effect."""
        env = _make_env()
        env.apply(
            Preset(
                name="release",
                category="variant",
                exclusive_group="build_variant",
                contributions=(ToolContribution("cc", flags=("-O2",)),),
            )
        )
        with pytest.raises(ValueError, match="no effect"):
            env.apply(
                Preset(
                    name="debug",
                    category="variant",
                    exclusive_group="build_variant",
                    contributions=(ToolContribution("nonexistent", flags=("-O0",)),),
                )
            )
        assert "-O2" in env.cc.flags
        assert [p.name for p in env.applied_presets] == ["release"]
        assert env.variant == "release"

    def test_reapplying_same_variant_is_idempotent(self, test_project):
        """set_variant is a knob: same name twice doesn't double flags."""
        env = _make_env()
        p = Preset(
            name="debug",
            category="variant",
            exclusive_group="build_variant",
            contributions=(ToolContribution("cc", flags=("-g",)),),
        )
        env.apply(p)
        env.apply(p)
        assert env.cc.flags.count("-g") == 1

    def test_clone_then_switch_variant_works(self, test_project):
        """The documented workflow: clone at any point, retune the clone."""
        env = _make_env()
        env.apply(
            Preset(
                name="release",
                category="variant",
                exclusive_group="build_variant",
                contributions=(ToolContribution("cc", flags=("-O2",)),),
            )
        )
        dbg = env.clone()
        dbg.apply(
            Preset(
                name="debug",
                category="variant",
                exclusive_group="build_variant",
                contributions=(ToolContribution("cc", flags=("-O0",)),),
            )
        )
        assert "-O0" in dbg.cc.flags
        assert "-O2" not in dbg.cc.flags
        # Original untouched.
        assert "-O2" in env.cc.flags
        assert "-O0" not in env.cc.flags

    def test_group_preset_with_cmd_rejected_at_apply(self, test_project):
        """Group presets must be invertible (purely additive): a cmd
        contribution is rejected when the preset is applied, not later
        when switching would fail."""
        env = _make_env()
        with pytest.raises(ValueError, match="purely additive"):
            env.apply(
                Preset(
                    name="alt-cc",
                    category="variant",
                    exclusive_group="build_variant",
                    contributions=(ToolContribution("cc", cmd="other-cc"),),
                )
            )
        assert env.applied_presets == ()

    def test_unapply_tolerates_externally_removed_tokens(self, test_project):
        """An imperative preset or manual edit may have removed a token the
        group preset contributed; switching still works and preserves
        user-added duplicates by count."""
        env = _make_env()
        env.apply(
            Preset(
                name="debug",
                category="variant",
                exclusive_group="build_variant",
                contributions=(ToolContribution("cc", flags=("-O0", "-g")),),
            )
        )
        env.cc.flags.remove("-O0")  # imperative/manual interference
        env.cc.flags.append("-g")  # user's own -g, must survive the switch
        env.apply(
            Preset(
                name="release",
                category="variant",
                exclusive_group="build_variant",
                contributions=(ToolContribution("cc", flags=("-O2",)),),
            )
        )
        assert "-O2" in env.cc.flags
        assert env.cc.flags.count("-g") == 1

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
