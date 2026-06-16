# SPDX-License-Identifier: MIT
"""Tests for derived flag provenance (Environment.explain / ToolConfig.explain)."""

from __future__ import annotations

import pytest

from pcons.core.environment import Environment
from pcons.core.preset import Preset, ToolContribution


def _make_env() -> Environment:
    env = Environment()
    for name in ("cc", "cxx"):
        tool = env.add_tool(name)
        tool.set("cmd", name)
        tool.set("flags", [])
        tool.set("defines", [])
    return env


def _release() -> Preset:
    return Preset(
        name="release",
        category="variant",
        exclusive_group="build_variant",
        contributions=(
            ToolContribution("cc", flags=("-O2",), defines=("NDEBUG",)),
            ToolContribution("cxx", flags=("-O2",), defines=("NDEBUG",)),
        ),
    )


def _warnings() -> Preset:
    return Preset(
        name="warnings",
        category="feature",
        contributions=(
            ToolContribution("cc", flags=("-Wall",)),
            ToolContribution("cxx", flags=("-Wall",)),
        ),
    )


def _src(explanation, tool, token):
    """Return the source attributed to a (tool, token) in an Explanation."""
    for row in explanation.rows:
        if row.tool == tool and row.token == token:
            return row.source
    raise AssertionError(f"{tool} {token!r} not found in explanation")


class TestAttribution:
    def test_attributes_each_flag_to_its_preset(self, test_project):
        env = _make_env()
        env.apply(_release())
        env.apply(_warnings())
        exp = env.explain()
        assert _src(exp, "cc", "-O2") == "release"
        assert _src(exp, "cc", "-Wall") == "warnings"
        assert _src(exp, "cc", "NDEBUG") == "release"

    def test_manual_flags_labelled_manual(self, test_project):
        env = _make_env()
        env.apply(_release())
        env.cxx.flags.append("-std=c++20")  # direct edit, not via a preset
        exp = env.explain("cxx")
        assert _src(exp, "cxx", "-std=c++20") is None
        assert _src(exp, "cxx", "-O2") == "release"

    def test_cmd_replacement_attributed(self, test_project):
        env = _make_env()
        env.apply(
            Preset(
                name="wasm32-emscripten",
                category="target",
                arch="wasm32",
                contributions=(ToolContribution("cc", cmd="emcc"),),
            )
        )
        exp = env.explain("cc")
        cmd_rows = [r for r in exp.rows if r.var == "cmd"]
        assert len(cmd_rows) == 1
        assert cmd_rows[0].token == "emcc"
        assert cmd_rows[0].source == "wasm32-emscripten"

    def test_unreplaced_cmd_not_shown(self, test_project):
        env = _make_env()
        env.apply(_warnings())
        exp = env.explain("cc")
        assert not [r for r in exp.rows if r.var == "cmd"]

    def test_single_tool_scope(self, test_project):
        env = _make_env()
        env.apply(_release())
        exp = env.explain("cc")
        assert {r.tool for r in exp.rows} == {"cc"}


class TestStr:
    def test_str_shows_origin(self, test_project):
        env = _make_env()
        env.apply(_release())
        text = str(env.explain("cc"))
        assert "-O2" in text
        assert "<- release (variant)" in text

    def test_str_marks_manual(self, test_project):
        env = _make_env()
        env.cc.flags.append("-fcustom")
        assert "(manual)" in str(env.explain("cc"))

    def test_empty_is_readable(self, test_project):
        env = Environment()
        assert str(env.explain()) == "(no flags)"


class TestToolConfigExplain:
    def test_tool_explain_matches_env_explain(self, test_project):
        env = _make_env()
        env.apply(_release())
        assert str(env.cc.explain()) == str(env.explain("cc"))

    def test_detached_toolconfig_raises(self, test_project):
        from pcons.core.toolconfig import ToolConfig

        tc = ToolConfig("cc", flags=["-O2"])
        with pytest.raises(RuntimeError, match="not attached"):
            tc.explain()


class TestCloneSurvival:
    def test_explain_survives_clone(self, test_project):
        env = _make_env()
        env.apply(_release())
        clone = env.clone()
        assert _src(clone.explain("cc"), "cc", "-O2") == "release"


class TestToolchainBaseline:
    """Toolchain base flags are attributed to the toolchain, not (manual)."""

    def test_base_flags_attributed_to_toolchain(self, test_project):
        from pcons.core.environment import Environment
        from pcons.tools.toolchain import BaseToolchain

        class FakeToolchain(BaseToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

            def setup(self, env: Environment) -> None:
                cc = env.add_tool("cc")
                cc.set("cmd", "cc")
                cc.set("flags", ["/nologo"])
                cc.set("defines", [])

        env = Environment(toolchain=FakeToolchain("faketc"))
        env.apply(_release())
        env.cc.flags.append("-std=c++20")  # user choice -> manual
        exp = env.explain("cc")
        assert _src(exp, "cc", "/nologo") == "faketc"
        assert _src(exp, "cc", "-O2") == "release"
        assert _src(exp, "cc", "-std=c++20") is None
        # category shows in the rendered table
        assert "<- faketc (toolchain)" in str(exp)

    def test_baseline_survives_clone(self, test_project):
        from pcons.core.environment import Environment
        from pcons.tools.toolchain import BaseToolchain

        class FakeToolchain(BaseToolchain):
            def _configure_tools(self, config: object) -> bool:
                return True

            def setup(self, env: Environment) -> None:
                cc = env.add_tool("cc")
                cc.set("flags", ["/nologo"])
                cc.set("defines", [])

        env = Environment(toolchain=FakeToolchain("faketc"))
        clone = env.clone()
        assert _src(clone.explain("cc"), "cc", "/nologo") == "faketc"
