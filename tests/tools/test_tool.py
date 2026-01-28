# SPDX-License-Identifier: MIT
"""Tests for pcons.tools.tool."""

from pcons.core.builder import Builder, CommandBuilder
from pcons.core.environment import Environment
from pcons.tools.tool import BaseTool, BuilderMethod, Tool


class MockTool(BaseTool):
    """A mock tool for testing."""

    def __init__(self) -> None:
        super().__init__("mock", language="c")

    def default_vars(self) -> dict[str, object]:
        return {
            "cmd": "mock-compiler",
            "flags": [],
        }

    def builders(self) -> dict[str, Builder]:
        return {
            "Compile": CommandBuilder(
                "Compile",
                "mock",
                "cmdline",
                src_suffixes=[".mock"],
                target_suffixes=[".out"],
                language="c",
                single_source=True,
            )
        }


class TestToolProtocol:
    def test_base_tool_is_tool(self):
        tool = MockTool()
        assert isinstance(tool, Tool)


class TestBaseTool:
    def test_properties(self):
        tool = MockTool()
        assert tool.name == "mock"
        assert tool.language == "c"

    def test_default_vars(self):
        tool = MockTool()
        defaults = tool.default_vars()
        assert defaults["cmd"] == "mock-compiler"
        assert defaults["flags"] == []

    def test_builders(self):
        tool = MockTool()
        builders = tool.builders()
        assert "Compile" in builders

    def test_setup_creates_namespace(self):
        tool = MockTool()
        env = Environment()

        tool.setup(env)

        assert env.has_tool("mock")
        assert env.mock.cmd == "mock-compiler"

    def test_setup_attaches_builders(self):
        tool = MockTool()
        env = Environment()

        tool.setup(env)

        # Builder should be callable from tool config
        assert hasattr(env.mock, "Compile")
        assert isinstance(env.mock.Compile, BuilderMethod)


class TestBuilderMethod:
    def test_call_with_string_source(self):
        tool = MockTool()
        env = Environment()
        tool.setup(env)

        result = env.mock.Compile("out.out", "input.mock")

        assert len(result) == 1

    def test_call_with_list_sources(self):
        tool = MockTool()
        env = Environment()
        tool.setup(env)

        result = env.mock.Compile(None, ["a.mock", "b.mock"])

        assert len(result) == 2

    def test_call_with_no_sources(self):
        tool = MockTool()
        env = Environment()
        tool.setup(env)

        result = env.mock.Compile("out.out", None)

        # No sources means no targets (for single_source=True)
        assert len(result) == 0
