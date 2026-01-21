# SPDX-License-Identifier: MIT
"""Tests for multi-output builder support."""

from pathlib import Path

import pytest

from pcons.core.builder import (
    MultiOutputBuilder,
    OutputGroup,
    OutputSpec,
)
from pcons.core.environment import Environment
from pcons.core.node import FileNode


class TestOutputSpec:
    def test_creation(self):
        spec = OutputSpec("primary", ".dll")
        assert spec.name == "primary"
        assert spec.suffix == ".dll"
        assert spec.implicit is False
        assert spec.required is True

    def test_creation_with_implicit(self):
        spec = OutputSpec("export_file", ".exp", implicit=True)
        assert spec.name == "export_file"
        assert spec.suffix == ".exp"
        assert spec.implicit is True
        assert spec.required is True

    def test_creation_with_optional(self):
        spec = OutputSpec("debug_info", ".pdb", implicit=True, required=False)
        assert spec.name == "debug_info"
        assert spec.suffix == ".pdb"
        assert spec.implicit is True
        assert spec.required is False


class TestOutputGroup:
    def test_creation(self):
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        nodes = {"primary": node1, "import_lib": node2}
        group = OutputGroup(nodes, "primary")

        assert group.primary is node1
        assert group["primary"] is node1
        assert group["import_lib"] is node2

    def test_attribute_access(self):
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        nodes = {"primary": node1, "import_lib": node2}
        group = OutputGroup(nodes, "primary")

        assert group.import_lib is node2

    def test_attribute_error_for_unknown(self):
        node1 = FileNode("test.dll")
        nodes = {"primary": node1}
        group = OutputGroup(nodes, "primary")

        with pytest.raises(AttributeError, match="No output named 'unknown'"):
            _ = group.unknown

    def test_iteration(self):
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        nodes = {"primary": node1, "import_lib": node2}
        group = OutputGroup(nodes, "primary")

        items = list(group)
        assert len(items) == 2
        assert node1 in items
        assert node2 in items

    def test_len(self):
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        node3 = FileNode("test.exp")
        nodes = {"primary": node1, "import_lib": node2, "export_file": node3}
        group = OutputGroup(nodes, "primary")

        assert len(group) == 3

    def test_keys(self):
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        nodes = {"primary": node1, "import_lib": node2}
        group = OutputGroup(nodes, "primary")

        keys = group.keys()
        assert "primary" in keys
        assert "import_lib" in keys

    def test_values(self):
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        nodes = {"primary": node1, "import_lib": node2}
        group = OutputGroup(nodes, "primary")

        values = group.values()
        assert node1 in values
        assert node2 in values

    def test_items(self):
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        nodes = {"primary": node1, "import_lib": node2}
        group = OutputGroup(nodes, "primary")

        items = group.items()
        assert ("primary", node1) in items
        assert ("import_lib", node2) in items

    def test_repr(self):
        node1 = FileNode("test.dll")
        nodes = {"primary": node1}
        group = OutputGroup(nodes, "primary")

        repr_str = repr(group)
        assert "OutputGroup" in repr_str
        assert "primary" in repr_str

    def test_list_compatibility(self):
        """OutputGroup should be usable in list operations."""
        node1 = FileNode("test.dll")
        node2 = FileNode("test.lib")
        nodes = {"primary": node1, "import_lib": node2}
        group = OutputGroup(nodes, "primary")

        # Should work with list concatenation
        result: list[FileNode] = []
        result.extend(group)
        assert len(result) == 2


