from __future__ import annotations

import os
from pathlib import Path

from drskill.harnesses import HarnessDef
from drskill.models import BrokenSymlink, RawInstance
from drskill.stores import discover_plugins


def _walk_dirs(base: Path):
    """os.walk following symlinks, guarded against loops."""
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(base, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        yield Path(dirpath), dirnames, filenames


def _find_skill_files(base: Path, recursive: bool) -> list[Path]:
    if not recursive:
        return sorted(base.glob("*/SKILL.md"))
    out = []
    for dirpath, _dirnames, filenames in _walk_dirs(base):
        if "SKILL.md" in filenames:
            out.append(dirpath / "SKILL.md")
    return sorted(out)


def _find_broken_symlinks(base: Path, recursive: bool = True) -> list[Path]:
    out = []
    if not recursive:
        # Check only entries directly in base and in base/* directories (depth matching */SKILL.md glob)
        for name in os.listdir(base):
            p = base / name
            if p.is_symlink() and not p.exists():
                out.append(p)
        # Also check one level deep (base/*/...)
        for subdir in base.iterdir():
            if subdir.is_dir():
                for name in os.listdir(subdir):
                    p = subdir / name
                    if p.is_symlink() and not p.exists():
                        out.append(p)
    else:
        for dirpath, dirnames, filenames in _walk_dirs(base):
            for name in list(dirnames) + list(filenames):
                p = dirpath / name
                if p.is_symlink() and not p.exists():
                    out.append(p)
    return sorted(out)


def _via_symlink(f: Path, base: Path) -> bool:
    cur = f
    while True:
        if cur.is_symlink():
            return True
        if cur == base or cur.parent == cur:
            return False
        cur = cur.parent


def discover(
    h: HarnessDef, project_root: Path, home: Path, global_only: bool = False
) -> tuple[list[RawInstance], list[BrokenSymlink], list[tuple[str, str]]]:
    instances: list[RawInstance] = []
    broken: list[BrokenSymlink] = []
    native_paths = h.search_paths(project_root, home, global_only)
    for order, (base, scope, spec_str) in enumerate(native_paths):
        if not base.is_dir():
            continue
        files = _find_skill_files(base, h.recursive)
        if spec_str in h.root_md_paths:
            files += sorted(
                p for p in base.glob("*.md")
                if p.name != "SKILL.md" and p.is_file()
            )
        for f in files:
            # Skip dangling symlinks (they are already reported as broken)
            if not f.exists():
                continue
            instances.append(
                RawInstance(
                    harness=h.id,
                    scope=scope,
                    skill_file=f,
                    via_symlink=_via_symlink(f, base),
                    order=order,
                )
            )
        broken += [BrokenSymlink(harness=h.id, path=p) for p in _find_broken_symlinks(base, h.recursive)]
    # Store-delivered skills: enabled plugins' roots rank BELOW every
    # native path (proven on gemini and copilot; codex keeps its
    # no-shadowing semantics via search_order "none").
    plugins, unreadable_states = discover_plugins(h.id, home, project_root)
    unreadable = [(h.id, p) for p in unreadable_states]
    order = len(native_paths)
    for plug in plugins:
        if not plug.enabled:
            continue  # disabled plugins' skills demonstrably do not load
        if global_only and plug.scope == "project":
            continue
        for base in plug.skills_roots:
            if not base.is_dir():
                order += 1
                continue
            for f in _find_skill_files(base, plug.recursive):
                if not f.exists():
                    continue
                instances.append(RawInstance(
                    harness=h.id,
                    scope=plug.scope,
                    skill_file=f,
                    via_symlink=_via_symlink(f, base),
                    order=order,
                    plugin=plug,
                ))
            broken += [
                BrokenSymlink(harness=h.id, path=p)
                for p in _find_broken_symlinks(base, plug.recursive)
            ]
            order += 1
    return instances, broken, unreadable
