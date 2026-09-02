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

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_repo(source: str | None) -> str | None:
    """owner/repo from an ecosystem source string, or None."""
    if not isinstance(source, str):
        return None
    s = source.strip()
    s = s.removeprefix("https://github.com/").removeprefix("github.com/").removeprefix("github:")
    s = s.removesuffix(".git")
    s = s.split("@", 1)[0]
    parts = s.split("/")
    if len(parts) >= 2 and _REPO.match("/".join(parts[:2])):
        return "/".join(parts[:2])
    return None


def _github_metadata(contributor: Contributor) -> dict:
    from drskill import content

    md: dict = {}
    repo = parse_repo(contributor.source.source)
    if repo:
        md["repo"] = repo
    if contributor.source.path is not None:
        md["skill_path"] = contributor.source.path
    if contributor.source.ref:
        md["ref"] = contributor.source.ref
    tree_sha = contributor.frontmatter.get("tree_sha")
    if isinstance(tree_sha, str) and tree_sha:
        md["tree_sha"] = tree_sha
    try:
        files = content.collect_files(contributor)
        md["directory_hash"] = content.manifest_hash(files)
        # The exact file set the hash covers. Install extracts only these
        # paths, so repo housekeeping files never count as the skill.
        md["files"] = sorted(f["path"] for f in files)
    except OSError:
        pass
    return md


def normalize_name(name: str) -> str:
    lowered = name.strip().lower()
    replaced = _INVALID.sub("-", lowered)
    collapsed = _COLLAPSE.sub("-", replaced).strip("-_.")
    return collapsed or "skill"


def is_local(contributor: Contributor) -> bool:
    """True when the contributor has no installable source and would
    publish as a local_only entry."""
    source_type = _SOURCE_TYPES.get(contributor.source.kind)
    return source_type is None or not contributor.source.source


def contributors_to_manifest(
    contributors: list[Contributor],
    hosted: dict[str, str] | None = None,
) -> tuple[dict, list[str]]:
    """hosted maps contributor id -> content hash for skills whose content
    was uploaded to the service; those become installable drskill entries."""
    hosted = hosted or {}
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

        if contributor.id in hosted:
            source_type = "drskill"
            source_reference = "drskill"
            content_hash = hosted[contributor.id]
            local_only = False
        else:
            mapped = _SOURCE_TYPES.get(contributor.source.kind)
            source = contributor.source.source
            local_only = is_local(contributor)
            source_type = "local" if local_only else mapped
            source_reference = source or contributor.id
            content_hash = contributor.content_hash
        metadata = _github_metadata(contributor) if source_type == "github" else {}
        entries.append(
            {
                "kind": kind,
                "selector": selector,
                "name": name,
                "source_type": source_type,
                "source_reference": source_reference,
                "content_hash": content_hash,
                "local_only": local_only,
                "metadata": metadata,
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