class TestMultiOutputBuilder:
    def test_creation(self):
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )
        assert builder.name == "SharedLibrary"
        assert builder.tool_name == "link"
        # target_suffixes should be set from primary output
        assert builder.target_suffixes == [".dll"]

    def test_outputs_property(self):
        outputs = [
            OutputSpec("primary", ".dll"),
            OutputSpec("import_lib", ".lib"),
            OutputSpec("export_file", ".exp", implicit=True),
        ]
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=outputs,
            src_suffixes=[".obj"],
        )
        assert builder.outputs == outputs

    def test_build_returns_output_group(self):
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.sharedcmd = "$link.cmd /DLL /OUT:$$TARGET $$SOURCES"

        result = builder(env, "test.dll", ["a.obj", "b.obj"])

        assert isinstance(result, OutputGroup)
        assert len(result) == 2
        assert result.primary.path == Path("test.dll")
        assert result.import_lib.path == Path("test.lib")

    def test_build_creates_nodes_with_correct_suffixes(self):
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
                OutputSpec("export_file", ".exp", implicit=True),
            ],
            src_suffixes=[".obj"],
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.sharedcmd = "$link.cmd /DLL /OUT:$$TARGET $$SOURCES"

        result = builder(env, "build/mylib.dll", ["a.obj"])

        assert isinstance(result, OutputGroup)
        assert result.primary.path == Path("build/mylib.dll")
        assert result.import_lib.path == Path("build/mylib.lib")
        assert result.export_file.path == Path("build/mylib.exp")

    def test_primary_node_has_dependencies(self):
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.sharedcmd = "$link.cmd /DLL /OUT:$$TARGET $$SOURCES"

        source = FileNode("a.obj")
        result = builder(env, "test.dll", [source])

        assert isinstance(result, OutputGroup)
        assert source in result.primary.explicit_deps

    def test_primary_node_has_build_info(self):
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.sharedcmd = "$link.cmd /DLL /OUT:$$TARGET $$SOURCES"

        result = builder(env, "test.dll", ["a.obj"])

        assert isinstance(result, OutputGroup)
        info = result.primary._build_info
        assert info is not None
        assert info["tool"] == "link"
        assert info["command_var"] == "sharedcmd"
        assert "outputs" in info
        assert "primary" in info["outputs"]
        assert "import_lib" in info["outputs"]

    def test_output_group_is_iterable_for_backward_compat(self):
        """OutputGroup should work with list += operations."""
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.sharedcmd = "$link.cmd /DLL /OUT:$$TARGET $$SOURCES"

        result = builder(env, "test.dll", ["a.obj"])
        assert isinstance(result, OutputGroup)

        # This is the backward compat pattern
        nodes: list[FileNode] = []
        nodes.extend(result)
        assert len(nodes) == 2

    def test_secondary_nodes_reference_primary(self):
        builder = MultiOutputBuilder(
            "SharedLibrary",
            "link",
            "sharedcmd",
            outputs=[
                OutputSpec("primary", ".dll"),
                OutputSpec("import_lib", ".lib"),
            ],
            src_suffixes=[".obj"],
        )

        env = Environment()
        env.add_tool("link")
        env.link.cmd = "link.exe"
        env.link.sharedcmd = "$link.cmd /DLL /OUT:$$TARGET $$SOURCES"

        result = builder(env, "test.dll", ["a.obj"])

        assert isinstance(result, OutputGroup)
        import_lib_info = result.import_lib._build_info
        assert import_lib_info is not None
        assert "primary_node" in import_lib_info
        assert import_lib_info["primary_node"] is result.primary


class TestMultiOutputBuilderSingleSource:
    def test_single_source_mode_returns_list(self):
        """In single_source mode, returns a flat list for compatibility."""
        builder = MultiOutputBuilder(
            "Object",
            "cc",
            "objcmd",
            outputs=[
                OutputSpec("primary", ".obj"),
                OutputSpec("debug", ".pdb", implicit=True),
            ],
            src_suffixes=[".c"],
            single_source=True,
        )

        env = Environment()
        env.add_tool("cc")
        env.cc.cmd = "cl.exe"
        env.cc.objcmd = "$cc.cmd /c /Fo$$TARGET $$SOURCE"

        result = builder(env, None, ["a.c", "b.c"])

        # In single_source mode, returns a list (flattened from multiple OutputGroups)
        assert isinstance(result, list)
        # 2 sources * 2 outputs each = 4 total nodes
        assert len(result) == 4
