"""In-memory object provider index. Ambiguous providers are preserved, never chosen."""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from .models import PackageInfo

LOG = logging.getLogger("DependencyAnalyzer")


def _norm(symbol: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", symbol.casefold())


class ObjectProviderIndex:
    def __init__(self) -> None:
        self._by_name: dict[str, list[dict[str, str]]] = defaultdict(list)

    def add_package(self, package: PackageInfo) -> None:
        seen: set[tuple[str, str, str]] = set()
        for obj in package.objects:
            self._add(obj.name, package.name, obj.type, seen)
        for symbol in package.provides:
            self._add(symbol, package.name, "SYMBOL", seen)

    def _add(self, name: str, acp: str, type_name: str, seen: set[tuple[str, str, str]]) -> None:
        key = _norm(name)
        if len(key) < 3:
            return
        fingerprint = (key, acp, type_name)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        self._by_name[key].append({"acp": acp, "type": type_name, "name": name})

    def lookup(self, name: str) -> list[dict[str, str]]:
        return list(self._by_name.get(_norm(name), []))

    def unique_acps(self, name: str, *, exclude: str) -> list[str]:
        owners = []
        for candidate in self.lookup(name):
            if candidate["acp"] == exclude:
                continue
            if candidate["acp"] not in owners:
                owners.append(candidate["acp"])
        return owners

    def as_dict(self) -> dict[str, list[dict[str, str]]]:
        return {key: list(value) for key, value in sorted(self._by_name.items())}

    def duplicate_definitions(self) -> list[dict[str, object]]:
        duplicates = []
        for key, providers in self._by_name.items():
            acps = sorted({item["acp"] for item in providers})
            if len(acps) > 1:
                duplicates.append({"object": key, "providers": acps, "types": sorted({item["type"] for item in providers})})
        return duplicates
