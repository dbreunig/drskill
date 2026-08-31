"""Turn scanner Contributors into a publishable loadout manifest.

Pure functions, no UI and no network, so the mapping is testable on its own.
The server's selector rule is [a-z0-9][a-z0-9._-]* after the kind prefix.
"""

from __future__ import annotations

import re

from drskill.models import Contributor

_INVALID = re.compile(r"[^a-z0-9._-]+")
_COLLAPSE = re.compile(r"-{2,}")

_SOURCE_TYPES = {
    "gh-skill": "github",
    "skills-lock": "github",
    "plugin": "plugin",
}

_KINDS = {"skill": "skill", "mcp_tool": "mcp"}


def normalize_name(name: str) -> str:
    lowered = name.strip().lower()
    replaced = _INVALID.sub("-", lowered)
    collapsed = _COLLAPSE.sub("-", replaced).strip("-_.")
    return collapsed or "skill"


def contributors_to_manifest(contributors: list[Contributor]) -> tuple[dict, list[str]]:
    entries: list[dict] = []
    notes: list[str] = []
    used_selectors: set[str] = set()

    for contributor in contributors:
        kind = _KINDS[contributor.kind]
        name = normalize_name(contributor.name)
        if name != contributor.name:
            notes.append(f"renamed {contributor.name!r} to {name!r} to fit the selector rules")

        selector = f"{kind}:{name}"
        if selector in used_selectors:
            suffix = 2
            while f"{selector}-{suffix}" in used_selectors:
                suffix += 1
            selector = f"{selector}-{suffix}"
            name = f"{name}-{suffix}"
            notes.append(f"renamed a duplicate of {contributor.name!r} to {name!r}")
        used_selectors.add(selector)

        source_type = _SOURCE_TYPES.get(contributor.source.kind)
        source = contributor.source.source
        local_only = source_type is None or not source
        entries.append(
            {
                "kind": kind,
                "selector": selector,
                "name": name,
                "source_type": "local" if local_only else source_type,
                "source_reference": source or contributor.id,
                "content_hash": contributor.content_hash,
                "local_only": local_only,
                "metadata": {},
            }
        )

    return (
        {
            "schema_version": 1,
            "reproducible": False,
            "entries": entries,
            "harness_mappings": [],
        },
        notes,
    )
