from __future__ import annotations

import os
from pathlib import Path

from drskill.harnesses import HarnessDef
from drskill.models import BrokenSymlink, RawInstance


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


def _find_broken_symlinks(base: Path) -> list[Path]:
    out = []
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
) -> tuple[list[RawInstance], list[BrokenSymlink]]:
    instances: list[RawInstance] = []
    broken: list[BrokenSymlink] = []
    for order, (base, scope, spec_str) in enumerate(
        h.search_paths(project_root, home, global_only)
    ):
        if not base.is_dir():
            continue
        files = _find_skill_files(base, h.recursive)
        if spec_str in h.root_md_paths:
            files += sorted(
                p for p in base.glob("*.md")
                if p.name != "SKILL.md" and p.is_file()
            )
        for f in files:
            instances.append(
                RawInstance(
                    harness=h.id,
                    scope=scope,
                    skill_file=f,
                    via_symlink=_via_symlink(f, base),
                    order=order,
                )
            )
        broken += [BrokenSymlink(harness=h.id, path=p) for p in _find_broken_symlinks(base)]
    return instances, broken
