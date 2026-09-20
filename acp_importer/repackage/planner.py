"""Item-level index, companion grouping, and deployment planning."""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from ..dependency.ai import AIDependencyResolver, NullAIDependencyResolver
from ..dependency.graph import detect_cycles
from .models import ACPItem, CATEGORY_ITEM_LIMITS, CATEGORY_ORDER, category_rank

LOG = logging.getLogger("Dependency Engine")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


class ItemProviderIndex:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], list[ACPItem]] = defaultdict(list)
        self._by_name: dict[str, list[ACPItem]] = defaultdict(list)

    def add(self, item: ACPItem) -> None:
        self._by_key[(item.object_type, _norm(item.object_name))].append(item)
        self._by_name[_norm(item.object_name)].append(item)
        for extra in filter(None, (item.metadata.get("LU"), item.metadata.get("PROJECTION_NAME"), item.metadata.get("PAGE_NAME"))):
            self._by_name[_norm(extra)].append(item)

    def providers_for(self, name: str, exclude_id: str) -> list[ACPItem]:
        seen: set[str] = set()
        result: list[ACPItem] = []
        for item in self._by_name.get(_norm(name), []):
            if item.id == exclude_id or item.id in seen:
                continue
            seen.add(item.id)
            result.append(item)
        return result

    def duplicate_definitions(self) -> list[dict[str, object]]:
        duplicates = []
        for (object_type, key), items in self._by_key.items():
            acps = sorted({item.source_acp for item in items})
            if len(acps) > 1:
                duplicates.append({"objectType": object_type, "object": key, "providers": acps})
        return duplicates


class UnionFind:
    def __init__(self, ids: list[str]) -> None:
        self.parent = {item_id: item_id for item_id in ids}

    def find(self, item_id: str) -> str:
        while self.parent[item_id] != item_id:
            self.parent[item_id] = self.parent[self.parent[item_id]]
            item_id = self.parent[item_id]
        return item_id

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def _lu_token(item: ACPItem) -> str:
    if item.metadata.get("LU"):
        return _norm(item.metadata["LU"])
    match = re.search(r"CustomField(?:Persistent|ReadOnly|Views)-([A-Za-z0-9_]+)", item.source_filename)
    if match:
        return _norm(match.group(1))
    match = re.search(r"Views-([A-Za-z0-9_]+)", item.object_name)
    return _norm(match.group(1) if match else "")


def identify_logical_groups(items: list[ACPItem]) -> list[list[ACPItem]]:
    """Companion groups taken from observed XML, not invented IFS relationships."""
    by_id = {item.id: item for item in items}
    union = UnionFind(list(by_id))
    projections: dict[str, list[ACPItem]] = defaultdict(list)
    events: dict[str, list[ACPItem]] = defaultdict(list)
    pages: dict[str, list[ACPItem]] = defaultdict(list)
    fields: dict[str, list[ACPItem]] = defaultdict(list)
    for item in items:
        if item.object_type in {"CUSTOM_PROJECTION", "CUSTOM_PROJCONFIG"}:
            projections[_norm(item.metadata.get("PROJECTION_NAME") or item.object_name)].append(item)
        elif item.object_type == "CUSTOM_EVENT":
            events[_norm(item.metadata.get("EVENT_ID") or item.object_name)].append(item)
        elif item.object_type == "CUSTOM_EVENT_ACTION" and "^" in item.object_name:
            parts = item.object_name.split("^")
            if len(parts) >= 2:
                events[_norm(parts[1])].append(item)
        elif item.object_type == "CUSTOM_PAGE":
            pages[_norm(item.metadata.get("PAGE_NAME") or item.object_name)].append(item)
        elif item.object_type == "CUSTOM_TAB" and item.metadata.get("ATTACHED_PAGE"):
            pages[_norm(item.metadata["ATTACHED_PAGE"])].append(item)
        elif item.object_type in {"CF_PERSISTENT", "CF_READ_ONLY", "CF_VIEWS"}:
            token = _lu_token(item)
            if token:
                fields[token].append(item)
    for bucket in (*projections.values(), *events.values(), *pages.values(), *fields.values()):
        for item in bucket[1:]:
            union.union(bucket[0].id, item.id)
    grouped: dict[str, list[ACPItem]] = defaultdict(list)
    for item in items:
        grouped[union.find(item.id)].append(item)
    return list(grouped.values())


