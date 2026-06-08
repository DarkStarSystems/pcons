# SPDX-License-Identifier: MIT
"""Tests for pcons.tools.tool."""

from pathlib import Path

from pcons.configure.config import Configure, ProgramInfo
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

    def test_setup_creates_namespace(self, test_project):  # noqa: F811
        tool = MockTool()
        env = Environment()

        tool.setup(env)

        assert env.has_tool("mock")
        assert env.mock.cmd == "mock-compiler"

    def test_setup_attaches_builders(self, test_project):  # noqa: F811
        tool = MockTool()
        env = Environment()

        tool.setup(env)

        # Builder should be callable from tool config
        assert hasattr(env.mock, "Compile")
        assert isinstance(env.mock.Compile, BuilderMethod)


class TestFindToolConfig:
    """Tests for BaseTool._find_tool_config (the shared configure helper)."""

    def test_non_configure_returns_none(self):
        tool = MockTool()
        assert tool._find_tool_config(object(), "gcc") is None

    def test_program_found(self, tmp_path):
        tool = MockTool()
        config = Configure(build_dir=tmp_path)
        config.find_program = (  # type: ignore[method-assign]
            lambda name, hints=None, version_flag="--version": ProgramInfo(
                path=Path("/usr/bin/gcc")
            )
        )
        cfg = tool._find_tool_config(config, "gcc")
        assert cfg is not None
        assert cfg.cmd == str(Path("/usr/bin/gcc"))

    def test_with_version(self, tmp_path):
        tool = MockTool()
        config = Configure(build_dir=tmp_path)
        config.find_program = (  # type: ignore[method-assign]
            lambda name, hints=None, version_flag="--version": ProgramInfo(
                path=Path("/usr/bin/gcc"), version="14.2.1"
            )
        )
        cfg = tool._find_tool_config(config, "gcc", with_version=True)
        assert cfg is not None
        assert cfg.version == "14.2.1"

    def test_not_found_returns_none(self, tmp_path):
        tool = MockTool()
        config = Configure(build_dir=tmp_path)
        config.find_program = (  # type: ignore[method-assign]
            lambda name, hints=None, version_flag="--version": None
        )
        assert tool._find_tool_config(config, "nope") is None

    def test_falls_through_to_second_candidate(self, tmp_path):
        tool = MockTool()
        config = Configure(build_dir=tmp_path)
        found = {"first": None, "second": ProgramInfo(path=Path("/usr/bin/second"))}

        def fake_find(name, hints=None, version_flag="--version"):
            return found[name]

        config.find_program = fake_find  # type: ignore[method-assign]
        cfg = tool._find_tool_config(config, "first", "second")
        assert cfg is not None
        assert cfg.cmd == str(Path("/usr/bin/second"))


class TestBuilderMethod:
    def test_call_with_string_source(self, test_project):  # noqa: F811
        tool = MockTool()
        env = Environment()
        tool.setup(env)

        result = env.mock.Compile("out.out", "input.mock")

        assert len(result) == 1

    def test_call_with_list_sources(self, test_project):  # noqa: F811
        tool = MockTool()
        env = Environment()
        tool.setup(env)

        result = env.mock.Compile(None, ["a.mock", "b.mock"])

        assert len(result) == 2

    def test_call_with_no_sources(self, test_project):  # noqa: F811
        tool = MockTool()
        env = Environment()
        tool.setup(env)

        result = env.mock.Compile("out.out", None)

        # No sources means no targets (for single_source=True)
        assert len(result) == 0
