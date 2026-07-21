"""skills-lock.json parsing (read-only, always) and drift attribution."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World

# Verified 2026-07-19 against `npx skills add vercel-labs/agent-skills` output and
# vercel-labs/skills @ src/local-lock.ts (LocalSkillLockEntry.computedHash /
# computeSkillFolderHash): the local project lockfile field is `computedHash`, a
# plain sha256 hex digest with no prefix. `hash`/`integrity`/`sha256` are kept as
# fallbacks for the (untested) global/user-scope lock schema, which the same source
# comment says uses a GitHub tree SHA instead.
_HASH_FIELDS = ("computedHash", "hash", "integrity", "sha256")


def load_lockfile(project_root: Path) -> dict[str, dict] | None:
    p = project_root / "skills-lock.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    skills = data.get("skills", data)
    if not isinstance(skills, dict):
        return None
    return {k: v for k, v in skills.items() if isinstance(v, dict)}


def compute_tree_hash(skill_dir: Path) -> str:
    # Mirrors vercel-labs/skills `computeSkillFolderHash` (src/local-lock.ts): sha256
    # over relative-path bytes immediately followed by file-content bytes, sorted by
    # relative path, with `.git` and `node_modules` subdirectories excluded. No
    # separator byte between path and content — matched here for hash compatibility.
    h = hashlib.sha256()
    for f in sorted(
        p
        for p in skill_dir.rglob("*")
        if p.is_file() and not {".git", "node_modules"} & set(p.relative_to(skill_dir).parts[:-1])
    ):
        h.update(f.relative_to(skill_dir).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _entry_hash(entry: dict) -> str | None:
    for field in _HASH_FIELDS:
        v = entry.get(field)
        if isinstance(v, str):
            return v.removeprefix("sha256:").removeprefix("sha256-")
    return None


@check("lockfile-drift")
def lockfile_drift(world: World, config: Config) -> list[Finding]:
    if not world.lockfile:
        return []
    by_name = {
        c.name: c for c in world.contributors.values() if c.kind == "skill"
    }
    out = []
    matches: list[str] = []
    mismatches: list[tuple[str, object]] = []
    for name, entry in sorted(world.lockfile.items()):
        expected = _entry_hash(entry)
        c = by_name.get(name)
        if c is None:
            out.append(
                make_finding(
                    "lockfile-drift", "warning", [],
                    f"'{name}' is in skills-lock.json but not found on disk",
                    harnesses=sorted(world.harnesses),
                    extra_key=f"missing:{name}",
                    fix_commands=[
                        f"npx skills add {shlex.quote(str(entry.get('source', name)))}",
                        "npx skills sync",
                    ],
                )
            )
            continue
        if expected is None:
            continue
        skill_dir = Path(c.id).parent
        if compute_tree_hash(skill_dir) == expected:
            matches.append(name)
        else:
            mismatches.append((name, c))

    # Self-calibration: if every hashed entry mismatches (and at least one
    # matched, verifying the algorithm), attribute each mismatch by name. If
    # NONE match, we can't tell drift from an algorithm mismatch against this
    # lockfile's producer — collapse to a single "unverifiable" warning
    # instead of crying wolf on every skill.
    if mismatches and not matches:
        out.append(
            make_finding(
                "lockfile-drift", "warning", [],
                "skills-lock.json hashes use an algorithm drskill cannot "
                "reproduce; hash-drift detection is disabled for this "
                "lockfile (missing-skill detection still active)",
                harnesses=sorted(world.harnesses),
                extra_key="unverifiable",
                fix_commands=[
                    "npx skills sync  # reinstall to a known-good state if you suspect drift"
                ],
            )
        )
    else:
        for name, c in mismatches:
            out.append(
                make_finding(
                    "lockfile-drift", "warning", [c],
                    f"'{name}' was modified outside `npx skills` — likely a "
                    "`gh skill update` or a hand edit; the lockfile no longer matches",
                    fix_commands=[
                        "npx skills sync  # restore the locked version",
                        f"npx skills update {shlex.quote(name)}  # or re-pin the new content",
                    ],
                )
            )
    return out
