# SPDX-License-Identifier: MIT
"""Tests for pcons.core.graph."""

import pytest

from pcons.core.errors import DependencyCycleError
from pcons.core.graph import (
    collect_all_nodes,
    collect_build_order,
    detect_cycles_in_targets,
    topological_sort_nodes,
    topological_sort_targets,
)
from pcons.core.node import FileNode
from pcons.core.target import Target


class TestTopologicalSortTargets:
    def test_empty_list(self):
        result = topological_sort_targets([])
        assert result == []

    def test_single_target(self, test_project):  # noqa: F811
        target = Target("app")
        result = topological_sort_targets([target])
        assert result == [target]

    def test_linear_dependency(self, test_project):  # noqa: F811
        # A depends on B depends on C
        c = Target("C")
        b = Target("B")
        b.private.link_libs.append(c)
        a = Target("A")
        a.private.link_libs.append(b)

        result = topological_sort_targets([a, b, c])

        # C should come before B, B before A
        assert result.index(c) < result.index(b)
        assert result.index(b) < result.index(a)

    def test_diamond_dependency(self, test_project):  # noqa: F811
        # A depends on B and C, both depend on D
        d = Target("D")
        b = Target("B")
        b.private.link_libs.append(d)
        c = Target("C")
        c.private.link_libs.append(d)
        a = Target("A")
        a.private.link_libs.append(b)
        a.private.link_libs.append(c)

        result = topological_sort_targets([a, b, c, d])

        # D should come before B and C, both before A
        assert result.index(d) < result.index(b)
        assert result.index(d) < result.index(c)
        assert result.index(b) < result.index(a)
        assert result.index(c) < result.index(a)

    def test_cycle_raises_error(self, test_project):  # noqa: F811
        a = Target("A")
        b = Target("B")
        a.private.link_libs.append(b)
        b.private.link_libs.append(a)

        with pytest.raises(DependencyCycleError):
            topological_sort_targets([a, b])


class TestDetectCycles:
    def test_no_cycle(self, test_project):  # noqa: F811
        a = Target("A")
        b = Target("B")
        a.private.link_libs.append(b)

        cycles = detect_cycles_in_targets([a, b])
        assert cycles == []

    def test_simple_cycle(self, test_project):  # noqa: F811
        a = Target("A")
        b = Target("B")
        a.private.link_libs.append(b)
        b.private.link_libs.append(a)

        cycles = detect_cycles_in_targets([a, b])
        assert len(cycles) == 1
        assert "A" in cycles[0]
        assert "B" in cycles[0]

    def test_self_cycle(self, test_project):  # noqa: F811
        a = Target("A")
        # Self-link is now caught early by link() validation
        with pytest.raises(ValueError, match="cannot link itself"):
            a.private.link_libs.append(a)

    def test_multiple_cycles(self, test_project):  # noqa: F811
        # Two separate cycles: A<->B and C<->D
        a = Target("A")
        b = Target("B")
        a.private.link_libs.append(b)
        b.private.link_libs.append(a)

        c = Target("C")
        d = Target("D")
        c.private.link_libs.append(d)
        d.private.link_libs.append(c)

        cycles = detect_cycles_in_targets([a, b, c, d])
        assert len(cycles) == 2


class TestTopologicalSortNodes:
    def test_empty_list(self):
        result = topological_sort_nodes([])
        assert result == []

    def test_nodes_with_dependencies(self):
        a = FileNode("a.o")
        b = FileNode("b.o")
        c = FileNode("c.o")

        # a depends on b, b depends on c
        a.depends(b)
        b.depends(c)

        result = topological_sort_nodes([a, b, c])

        assert result.index(c) < result.index(b)
        assert result.index(b) < result.index(a)

    def test_node_cycle_raises_error(self):
        a = FileNode("a.o")
        b = FileNode("b.o")
        a.depends(b)
        b.depends(a)

        with pytest.raises(DependencyCycleError):
            topological_sort_nodes([a, b])


class TestCollectAllNodes:
    def test_empty_targets(self):
        nodes = collect_all_nodes([])
        assert nodes == set()

    def test_collects_from_single_target(self, test_project):  # noqa: F811
        target = Target("app")
        src = FileNode("main.c")
        out = FileNode("app")
        target.add_source(src)
        target.output_nodes.append(out)

        nodes = collect_all_nodes([target])

        assert src in nodes
        assert out in nodes

    def test_collects_from_dependencies(self, test_project):  # noqa: F811
        lib = Target("lib")
        lib_src = FileNode("lib.c")
        lib_out = FileNode("lib.o")
        lib.add_source(lib_src)
        lib.output_nodes.append(lib_out)

        app = Target("app")
        app_src = FileNode("main.c")
        app_out = FileNode("app")
        app.add_source(app_src)
        app.output_nodes.append(app_out)
        app.private.link_libs.append(lib)

        nodes = collect_all_nodes([app])

        assert lib_src in nodes
        assert lib_out in nodes
        assert app_src in nodes
        assert app_out in nodes


class TestCollectBuildOrder:
    def test_single_target(self, test_project):  # noqa: F811
        app = Target("app")
        order = collect_build_order(app)
        assert order == [app]

    def test_with_dependencies(self, test_project):  # noqa: F811
        lib = Target("lib")
        app = Target("app")
        app.private.link_libs.append(lib)

        order = collect_build_order(app)

        assert order.index(lib) < order.index(app)

    def test_diamond_dependency(self, test_project):  # noqa: F811
        base = Target("base")
        left = Target("left")
        left.private.link_libs.append(base)
        right = Target("right")
        right.private.link_libs.append(base)
        top = Target("top")
        top.private.link_libs.append(left)
        top.private.link_libs.append(right)

        order = collect_build_order(top)

        # Base should come before left and right
        assert order.index(base) < order.index(left)
        assert order.index(base) < order.index(right)
        # left and right should come before top
        assert order.index(left) < order.index(top)
        assert order.index(right) < order.index(top)
