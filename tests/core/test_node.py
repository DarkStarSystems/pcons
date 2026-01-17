# SPDX-License-Identifier: MIT
"""Tests for pcons.core.node."""

from pathlib import Path

from pcons.core.node import AliasNode, DirNode, FileNode, ValueNode
from pcons.util.source_location import SourceLocation


def normalize_path(p: str) -> str:
    """Normalize path separators for cross-platform comparison.

    On Windows, Path('/path/to/file') may convert forward slashes to backslashes.
    This function normalizes the string representation back to forward slashes.
    """
    # Replace backslashes with forward slashes
    result = p.replace("\\", "/")
    # Clean up any doubled slashes that might result
    while "//" in result:
        result = result.replace("//", "/")
    return result


class TestFileNode:
    def test_creation_with_string(self):
        node = FileNode("/path/to/file.cpp")
        assert node.path == Path("/path/to/file.cpp")
        # On Windows, Path converts forward slashes to backslashes
        # So we compare normalized paths
        assert normalize_path(node.name) == "/path/to/file.cpp"
        assert node.suffix == ".cpp"

    def test_creation_with_path(self):
        node = FileNode(Path("/path/to/file.cpp"))
        assert node.path == Path("/path/to/file.cpp")

    def test_exists(self, tmp_path):
        # Existing file
        existing = tmp_path / "exists.txt"
        existing.write_text("content")
        node1 = FileNode(existing)
        assert node1.exists() is True

        # Non-existing file
        node2 = FileNode(tmp_path / "missing.txt")
        assert node2.exists() is False

    def test_is_source_by_default(self):
        node = FileNode("file.cpp")
        assert node.is_source is True
        assert node.is_target is False
        assert node.builder is None

    def test_equality_and_hash(self):
        node1 = FileNode("/path/to/file.cpp")
        node2 = FileNode("/path/to/file.cpp")
        node3 = FileNode("/path/to/other.cpp")

        assert node1 == node2
        assert node1 != node3
        assert hash(node1) == hash(node2)
        assert hash(node1) != hash(node3)

        # Can use as dict key
        d = {node1: "value"}
        assert d[node2] == "value"

    def test_repr(self):
        node = FileNode("/path/to/file.cpp")
        # On Windows, the path will use backslashes, so normalize for comparison
        assert normalize_path(repr(node)) == "FileNode('/path/to/file.cpp')"

    def test_tracks_source_location(self):
        node = FileNode("file.cpp")
        assert node.defined_at is not None
        # Just verify it captures some location (the exact frame depends on call stack)
        assert node.defined_at.lineno > 0
        assert node.defined_at.filename is not None

    def test_custom_source_location(self):
        loc = SourceLocation("custom.py", 99)
        node = FileNode("file.cpp", defined_at=loc)
        assert node.defined_at == loc


class TestDirNode:
    def test_creation(self):
        node = DirNode("/path/to/dir")
        assert node.path == Path("/path/to/dir")
        # On Windows, Path converts forward slashes to backslashes
        assert normalize_path(node.name) == "/path/to/dir"
        assert node.members == []

    def test_exists(self, tmp_path):
        # Existing directory
        existing = tmp_path / "subdir"
        existing.mkdir()
        node1 = DirNode(existing)
        assert node1.exists() is True

        # Non-existing directory
        node2 = DirNode(tmp_path / "missing")
        assert node2.exists() is False

        # File (not a directory)
        file = tmp_path / "file.txt"
        file.write_text("content")
        node3 = DirNode(file)
        assert node3.exists() is False

    def test_members(self):
        dir_node = DirNode("/path/to/dir")
        file1 = FileNode("/path/to/dir/a.txt")
        file2 = FileNode("/path/to/dir/b.txt")

        dir_node.add_member(file1)
        dir_node.add_member(file2)

        assert len(dir_node.members) == 2
        assert file1 in dir_node.members
        assert file2 in dir_node.members

    def test_equality_and_hash(self):
        node1 = DirNode("/path/to/dir")
        node2 = DirNode("/path/to/dir")
        node3 = DirNode("/path/to/other")

        assert node1 == node2
        assert node1 != node3
        assert hash(node1) == hash(node2)


class TestValueNode:
    def test_creation(self):
        node = ValueNode("config_hash", "abc123")
        assert node.value_name == "config_hash"
        assert node.value == "abc123"
        assert node.name == "Value(config_hash)"

    def test_set_value(self):
        node = ValueNode("version")
        assert node.value is None

        node.set_value("1.2.3")
        assert node.value == "1.2.3"

    def test_equality_and_hash(self):
        node1 = ValueNode("version", "1.0")
        node2 = ValueNode("version", "2.0")  # Same name, different value
        node3 = ValueNode("other", "1.0")

        # Equality is by name only
        assert node1 == node2
        assert node1 != node3
        assert hash(node1) == hash(node2)


class TestAliasNode:
    def test_creation(self):
        node = AliasNode("all")
        assert node.alias_name == "all"
        assert node.name == "all"
        assert node.targets == []

    def test_creation_with_targets(self):
        target1 = FileNode("a.o")
        target2 = FileNode("b.o")
        alias = AliasNode("objects", [target1, target2])

        assert len(alias.targets) == 2
        assert target1 in alias.targets

    def test_add_target(self):
        alias = AliasNode("all")
        target = FileNode("app")

        alias.add_target(target)
        assert target in alias.targets

    def test_add_targets(self):
        alias = AliasNode("all")
        targets = [FileNode("a"), FileNode("b")]

        alias.add_targets(targets)
        assert len(alias.targets) == 2

    def test_equality_and_hash(self):
        node1 = AliasNode("all")
        node2 = AliasNode("all")
        node3 = AliasNode("test")

        assert node1 == node2
        assert node1 != node3
        assert hash(node1) == hash(node2)


class TestNodeDependencies:
    def test_explicit_deps_initially_empty(self):
        node = FileNode("target.o")
        assert node.explicit_deps == []
        assert node.implicit_deps == []
        assert node.deps == []

    def test_depends_single_node(self):
        target = FileNode("target.o")
        source = FileNode("source.cpp")

        target.depends(source)

        assert source in target.explicit_deps
        assert source in target.deps
        assert len(target.deps) == 1

    def test_depends_multiple_nodes(self):
        target = FileNode("target.o")
        source1 = FileNode("source.cpp")
        source2 = FileNode("header.h")

        target.depends(source1, source2)

        assert source1 in target.deps
        assert source2 in target.deps
        assert len(target.deps) == 2

    def test_depends_sequence(self):
        target = FileNode("target.o")
        sources = [FileNode("a.cpp"), FileNode("b.cpp")]

        target.depends(sources)

        assert len(target.deps) == 2

    def test_deps_combines_explicit_and_implicit(self):
        target = FileNode("target.o")
        explicit = FileNode("source.cpp")
        implicit = FileNode("header.h")

        target.explicit_deps.append(explicit)
        target.implicit_deps.append(implicit)

        assert len(target.deps) == 2
        assert explicit in target.deps
        assert implicit in target.deps

    def test_dependency_chain(self):
        app = FileNode("app")
        obj = FileNode("main.o")
        src = FileNode("main.cpp")

        obj.depends(src)
        app.depends(obj)

        assert src in obj.deps
        assert obj in app.deps
        # Note: deps are not transitive automatically
        assert src not in app.deps
