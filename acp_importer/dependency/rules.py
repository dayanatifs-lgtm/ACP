"""Level 2: configurable IFS relationship rules. Rules must not invent links."""

from __future__ import annotations

from dataclasses import dataclass

# Lower number = earlier within a deployment level when the graph is otherwise equal.
TYPE_RANK: dict[str, int] = {
    "CUSTOM_LU": 10,
    "CUSTOM_ENUMERATION": 15,
    "CF_PERSISTENT": 20,
    "CF_READ_ONLY": 25,
    "CF_VIEW": 25,
    "CUSTOM_FIELD": 25,
    "CUSTOM_MENU": 30,
    "CUSTOM_PAGE": 35,
    "CUSTOM_TAB": 35,
    "CUSTOM_EVENT": 40,
    "CUSTOM_EVENT_ACTION": 45,
    "PROJECTION_CONFIGURATION": 50,
    "PROJECTION_ACTION": 55,
    "WORKFLOW": 60,
    "CUSTOM_COMMAND": 65,
    "APPEARANCE": 70,
    "NAVIGATOR": 75,
}


@dataclass(frozen=True)
class DependencyRule:
    name: str
    source_object_type: str
    target_object_type: str
    matching_strategy: str
    priority: int
    detection_method: str


# Only relationships already observed in ACP XML (see scanner.extract_from_xml).
RULES: tuple[DependencyRule, ...] = (
    DependencyRule("explicit_package_reference", "PACKAGE", "PACKAGE", "PACKAGE_NAME", 10, "STATIC_ANALYSIS"),
    DependencyRule("event_action_requires_event", "CUSTOM_EVENT_ACTION", "CUSTOM_EVENT", "EXACT_NAME", 20, "RULE_ENGINE"),
    DependencyRule("projection_entity_set", "PROJECTION", "CUSTOM_LU", "NORMALIZED_NAME", 30, "RULE_ENGINE"),
    DependencyRule("projection_name", "PROJECTION", "PROJECTION_CONFIGURATION", "NORMALIZED_NAME", 35, "RULE_ENGINE"),
    DependencyRule("enumeration_reference", "CUSTOM_ENUMERATION", "CUSTOM_ENUMERATION", "NORMALIZED_NAME", 40, "RULE_ENGINE"),
    DependencyRule("custom_field_attribute", "CUSTOM_FIELD", "CF_PERSISTENT", "NORMALIZED_NAME", 50, "RULE_ENGINE"),
    DependencyRule("workflow_process", "WORKFLOW", "WORKFLOW", "NORMALIZED_NAME", 60, "RULE_ENGINE"),
    DependencyRule("family_version_order", "PACKAGE", "PACKAGE", "FAMILY", 80, "RULE_ENGINE"),
)


def type_priority(item_types: set[str]) -> int:
    if not item_types:
        return 100
    return min(TYPE_RANK.get(item_type, 80) for item_type in item_types)
