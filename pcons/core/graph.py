# SPDX-License-Identifier: MIT
"""Dependency graph utilities.

Provides algorithms for working with the dependency graph including
topological sorting, cycle detection, and node collection.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from pcons.core.errors import DependencyCycleError, DuplicateTargetError

if TYPE_CHECKING:
    from pcons.core.node import Node
    from pcons.core.target import Target


def _index(targets: list[Target]) -> dict[str, Target]:
    """Targets by qualified name, which is what the graph walks key on.

    Args:
        targets: The targets to index.

    Returns:
        The targets, keyed by ``qualified_name``.

    Raises:
        DuplicateTargetError: Two targets answer to one qualified name. The
            walks cannot tell them apart, and folding them together would turn
            a duplicate into a bogus dependency cycle.
    """
    indexed: dict[str, Target] = {}
    for target in targets:
        first = indexed.setdefault(target.qualified_name, target)
        if first is not target:
            raise DuplicateTargetError(target.qualified_name, first, target)
    return indexed


#: Target types that may link each other in a cycle. Each contributes at
#: most an archive or objects to a link, which every linker can resolve in a
#: cycle (GNU ld with a group, the others by rescanning); there is no build
#: order between them because compiling one needs only the other's headers.
LINKABLE_IN_A_CYCLE: frozenset[str] = frozenset(
    {"static_library", "object", "interface"}
)


def cycle_reason(members: list[Target]) -> str | None:
    """Why *members* may not form a cycle, or None when they may."""
    for target in members:
        if target.target_type not in LINKABLE_IN_A_CYCLE:
            kind = target.target_type or "target"
            return (
                f"{target.qualified_name} is a {kind}; only static libraries, "
                f"object libraries and header-only libraries may link each "
                f"other in a cycle"
            )
    return None


def strongly_connected_components(
    targets: list[Target], deps_of: Callable[[Target], Iterable[Target]]
) -> list[list[Target]]:
    """Tarjan's components over *targets*, each in the order given.

    Edges outside *targets* are ignored. Components come out with
    dependencies before dependents, which is also a valid build order.
    """
    index_of = {id(t): i for i, t in enumerate(targets)}
    order: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[Target] = []
    components: list[list[Target]] = []

    def visit(target: Target) -> None:
        order[id(target)] = low[id(target)] = len(order)
        stack.append(target)
        on_stack.add(id(target))
        for dep in deps_of(target):
            if id(dep) not in index_of:
                continue
            if id(dep) not in order:
                visit(dep)
                low[id(target)] = min(low[id(target)], low[id(dep)])
            elif id(dep) in on_stack:
                low[id(target)] = min(low[id(target)], order[id(dep)])
        if low[id(target)] == order[id(target)]:
            component: list[Target] = []
            while True:
                member = stack.pop()
                on_stack.discard(id(member))
                component.append(member)
                if member is target:
                    break
            component.sort(key=lambda t: index_of[id(t)])
            components.append(component)

    for target in targets:
        if id(target) not in order:
            visit(target)
    return components


def topological_sort_targets(targets: list[Target]) -> list[Target]:
    """Sort targets in dependency order (dependencies first) via Kahn's algorithm.

    A cycle among targets that may link each other (LINKABLE_IN_A_CYCLE) is
    one unit in the order: its members come out together, in the order they
    were declared. Any other cycle is an error.

    Raises:
        DependencyCycleError: If there's a cycle in the dependency graph
            that is not a link cycle among static libraries.
        DuplicateTargetError: If two targets share a qualified name.
    """
    if not targets:
        return []

    _index(targets)  # refuses two targets with one qualified name
    components = strongly_connected_components(targets, lambda t: t.dependencies)
    unit_of: dict[str, int] = {}
    for number, members in enumerate(components):
        if len(members) > 1:
            reason = cycle_reason(members)
            if reason is not None:
                names = [m.qualified_name for m in members]
                raise DependencyCycleError([*names, names[0]], reason=reason)
        for member in members:
            unit_of[member.qualified_name] = number

    dependents: dict[int, set[int]] = {i: set() for i in range(len(components))}
    in_degree: dict[int, int] = dict.fromkeys(range(len(components)), 0)
    for target in targets:
        unit = unit_of[target.qualified_name]
        for dep in target.dependencies:
            dep_unit = unit_of.get(dep.qualified_name)
            if dep_unit is None or dep_unit == unit:
                continue
            if unit not in dependents[dep_unit]:
                dependents[dep_unit].add(unit)
                in_degree[unit] += 1

    # Units with no dependencies first, in declaration order of their members
    position = {id(t): i for i, t in enumerate(targets)}
    first_index = {i: position[id(members[0])] for i, members in enumerate(components)}
    queue = deque(
        sorted(
            (i for i, count in in_degree.items() if count == 0),
            key=lambda i: first_index[i],
        )
    )
    result: list[Target] = []
    while queue:
        unit = queue.popleft()
        result.extend(components[unit])
        for dependent in sorted(dependents[unit], key=lambda i: first_index[i]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    assert len(result) == len(targets), "condensation is acyclic"
    return result


def detect_cycles_in_targets(targets: list[Target]) -> list[list[str]]:
    """Find all cycles in the target dependency graph (DFS back edges).

    Returns:
        List of cycles, each a list of target names; empty if none.

    Raises:
        DuplicateTargetError: If two targets share a qualified name.
    """
    cycles: list[list[str]] = []
    target_map = _index(targets)

    # Colors: 0=white (unvisited), 1=gray (in progress), 2=black (done)
    colors: dict[str, int] = dict.fromkeys(target_map, 0)
    path: list[str] = []

    def dfs(name: str) -> None:
        colors[name] = 1  # Gray - in progress
        path.append(name)

        for dep in target_map[name].dependencies:
            dep_name = dep.qualified_name
            if dep_name not in colors:
                # External dependency, skip
                continue
            if colors[dep_name] == 1:
                # Found a back edge - there's a cycle
                cycle_start = path.index(dep_name)
                cycles.append([*path[cycle_start:], dep_name])
            elif colors[dep_name] == 0:
                dfs(dep_name)

        path.pop()
        colors[name] = 2  # Black - done

    for name in target_map:
        if colors[name] == 0:
            dfs(name)

    # A cycle static libraries may form is not a fault; see LINKABLE_IN_A_CYCLE.
    return [
        cycle
        for cycle in cycles
        if cycle_reason([target_map[name] for name in cycle]) is not None
    ]


def topological_sort_nodes(nodes: list[Node]) -> list[Node]:
    """Sort nodes in dependency order (dependencies first).

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
    """Collect all nodes from targets and their dependencies, recursively."""
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
    """Return all targets needed to build *target*, dependencies first,
    ending with the target itself.
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
