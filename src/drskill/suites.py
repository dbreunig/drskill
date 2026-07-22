"""Recover which suite a skill came from. drskill matches installed skills
against the plugin caches on disk (by content hash) and against
skills-lock.json sources. It never guesses from a path or a name."""

from __future__ import annotations

import json
from pathlib import Path

from drskill.resolution import content_hash


def _plugin_hashes(home: Path) -> dict[str, str]:
    by_hash: dict[str, str] = {}
    cache = home / ".claude" / "plugins" / "cache"
    if not cache.is_dir():
        return by_hash
    # cache/<marketplace>/<plugin>/<version>/skills/<name>/SKILL.md
    for skill_md in cache.glob("*/*/*/skills/*/SKILL.md"):
        plugin = skill_md.parents[3].name
        try:
            h = content_hash(skill_md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        by_hash.setdefault(h, plugin)  # first plugin to claim a hash wins, stable
    return by_hash


def _lockfile_sources(home: Path) -> dict[str, str]:
    # Bounded search of the known machine-level lockfile spots. Never walk
    # the whole home tree; the project lockfile is folded in separately by
    # the pipeline.
    candidates = [
        home / "skills-lock.json",
        home / ".claude" / "skills-lock.json",
        home / ".agents" / "skills-lock.json",
    ]
    by_name: dict[str, str] = {}
    for lock in candidates:
        if not lock.is_file():
            continue
        try:
            data = json.loads(lock.read_text())
        except Exception:
            continue
        entries = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if isinstance(entry, dict) and isinstance(entry.get("source"), str):
                by_name.setdefault(str(name), entry["source"])
    return by_name


def build_registry(home: Path) -> tuple[dict[str, str], dict[str, str]]:
    return _plugin_hashes(home), _lockfile_sources(home)


def suite_for(
    content_hash: str, name: str, by_hash: dict[str, str], by_name: dict[str, str]
) -> str | None:
    if content_hash in by_hash:
        return by_hash[content_hash]
    return by_name.get(name)
