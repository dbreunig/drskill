"""Recover which suite a skill came from.

drskill matches installed skills against the plugin caches on disk by
content hash. For a skill that a lockfile already governs, it reuses the
lockfile source drskill has already recorded as that skill's provenance,
the same value the `source` column shows. It never guesses a suite from a
path or a bare name."""

from __future__ import annotations

from pathlib import Path

from drskill.resolution import World, content_hash


def build_registry(home: Path) -> dict[str, str]:
    """Map a normalized content hash to a plugin name, read from every
    cached plugin skill. Iteration is sorted so a hash shared by two
    plugins resolves to the same plugin on every machine."""
    by_hash: dict[str, str] = {}
    cache = home / ".claude" / "plugins" / "cache"
    if not cache.is_dir():
        return by_hash
    # cache/<marketplace>/<plugin>/<version>/skills/**/SKILL.md
    for skills_dir in sorted(cache.glob("*/*/*/skills")):
        if not skills_dir.is_dir():
            continue
        plugin = skills_dir.parent.parent.name
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            try:
                h = content_hash(skill_md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            by_hash.setdefault(h, plugin)
    return by_hash


def assign_suites(world: World, home: Path) -> None:
    """Set `suite` on each contributor in place. A skill's suite is its
    plugin (by content hash) or its lockfile source. An MCP tool's suite is
    the server that exposes it, so tools group by server the way skills
    group by plugin. Anything unmatched stays None."""
    by_hash = build_registry(home)
    server_by_cfg = {s.config_hash: s.name for s in world.mcp_servers}
    for c in world.contributors.values():
        if c.kind == "mcp_tool":
            c.suite = server_by_cfg.get(c.id.split(":", 1)[0])
            continue
        if c.kind != "skill":
            continue
        found = by_hash.get(c.content_hash)
        if found is None and c.source.kind == "skills-lock" and c.source.source:
            found = c.source.source
        c.suite = found
