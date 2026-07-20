import os
from pathlib import Path

import pytest

from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.resolution import (
    build_world,
    content_hash,
    normalize_content,
    split_frontmatter,
)


def running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def get(hid):
    return next(h for h in load_harnesses() if h.id == hid)


def write_skill(root: Path, name: str, description: str = "d", body: str = "body",
                extra_fm: str = "") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text(f"---\nname: {name}\ndescription: {description}\n{extra_fm}---\n{body}\n")
    return d


def world_for(harness_id, proj, home):
    h = get(harness_id)
    instances, broken = discover(h, proj, home)
    return build_world(instances, {h.id: h}, broken)


def test_split_frontmatter_variants():
    fm, raw, body = split_frontmatter("---\nname: x\n---\nhello\n")
    assert fm == {"name": "x"} and body == "hello\n" and "name: x" in raw
    fm, raw, body = split_frontmatter("no frontmatter here")
    assert fm == {} and raw == "" and body == "no frontmatter here"
    fm, raw, body = split_frontmatter("---\n: bad: [yaml\n---\nb")
    assert fm is None


def test_normalize_strips_gh_provenance_and_line_endings():
    a = "---\nname: x\ndescription: d\n---\nbody\n"
    b = "---\r\nname: x\r\ndescription: d\r\nsource: octo/repo\r\nref: main\r\ntree_sha: abc\r\n---\r\nbody\r\n"
    assert normalize_content(a) == normalize_content(b)
    assert content_hash(a) == content_hash(b)
    assert content_hash(a).startswith("sha256:")


def test_symlink_dedup_one_contributor_many_deployments(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    canonical = write_skill(proj / ".agents" / "skills", "shared")
    for d in [proj / ".claude" / "skills"]:
        d.mkdir(parents=True)
        os.symlink(canonical, d / "shared")
    (proj / ".pi").mkdir()
    cc, pi = get("claude-code"), get("pi")
    i1, b1 = discover(cc, proj, home)
    i2, b2 = discover(pi, proj, home)
    world = build_world(i1 + i2, {"claude-code": cc, "pi": pi}, b1 + b2)
    assert len(world.contributors) == 1
    c = next(iter(world.contributors.values()))
    assert {d.harness for d in c.deployments} == {"claude-code", "pi"}
    assert c.name == "shared" and c.scope == "project"


def test_shadowing_same_name_different_content(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".claude" / "skills", "tool", body="project version")
    write_skill(home / ".claude" / "skills", "tool", body="user version")
    world = world_for("claude-code", proj, home)
    loads = world.harness_loads("claude-code")
    assert len(loads) == 2
    winner = [c for c, d in loads if d.shadowed_by is None]
    loser = [(c, d) for c, d in loads if d.shadowed_by is not None]
    assert len(winner) == 1 and winner[0].scope == "project"
    assert len(loser) == 1 and loser[0][1].shadowed_by == winner[0].id
    assert world.effective("claude-code") == winner


def test_same_name_same_content_not_shadowed(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".pi" / "skills", "tool")
    write_skill(proj / ".agents" / "skills", "tool")
    world = world_for("pi", proj, home)
    assert all(d.shadowed_by is None for c, d in world.harness_loads("pi"))


def test_invalid_frontmatter_still_becomes_contributor(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    d = proj / ".claude" / "skills" / "bad"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\n: bad: [yaml\n---\nbody\n")
    world = world_for("claude-code", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.frontmatter_valid is False
    assert c.name == "bad"  # falls back to folder name


def test_gh_provenance_detected(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".claude" / "skills", "managed",
                extra_fm="source: octo/repo\nref: main\ntree_sha: abc\n")
    world = world_for("claude-code", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "gh-skill"
    assert c.source.source == "octo/repo"


def test_unreadable_skill_recorded_and_not_a_contributor(tmp_path):
    if running_as_root():
        pytest.skip("root ignores file permissions")
    proj, home = tmp_path / "proj", tmp_path / "home"
    d = write_skill(proj / ".claude" / "skills", "locked")
    (d / "SKILL.md").chmod(0)
    try:
        world = world_for("claude-code", proj, home)
        assert world.contributors == {}
        assert len(world.unreadable) == 1
        harness, path = world.unreadable[0]
        assert harness == "claude-code"
        assert path.endswith("locked/SKILL.md")
    finally:
        (d / "SKILL.md").chmod(0o644)


def test_token_costs_populated(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".claude" / "skills", "tok", description="does things",
                body="a longer body " * 50)
    world = world_for("claude-code", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.token_cost.catalog_tokens > 0
    assert c.token_cost.body_tokens > c.token_cost.catalog_tokens


def test_linked_provenance_for_store_symlink(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    canonical = write_skill(proj / ".agents" / "skills", "store-skill")
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(canonical, d / "store-skill")
    world = world_for("claude-code", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "linked"


def test_linked_provenance_for_direct_store_residence(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".agents" / "skills", "store-skill")
    (proj / ".pi").mkdir()
    world = world_for("pi", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "linked"


def test_plain_directory_stays_unmanaged(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".claude" / "skills", "hand-dropped")
    world = world_for("claude-code", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "unmanaged"


def test_gh_provenance_beats_linked(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / ".agents" / "skills", "managed",
                extra_fm="source: octo/repo\nref: main\ntree_sha: abc\n")
    (proj / ".pi").mkdir()
    world = world_for("pi", proj, home)
    c = next(iter(world.contributors.values()))
    assert c.source.kind == "gh-skill"


def test_search_order_none_never_shadows(tmp_path):
    from drskill.harnesses import HarnessDef

    proj, home = tmp_path / "proj", tmp_path / "home"
    write_skill(proj / "dir-a", "tool", body="version a")
    write_skill(proj / "dir-b", "tool", body="version b")
    h = HarnessDef(
        id="nonesuch", display_name="Nonesuch", search_order="none",
        project_paths=["dir-a", "dir-b"], recursive=True,
    )
    from drskill.discovery import discover

    instances, broken = discover(h, proj, home)
    world = build_world(instances, {h.id: h}, broken)
    assert all(d.shadowed_by is None for c, d in world.harness_loads("nonesuch"))
