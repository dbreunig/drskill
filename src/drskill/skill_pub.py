"""Pure helpers for registry skill publishing and refs."""

from __future__ import annotations

import re
from pathlib import Path

from drskill import resolution
from drskill.manifest_build import normalize_name

_REF = re.compile(r"^([A-Za-z0-9-]+)/([A-Za-z0-9-]+)(?:@([1-9]\d*))?$")


def collect_dir(path: Path) -> list[dict]:
    """Every regular file under path (skipping .git), in the content.py
    file shape. The directory must hold a SKILL.md at its root. Symlinks
    are rejected loudly rather than silently dropped, so an upload never
    quietly diverges from what is on disk."""
    root = Path(path)
    files = []
    symlinks = []
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts:
            continue
        if p.is_symlink():
            symlinks.append(p.relative_to(root).as_posix())
            continue
        if not p.is_file():
            continue
        files.append({
            "path": p.relative_to(root).as_posix(),
            "data": p.read_bytes(),
            "executable": bool(p.stat().st_mode & 0o111),
        })
    if symlinks:
        raise ValueError(
            f"{root} contains symlinks ({', '.join(symlinks[:5])}); resolve them before publishing")
    if not any(f["path"] == "SKILL.md" for f in files):
        raise ValueError(f"{root} has no SKILL.md")
    return files


def skill_slug(name: str) -> str:
    """The server slug for a skill name: normalized, then restricted to
    the server's [a-z0-9-] rule."""
    slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", normalize_name(name))).strip("-")
    return slug or "skill"


def frontmatter_meta(files: list[dict], fallback: str) -> tuple[str, str | None]:
    """(normalized skill name, description) from the SKILL.md frontmatter."""
    skill_md = next(f for f in files if f["path"] == "SKILL.md")
    fm, _, _ = resolution.split_frontmatter(skill_md["data"].decode(errors="replace"))
    name = fm.get("name") if isinstance(fm, dict) else None
    description = fm.get("description") if isinstance(fm, dict) else None
    return (
        normalize_name(name if isinstance(name, str) and name.strip() else fallback),
        description if isinstance(description, str) and description.strip() else None,
    )


def parse_skill_ref(ref: str) -> tuple[str, str, int | None]:
    """("owner", "slug", version or None) from owner/slug[@N]."""
    match = _REF.match(ref.strip())
    if not match:
        raise ValueError(f"expected owner/slug or owner/slug@N, got {ref!r}")
    owner, slug, number = match.groups()
    return owner, slug, int(number) if number else None


def blocking_findings(findings) -> list:
    """The findings that block a registry publish: errors and warnings."""
    return [f for f in findings if f.severity in ("error", "warning")]
