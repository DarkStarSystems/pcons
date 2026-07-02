# SPDX-License-Identifier: MIT
"""Dependency graph utilities.

Provides algorithms for working with the dependency graph including
topological sorting, cycle detection, and node collection.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from pcons.core.errors import DependencyCycleError

if TYPE_CHECKING:
    from pcons.core.node import Node
    from pcons.core.target import Target


def topological_sort_targets(targets: list[Target]) -> list[Target]:
    """Sort targets in dependency order (dependencies first).

    Uses Kahn's algorithm for topological sorting.

    Args:
        targets: List of targets to sort.

    Returns:
        Targets in dependency order (independent targets first).

    Raises:
        DependencyCycleError: If there's a cycle in the dependency graph.
    """
    if not targets:
        return []

    # Build adjacency list and in-degree count. Keyed on qualified_name
    # (project::target) rather than the bare name, since two targets in
    # different (sub)projects may legitimately share a short name.
    # target -> set of targets that depend on it
    dependents: dict[str, set[str]] = {t.qualified_name: set() for t in targets}
    # target -> number of dependencies not yet processed
    in_degree: dict[str, int] = {t.qualified_name: 0 for t in targets}
    target_map: dict[str, Target] = {t.qualified_name: t for t in targets}

    for target in targets:
        for dep in target.dependencies:
            if dep.qualified_name in dependents:
                dependents[dep.qualified_name].add(target.qualified_name)
                in_degree[target.qualified_name] += 1

    # Start with targets that have no dependencies
    queue = deque(name for name, count in in_degree.items() if count == 0)
    result: list[Target] = []

    while queue:
        name = queue.popleft()
        result.append(target_map[name])

        # Reduce in-degree for all dependents
        for dependent_name in dependents[name]:
            in_degree[dependent_name] -= 1
            if in_degree[dependent_name] == 0:
                queue.append(dependent_name)

    # If we didn't process all targets, there's a cycle
    if len(result) != len(targets):
        cycle_nodes = [name for name, count in in_degree.items() if count > 0]
        raise DependencyCycleError(cycle_nodes)

    return result


def detect_cycles_in_targets(targets: list[Target]) -> list[list[str]]:
    """Find all cycles in the target dependency graph.

    Uses DFS with coloring to find back edges (cycles).

    Args:
        targets: Targets to check for cycles.

    Returns:
        List of cycles, where each cycle is a list of target names.
        Empty list if no cycles.
    """
    cycles: list[list[str]] = []
    # Keyed on qualified_name (project::target): two targets in different
    # (sub)projects may legitimately share a short name.
    target_map: dict[str, Target] = {t.qualified_name: t for t in targets}

    # Colors: 0=white (unvisited), 1=gray (in progress), 2=black (done)
    colors: dict[str, int] = {t.qualified_name: 0 for t in targets}
    path: list[str] = []

    def target_deps(target: Target) -> list[Target]:
        # Include implicit target deps from target.depends(other_target):
        # they are real must-resolve-before edges (see Resolver._resolve_target)
        # even though they don't propagate into .dependencies.
        return [
            *target.dependencies,
            *target._implicit_target_deps,
            *target._implicit_target_deps_output_only,
        ]

    def dfs(name: str) -> None:
        colors[name] = 1  # Gray - in progress
        path.append(name)

        target = target_map.get(name)
        if target:
            for dep in target_deps(target):
                dep_name = dep.qualified_name
                if dep_name not in colors:
                    # External dependency, skip
                    continue
                if colors[dep_name] == 1:
                    # Found a back edge - there's a cycle
                    cycle_start = path.index(dep_name)
                    cycles.append(path[cycle_start:] + [dep_name])
                elif colors[dep_name] == 0:
                    dfs(dep_name)

        path.pop()
        colors[name] = 2  # Black - done

    for target in targets:
        if colors[target.qualified_name] == 0:
            dfs(target.qualified_name)

    return cycles


def topological_sort_nodes(nodes: list[Node]) -> list[Node]:
    """Sort nodes in dependency order (dependencies first).

    Args:
        nodes: List of nodes to sort.

    Returns:
        Nodes in dependency order.

    Raises:
        DependencyCycleError: If there's a cycle in the dependency graph.
    """
    if not nodes:
        return []

    # Use node names as keys
    node_map: dict[str, Node] = {n.name: n for n in nodes}
    # node -> set of nodes that depend on it
    dependents: dict[str, set[str]] = {n.name: set() for n in nodes}
    # node -> number of dependencies
    in_degree: dict[str, int] = {n.name: 0 for n in nodes}

    for node in nodes:
        for dep in node.deps:
            if dep.name in dependents:
                dependents[dep.name].add(node.name)
                in_degree[node.name] += 1

    # Start with nodes that have no dependencies
    queue = deque(name for name, count in in_degree.items() if count == 0)
    result: list[Node] = []

    while queue:
        name = queue.popleft()
        result.append(node_map[name])

        for dependent_name in dependents[name]:
            in_degree[dependent_name] -= 1
            if in_degree[dependent_name] == 0:
                queue.append(dependent_name)

    if len(result) != len(nodes):
        cycle_nodes = [name for name, count in in_degree.items() if count > 0]
        raise DependencyCycleError(cycle_nodes)

    return result


def collect_all_nodes(targets: list[Target]) -> set[Node]:
    """Collect all nodes from a list of targets.

    Recursively collects nodes from targets and their dependencies.

    Args:
        targets: Targets to collect nodes from.

    Returns:
        Set of all nodes.
    """
    result: set[Node] = set()
    visited_targets: set[str] = set()

    def collect_from_target(target: Target) -> None:
        if target.qualified_name in visited_targets:
            return
        visited_targets.add(target.qualified_name)

        # Add this target's nodes and sources
        result.update(target.nodes)
        result.update(target.sources)

        # Recursively collect from dependencies
        for dep in target.dependencies:
            collect_from_target(dep)

    for target in targets:
        collect_from_target(target)

    return result


def collect_build_order(target: Target) -> list[Target]:
    """Get all targets needed to build a given target, in build order.

    Returns targets in the order they should be built (dependencies first).

    Args:
        target: The target to build.

    Returns:
        List of targets in build order, ending with the given target.
    """
    all_targets: list[Target] = []
    visited: set[str] = set()

    def collect(t: Target) -> None:
        if t.qualified_name in visited:
            return
        visited.add(t.qualified_name)

        # Collect dependencies first
        for dep in t.dependencies:
            collect(dep)

        all_targets.append(t)

    collect(target)
    return all_targets