def analyze_item_dependencies(
    items: list[ACPItem],
    index: ItemProviderIndex,
    ai_resolver: AIDependencyResolver | None = None,
) -> list[dict[str, object]]:
    resolver = ai_resolver or NullAIDependencyResolver()
    edges: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        for reference in item.references:
            name = reference["referenceName"]
            providers = [candidate for candidate in index.providers_for(name, item.id) if candidate.id != item.id]
            key = (item.id, name, ",".join(sorted(p.id for p in providers)))
            if key in seen:
                continue
            seen.add(key)
            evidence = [f"{item.object_type}:{item.object_name} references {name}"]
            acps = sorted({provider.source_acp for provider in providers})
            if not providers:
                status, confidence, provider_item = "MISSING", "MEDIUM", None
            elif len(acps) == 1 and len(providers) == 1:
                status, confidence, provider_item = "CONFIRMED", "HIGH", providers[0]
                evidence.append(f"{providers[0].source_acp} provides {providers[0].object_type}:{providers[0].object_name}")
                LOG.info("[Dependency Engine] Dependency detected: %s -> %s", item.id, providers[0].id)
            elif len({provider.id for provider in providers}) == 1:
                status, confidence, provider_item = "CONFIRMED", "HIGH", providers[0]
            else:
                status, confidence, provider_item = "AMBIGUOUS", "LOW", None
                evidence.append("Candidates: " + ", ".join(acps))
                suggestion = resolver.resolve_dependency(item.id, name, acps, evidence)
                if suggestion is not None:
                    evidence.append(f"AI suggestion (not used for packaging): {suggestion.provider}")
            edges.append({
                "consumerItem": item.id,
                "providerItem": provider_item.id if provider_item else None,
                "sourceAcp": item.source_acp,
                "providerAcp": provider_item.source_acp if provider_item else None,
                "dependencyType": reference["referenceType"],
                "referencedObject": name,
                "evidence": evidence,
                "detectionMethod": "STATIC_ANALYSIS",
                "confidence": confidence,
                "status": status,
                "candidates": acps,
            })
    return edges


def _item_graph(items: list[ACPItem], edges: list[dict[str, object]]) -> dict[str, set[str]]:
    ids = {item.id for item in items}
    graph: dict[str, set[str]] = {item.id: set() for item in items}
    for edge in edges:
        if edge["status"] != "CONFIRMED":
            continue
        consumer, provider = str(edge["consumerItem"]), str(edge.get("providerItem") or "")
        if consumer in ids and provider in ids and consumer != provider:
            graph[consumer].add(provider)
    return graph


def merge_cycles_into_groups(groups: list[list[ACPItem]], edges: list[dict[str, object]]) -> tuple[list[list[ACPItem]], list[list[str]]]:
    """Contain item cycles in one package candidate instead of silently breaking them."""
    items = [item for group in groups for item in group]
    graph = _item_graph(items, edges)
    cycles = detect_cycles([item.id for item in items], graph)
    if not cycles:
        return groups, []
    by_id = {item.id: item for item in items}
    union = UnionFind([item.id for item in items])
    for group in groups:
        for item in group[1:]:
            union.union(group[0].id, item.id)
    contained: list[list[str]] = []
    for cycle in cycles:
        unique = [node for node in cycle[:-1]]
        for node in unique[1:]:
            union.union(unique[0], node)
        contained.append(cycle)
    merged: dict[str, list[ACPItem]] = defaultdict(list)
    for item in items:
        merged[union.find(item.id)].append(item)
    return list(merged.values()), contained


def group_primary_category(group: list[ACPItem]) -> str:
    ranked = sorted(group, key=lambda item: (item.source_section == "ADDITIONAL_ITEMS", category_rank(item.object_type), item.object_name.casefold()))
    return ranked[0].object_type if ranked else "UNKNOWN"


def _item_identity(item: ACPItem) -> tuple[str, str]:
    return (item.object_type, _norm(item.metadata.get("LU") or item.object_name))


def dedupe_items(items: list[ACPItem]) -> tuple[list[ACPItem], list[str]]:
    """Keep one definition per object. Last copy wins so later ACP versions replace earlier ones."""
    chosen: dict[tuple[str, str], ACPItem] = {}
    order: list[tuple[str, str]] = []
    warnings: list[str] = []
    for item in items:
        key = _item_identity(item)
        previous = chosen.get(key)
        if previous is None:
            order.append(key)
        else:
            warnings.append(
                f"Duplicate {item.object_type} '{item.object_name}' from {item.source_acp}; "
                f"keeping this copy instead of {previous.source_acp}."
            )
        chosen[key] = item
    return [chosen[key] for key in order], warnings


def pack_groups(
    groups: list[list[ACPItem]],
    max_items: int,
    category_limits: dict[str, int] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    warnings: list[str] = []
    packages: list[dict[str, object]] = []
    by_category: dict[str, list[list[ACPItem]]] = defaultdict(list)
    for group in groups:
        by_category[group_primary_category(group)].append(group)
    limits = dict(CATEGORY_ITEM_LIMITS)
    if category_limits:
        limits.update(category_limits)
    global_sequence = 0
    for category in list(CATEGORY_ORDER) + sorted(set(by_category) - set(CATEGORY_ORDER)):
        buckets = by_category.get(category, [])
        buckets.sort(key=lambda group: (min(item.object_name.casefold() for item in group),))
        current: list[ACPItem] = []
        limit = max(1, int(limits.get(category, max_items)))
        category_index = 0

        def emit(cat: str, grouped_items: list[ACPItem], oversized: bool) -> None:
            nonlocal global_sequence, category_index
            if not grouped_items:
                return
            global_sequence += 1
            category_index += 1
            name = f"{cat}_{category_index:03d}"
            packages.append({
                "sequence": global_sequence,
                "category": cat,
                "name": name,
                "items": list(grouped_items),
                "oversized": oversized,
            })
            LOG.info("[Package Planner] Created %s", name)

        for group in buckets:
            if len(group) > limit:
                warnings.append(
                    f"Logical group in {category} has {len(group)} items which exceeds the maximum of {limit}; it was kept whole."
                )
                emit(category, current, False)
                current = []
                emit(category, list(group), True)
                continue
            if current and len(current) + len(group) > limit:
                emit(category, current, False)
                current = []
            current.extend(group)
        emit(category, current, False)
    return packages, warnings
