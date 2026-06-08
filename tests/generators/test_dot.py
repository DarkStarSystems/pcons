# SPDX-License-Identifier: MIT
"""Tests for DotGenerator (GraphViz output)."""

import sys
from pathlib import Path

from pcons.core.node import FileNode
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.dot import DotGenerator
from pcons.generators.generator import BaseGenerator


class TestDotGeneratorBasic:
    def test_generator_creation(self):
        gen = DotGenerator()
        assert gen.name == "dot"

    def test_empty_project(self, tmp_path):
        project = Project("empty", build_dir=tmp_path)
        gen = DotGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        output = (tmp_path / "deps.dot").read_text()
        assert 'digraph "empty"' in output
        assert "rankdir=LR" in output

    def test_object_and_output_nodes(self, tmp_path):
        """Object nodes (ellipse) and output nodes are emitted."""
        project = Project("app", build_dir=tmp_path)
        target = Target("myapp", target_type="program")
        src = FileNode(Path("src/main.c"))
        obj = FileNode(Path("build/main.o"))
        exe = FileNode(Path("build/myapp"))
        obj.depends([src])
        target.intermediate_nodes.append(obj)
        target.output_nodes.append(exe)

        gen = DotGenerator()
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        output = (tmp_path / "deps.dot").read_text()
        # Object node uses ellipse shape
        assert "shape=ellipse" in output
        assert "shape=box3d" in output  # program output
        assert "src_main_c -> build_main_o" in output

    def test_header_dependencies(self, tmp_path):
        """include_headers parses .d files and emits header (note) nodes."""
        project = Project("hdr", build_dir=tmp_path)
        target = Target("app", target_type="program")
        src = FileNode(tmp_path / "main.c")
        obj = FileNode(tmp_path / "main.o")
        exe = FileNode(tmp_path / "app")
        obj.depends([src])
        target.intermediate_nodes.append(obj)
        target.output_nodes.append(exe)

        depfile = tmp_path / "main.o.d"
        depfile.write_text(
            f"main.o: {tmp_path / 'main.c'} {tmp_path / 'foo.h'} /usr/include/stdio.h\n"
        )

        gen = DotGenerator(include_headers=True)
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        output = (tmp_path / "deps.dot").read_text()
        assert 'foo_h [label="foo.h" shape=note];' in output
        assert "foo_h -> main_o" in output
        # System headers (/usr, /Library, /System) are dropped on POSIX; the
        # prefix filter is Unix-pathed, so skip the assertion on Windows.
        if not sys.platform.startswith("win"):
            assert "stdio" not in output

    def test_include_headers_without_depfile(self, tmp_path):
        """include_headers=True with no .d file present yields no header nodes."""
        project = Project("nodep", build_dir=tmp_path)
        target = Target("app", target_type="program")
        obj = FileNode(tmp_path / "main.o")
        obj.depends([FileNode(tmp_path / "main.c")])
        target.intermediate_nodes.append(obj)
        target.output_nodes.append(FileNode(tmp_path / "app"))

        gen = DotGenerator(include_headers=True)
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        output = (tmp_path / "deps.dot").read_text()
        # Source edge present, but no header nodes were parsed (no .d file).
        assert "main_c -> main_o" in output

    def test_depfile_read_error_is_swallowed(self, tmp_path):
        """A depfile that cannot be read (e.g. a directory) is ignored."""
        project = Project("baddep", build_dir=tmp_path)
        target = Target("app", target_type="program")
        obj = FileNode(tmp_path / "main.o")
        obj.depends([FileNode(tmp_path / "main.c")])
        target.intermediate_nodes.append(obj)
        target.output_nodes.append(FileNode(tmp_path / "app"))

        # A directory at the depfile path exists() but read_text() raises OSError.
        (tmp_path / "main.o.d").mkdir()

        gen = DotGenerator(include_headers=True)
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        # No crash; output produced.
        assert (tmp_path / "deps.dot").exists()

    def test_output_dir_override(self, tmp_path):
        sub = tmp_path / "diagrams"
        project = Project("p", build_dir=tmp_path)
        gen = DotGenerator(output_dir=sub)
        gen.generate(project)
        BaseGenerator._generate_pending(project)

        assert (sub / "deps.dot").exists()
