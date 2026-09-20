"""Directed ACP graph: consumer -> provider. Provider must be deployed first."""

from __future__ import annotations

from collections import defaultdict

from .models import CONFIRMED, DependencyEdge
from .rules import type_priority


def adjacency(edges: list[DependencyEdge]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.status == CONFIRMED and edge.provider_acp and edge.provider_acp != edge.consumer_acp:
            graph[edge.consumer_acp].add(edge.provider_acp)
    return graph


def detect_cycles(nodes: list[str], graph: dict[str, set[str]]) -> list[list[str]]:
    """Return distinct simple cycles. Cycles are reported, never broken."""
    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> None:
        visiting.add(node)
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt not in nodes:
                continue
            if nxt in visiting:
                start = stack.index(nxt)
                cycle = stack[start:] + [nxt]
                if cycle not in cycles:
                    cycles.append(cycle)
            elif nxt not in visited:
                dfs(nxt)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in nodes:
        if node not in visited:
            dfs(node)
    return cycles


def nodes_affected_by_cycles(nodes: list[str], graph: dict[str, set[str]], cycles: list[list[str]]) -> set[str]:
    cycle_nodes = {name for cycle in cycles for name in cycle}
    blocked = set(cycle_nodes)

    def depends_on_blocked(node: str, seen: set[str]) -> bool:
        if node in blocked:
            return True
        if node in seen:
            return False
        seen.add(node)
        return any(depends_on_blocked(provider, seen) for provider in graph.get(node, ()))

    for node in nodes:
        if depends_on_blocked(node, set()):
            blocked.add(node)
    return blocked


def deployment_levels(
    nodes: list[str],
    graph: dict[str, set[str]],
    item_types: dict[str, set[str]],
) -> list[list[str]]:
    """Kahn topological levels. Packages in one level do not depend on each other."""
    remaining = {name: set(graph.get(name, ())) & set(nodes) for name in nodes}
    levels: list[list[str]] = []
    while remaining:
        ready = [name for name, deps in remaining.items() if not deps]
        if not ready:
            # Do not drop cyclic packages. Pick the next best node so every
            # valid ACP still gets a deployment slot.
            ready = [min(
                remaining,
                key=lambda name: (
                    len(remaining[name]),
                    type_priority(item_types.get(name, set())),
                    name.casefold(),
                ),
            )]
        ready.sort(key=lambda name: (type_priority(item_types.get(name, set())), name.casefold()))
        levels.append(ready)
        ready_set = set(ready)
        for name in ready:
            del remaining[name]
        for deps in remaining.values():
            deps.difference_update(ready_set)
    return levels
