from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from drskill import tokens
from drskill.harnesses import HarnessDef
from drskill.mcp import MCPServer
from drskill.models import (
    BrokenSymlink,
    BundledFile,
    Contributor,
    Deployment,
    Provenance,
    RawInstance,
    TokenCost,
)

# Frontmatter keys `gh skill` writes for provenance (repo, ref, tree SHA).
# Verify against a real `gh skill` install during Task 10 and adjust if the
# observed key names differ.
GH_PROVENANCE_KEYS: frozenset[str] = frozenset({"source", "ref", "tree_sha"})


def split_frontmatter(text: str) -> tuple[dict | None, str, str]:
    if not text.startswith("---\n"):
        return {}, "", text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, "", text
    raw = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, raw, body
    if not isinstance(parsed, dict):
        return None, raw, body
    return parsed, raw, body


def normalize_content(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    fm, _raw, body = split_frontmatter(text)
    if not fm:  # None (invalid) or {} (absent): hash the raw text
        return text
    kept = {k: v for k, v in fm.items() if k not in GH_PROVENANCE_KEYS}
    canonical_fm = yaml.safe_dump(kept, sort_keys=True)
    return canonical_fm + "\n---\n" + body


def _in_agents_store(path: Path) -> bool:
    """Layout heuristic: the realpath lives under a `.agents/skills` canonical
    store, which is how installers like `npx skills` materialize skills. It is
    evidence of installer management, not a claim about which installer."""
    return any(
        p.name == "skills" and p.parent.name == ".agents" for p in [path, *path.parents]
    )


SCAN_CAP_BYTES = 1_048_576  # bundled files above 1 MiB are recorded, not scanned
_SNIFF_BYTES = 8192


def collect_bundled_files(skill_file: Path) -> tuple[list[BundledFile], list[str]]:
    """Walk the skill directory and record every file except SKILL.md itself.

    Returns (files sorted by relpath, unreadable paths). Attackers do not
    follow directory conventions, so the whole tree is covered."""
    from drskill.discovery import _walk_dirs

    base = skill_file.parent
    out: list[BundledFile] = []
    unreadable: list[str] = []
    for dirpath, _dirnames, filenames in _walk_dirs(base):
        for fname in filenames:
            if dirpath == base and fname == skill_file.name:
                continue
            p = dirpath / fname
            if not p.is_file():  # dangling symlink; broken-symlink covers it
                continue
            digest = hashlib.sha256()
            head = b""
            size = 0  # counted from the bytes hashed, so the two cannot drift
            try:
                with open(p, "rb") as fh:
                    while chunk := fh.read(65536):
                        if not head:
                            head = chunk[:_SNIFF_BYTES]
                        digest.update(chunk)
                        size += len(chunk)
            except OSError:
                unreadable.append(str(p))
                continue
            out.append(
                BundledFile(
                    relpath=p.relative_to(base).as_posix(),
                    size=size,
                    content_hash="sha256:" + digest.hexdigest(),
                    is_text=b"\x00" not in head,
                    oversize=size > SCAN_CAP_BYTES,
                )
            )
    return sorted(out, key=lambda f: f.relpath), sorted(unreadable)


def content_hash(text: str) -> str:
    digest = hashlib.sha256(normalize_content(text).encode()).hexdigest()
    return "sha256:" + digest


class World(BaseModel):
    contributors: dict[str, Contributor] = Field(default_factory=dict)
    harnesses: dict[str, HarnessDef] = Field(default_factory=dict)
    broken_symlinks: list[BrokenSymlink] = Field(default_factory=list)
    unreadable: list[tuple[str, str]] = Field(default_factory=list)  # (harness, path)
    lockfile: dict[str, dict] | None = None
    mcp_servers: list[MCPServer] = Field(default_factory=list)
    # (harness, path, message, in_project)
    mcp_config_errors: list[tuple[str, str, str, bool]] = Field(default_factory=list)

    def harness_loads(self, harness_id: str) -> list[tuple[Contributor, Deployment]]:
        out = [
            (c, d)
            for c in self.contributors.values()
            for d in c.deployments
            if d.harness == harness_id
        ]
        return sorted(out, key=lambda cd: (cd[1].order, str(cd[1].path)))

    def effective(self, harness_id: str) -> list[Contributor]:
        seen: list[Contributor] = []
        for c, d in self.harness_loads(harness_id):
            if d.shadowed_by is None and c not in seen:
                seen.append(c)
        return seen


def _skill_name(fm: dict | None, skill_file: Path) -> str:
    if fm and isinstance(fm.get("name"), str) and fm["name"].strip():
        return fm["name"].strip()
    if skill_file.name == "SKILL.md":
        return skill_file.parent.name
    return skill_file.stem


def build_world(
    instances: list[RawInstance],
    harnesses: dict[str, HarnessDef],
    broken: list[BrokenSymlink],
) -> World:
    world = World(harnesses=harnesses, broken_symlinks=broken)
    for inst in instances:
        real = inst.skill_file.resolve()
        cid = str(real)
        c = world.contributors.get(cid)
        if c is None:
            try:
                text = real.read_text(encoding="utf-8", errors="replace")
            except OSError:
                world.unreadable.append((inst.harness, cid))
                continue
            fm, raw_fm, body = split_frontmatter(text)
            name = _skill_name(fm, real)
            description = ""
            if fm and isinstance(fm.get("description"), str):
                description = fm["description"]
            provenance = Provenance()
            if fm and GH_PROVENANCE_KEYS & fm.keys():
                provenance = Provenance(kind="gh-skill", source=fm.get("source"))
            elif _in_agents_store(real):
                provenance = Provenance(kind="linked")
            bundled: list[BundledFile] = []
            if real.name == "SKILL.md":
                bundled, unreadable_files = collect_bundled_files(real)
                world.unreadable += [(inst.harness, p) for p in unreadable_files]
            c = Contributor(
                id=cid,
                name=name,
                scope=inst.scope,
                source=provenance,
                bundled_files=bundled,
                routing_text=description,
                body=body,
                token_cost=TokenCost(
                    catalog_tokens=tokens.count(f"{name}: {description}"),
                    body_tokens=tokens.count(body),
                ),
                content_hash=content_hash(text),
                frontmatter_valid=fm is not None,
                frontmatter=fm or {},
                frontmatter_text=raw_fm,
            )
            world.contributors[cid] = c
        if inst.scope == "project":
            c.scope = "project"
        c.deployments.append(
            Deployment(
                harness=inst.harness,
                path=inst.skill_file,
                scope=inst.scope,
                via_symlink=inst.via_symlink,
                order=inst.order,
            )
        )
    for c in world.contributors.values():
        paths = [Path(c.id), *(d.path for d in c.deployments)]
        c.system = any(".system" in p.parts for p in paths)
    _mark_shadows(world)
    return world


def _mark_shadows(world: World) -> None:
    for hid in world.harnesses:
        if world.harnesses[hid].search_order == "none":
            continue  # this harness keeps every same-name copy visible
        first_by_name: dict[str, Contributor] = {}
        for c, d in world.harness_loads(hid):
            prior = first_by_name.get(c.name)
            if prior is None:
                first_by_name[c.name] = c
            elif prior.id != c.id and prior.content_hash != c.content_hash:
                d.shadowed_by = prior.id
