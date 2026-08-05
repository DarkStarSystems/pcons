# SPDX-License-Identifier: MIT
"""Property tests for dependency-graph ordering and cycle detection.

Hand-written graph tests cover the shapes a person thinks of: a chain, a
diamond, a self-edge. These check the same guarantees over arbitrary
graphs, against a naive reference implementation.
"""

import pytest
from hypothesis import HealthCheck, given, settings

from pcons.core.errors import DependencyCycleError
from pcons.core.graph import (
    collect_build_order,
    detect_cycles_in_targets,
    topological_sort_targets,
)
from pcons.core.project import Project
from pcons.core.target import Target
from pcons.generators.generator import BaseGenerator

from .strategies import dependency_graphs, has_cycle

pytestmark = pytest.mark.fuzz

# tmp_path is per-test, not per-example, but nothing here writes to it --
# it only anchors the project root -- so sharing it across examples is safe.
# max_examples deliberately stays with the profile (see tests/conftest.py).
graph_settings = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


def make_targets(root_dir, count, edges):
    """Build `count` targets in a fresh project, wired up by `edges`.

    Each example needs its own project: target names must be unique
    within one, and Hypothesis runs many examples per test function.
    Dependencies go through private.link_libs, the edge kind that
    `Target.dependencies` reports.
    """
    Project._clear_tree()
    BaseGenerator._clear_pending()
    Project(name="fuzz", root_dir=root_dir)
    targets = [Target(f"t{i}") for i in range(count)]
    for dependent, dependency in edges:
        targets[dependent].private.link_libs.append(targets[dependency])
    return targets


@graph_settings
@given(dependency_graphs(acyclic=True))
def test_topological_sort_orders_dependencies_first(tmp_path, graph):
    count, edges = graph
    targets = make_targets(tmp_path, count, edges)

    ordered = topological_sort_targets(targets)

    assert sorted(id(t) for t in ordered) == sorted(id(t) for t in targets)
    for dependent, dependency in edges:
        assert ordered.index(targets[dependency]) < ordered.index(targets[dependent])


@graph_settings
@given(dependency_graphs(acyclic=False))
def test_topological_sort_raises_exactly_on_cycles(tmp_path, graph):
    count, edges = graph
    targets = make_targets(tmp_path, count, edges)

    if has_cycle(count, edges):
        with pytest.raises(DependencyCycleError):
            topological_sort_targets(targets)
    else:
        assert len(topological_sort_targets(targets)) == count


@graph_settings
@given(dependency_graphs(acyclic=False))
def test_detect_cycles_agrees_with_reference(tmp_path, graph):
    count, edges = graph
    targets = make_targets(tmp_path, count, edges)

    cycles = detect_cycles_in_targets(targets)

    assert bool(cycles) == has_cycle(count, edges)
    # Every reported cycle is a real closed walk in the graph.
    edge_set = {(f"fuzz::t{a}", f"fuzz::t{b}") for a, b in edges}
    for cycle in cycles:
        assert cycle[0] == cycle[-1], f"not closed: {cycle}"
        for step in zip(cycle, cycle[1:], strict=False):
            assert step in edge_set, f"invented edge {step} in {cycle}"


@graph_settings
@given(dependency_graphs(acyclic=True))
def test_build_order_is_complete_and_ordered(tmp_path, graph):
    count, edges = graph
    targets = make_targets(tmp_path, count, edges)
    successors = {i: [b for a, b in edges if a == i] for i in range(count)}

    for index, target in enumerate(targets):
        order = collect_build_order(target)

        assert order[-1] is target
        assert len(order) == len({id(t) for t in order}), f"{target.name} listed twice"

        reachable = {index}
        pending = [index]
        while pending:
            for nxt in successors[pending.pop()]:
                if nxt not in reachable:
                    reachable.add(nxt)
                    pending.append(nxt)
        assert {t.name for t in order} == {f"t{i}" for i in reachable}

        in_order = {id(t): i for i, t in enumerate(order)}
        for dependent, dependency in edges:
            if id(targets[dependent]) in in_order:
                assert (
                    in_order[id(targets[dependency])] < in_order[id(targets[dependent])]
                )
