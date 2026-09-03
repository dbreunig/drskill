"""Bridge symlinks from harness skill stores to the shared .agents store.

The canonical copy of an installed skill lives in .agents/skills; harnesses
that do not read the shared store get a relative symlink in their own
`.<name>/skills` directory. Stores are discovered by convention (any
existing `.<name>/skills`) plus known blind harnesses from the harness
table, so unknown agents work without table maintenance.
"""

from __future__ import annotations

import os
from pathlib import Path

SHARED = ".agents"


def discover_bridge_dirs(scope_root: Path, scope: str = "project") -> list[tuple[str, Path]]:
    """(label, skills dir) bridge candidates under scope_root: existing
    `.<name>/skills` directories by convention, plus known harnesses that
    cannot read the shared store when one of their real detect markers
    exists (their skills directory is created at link time). scope picks
    project_paths or global_paths for known harnesses."""
    from drskill.harnesses import load_harnesses

    found: dict[Path, str] = {}
    for entry in sorted(scope_root.glob(".*/skills")):
        dot = entry.parent
        if dot.name == SHARED or not entry.is_dir() or entry.is_symlink():
            continue
        found[entry] = dot.name

    for harness in load_harnesses():
        reads_shared = any("agents/skills" in spec
                           for spec in harness.project_paths + harness.global_paths)
        if reads_shared:
            continue
        present = any((scope_root / marker.removeprefix("~/")).exists()
                      for marker in harness.detect)
        if not present:
            continue
        specs = harness.project_paths if scope == "project" else harness.global_paths
        if specs:
            found[scope_root / specs[0].removeprefix("~/")] = harness.display_name
    return [(label, path) for path, label in sorted(found.items())]


def retarget_cwd(cwd: Path) -> tuple[Path, Path] | None:
    """(project root, harness skills dir) when cwd sits inside a harness's
    own store — `.hermes`, `.hermes/skills`, or deeper — meaning "install
    here". None otherwise, and never for the shared store itself."""
    for candidate in (cwd, *cwd.parents):
        name = candidate.name
        if not name.startswith(".") or name in (SHARED, ".", ".."):
            continue
        return candidate.parent, candidate / "skills"
    return None


def create_link(store: Path, name: str, canonical: Path) -> str:
    """A relative symlink store/name -> canonical. "linked" when created,
    "refreshed" when an existing symlink was replaced, "exists" when a real
    file or directory occupies the spot (never overwritten)."""
    store.mkdir(parents=True, exist_ok=True)
    link = store / name
    if link.is_symlink():
        link.unlink()
        status = "refreshed"
    elif link.exists():
        return "exists"
    else:
        status = "linked"
    link.symlink_to(os.path.relpath(canonical, store))
    return status
