"""Classify a revision's entries against locally scanned skills.

Matching is by normalized name until the resolution phase brings a real
lockfile binding. A hash tiebreak settles duplicate names; an unresolved
duplicate carries an ambiguity note.
"""

from __future__ import annotations

from dataclasses import dataclass

from drskill import content
from drskill.manifest_build import normalize_name
from drskill.models import Contributor


@dataclass
class EntryStatus:
    entry: dict
    contributor: Contributor | None
    state: str  # matches | changed | missing | unreadable | unchecked
    note: str | None = None


def classify_entries(entries: list[dict], contributors: list[Contributor]) -> list[EntryStatus]:
    skills = [c for c in contributors if c.kind == "skill"]
    by_name: dict[str, list[Contributor]] = {}
    for c in skills:
        by_name.setdefault(normalize_name(c.name), []).append(c)

    out: list[EntryStatus] = []
    for entry in entries:
        if entry.get("kind") != "skill":
            out.append(EntryStatus(entry, None, "unchecked"))
            continue
        candidates = by_name.get(entry.get("name"), [])
        if not candidates:
            out.append(EntryStatus(entry, None, "missing"))
            continue
        states = [_compare(entry, c) for c in candidates]
        matched = next((i for i, state in enumerate(states) if state == "matches"), None)
        if matched is not None:
            out.append(EntryStatus(entry, candidates[matched], "matches"))
            continue
        note = "several local skills share this name" if len(candidates) > 1 else None
        out.append(EntryStatus(entry, candidates[0], states[0], note))
    return out


def _compare(entry: dict, contributor: Contributor) -> str:
    source_type = entry.get("source_type")
    metadata = entry.get("metadata") or {}
    try:
        if source_type == "local" or (source_type == "github" and not metadata.get("directory_hash")):
            local = contributor.content_hash
            expected = entry.get("content_hash")
        else:
            local = content.manifest_hash(content.collect_files(contributor))
            expected = metadata.get("directory_hash") if source_type == "github" \
                else entry.get("content_hash")
    except OSError:
        return "unreadable"
    return "matches" if local == expected else "changed"
