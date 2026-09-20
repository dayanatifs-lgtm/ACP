"""Level 3 plug-in. Do not call an LLM from the deterministic analyzer.

Adding a real resolver later must not rewrite DependencyAnalyzer or the
deployment planner: pass an AIDependencyResolver into analyze_all().
AI suggestions are attached to unresolved edges only. They never become
CONFIRMED graph edges and never change import order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AIResolution:
    provider: str
    confidence: str
    reason: str
    evidence: list[str]


class AIDependencyResolver(Protocol):
    def resolve_dependency(
        self,
        consumer_acp: str,
        unresolved_reference: str,
        candidate_providers: list[str],
        evidence: list[str],
    ) -> AIResolution | None:
        ...


class NullAIDependencyResolver:
    """Default Level 3 implementation: no suggestion, no side effects."""

    def resolve_dependency(
        self,
        consumer_acp: str,
        unresolved_reference: str,
        candidate_providers: list[str],
        evidence: list[str],
    ) -> AIResolution | None:
        return None
