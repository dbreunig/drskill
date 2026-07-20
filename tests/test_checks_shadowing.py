import os
from pathlib import Path

from drskill.checks import run_all
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world


def world_from(proj, home, harness_ids=("claude-code",)):
    hs = [h for h in load_harnesses() if h.id in harness_ids]
    instances, broken = [], []
    for h in hs:
        i, b = discover(h, proj, home)
        instances += i
        broken += b
    return build_world(instances, {h.id: h for h in hs}, broken)


def write(root, rel, name, body="body"):
    d = root / rel / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n")
    return d


def find(findings, check_id):
    return [f for f in findings if f.check_id == check_id]


def test_name_shadow(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".claude/skills", "tool", body="project version")
    write(home, ".claude/skills", "tool", body="user version")
    findings = run_all(world_from(proj, home), Config())
    shadows = find(findings, "name-shadow")
    assert len(shadows) == 1
    assert shadows[0].severity == "warning"
    assert "project" in shadows[0].message  # names the winner's scope


def test_double_load_two_real_copies(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # the gh-skill x npx-skills interaction: two independently materialized
    # copies with identical content, both read by pi from different search
    # directories -- two distinct real paths, not one file reached twice.
    content = "---\nname: pdf-tools\ndescription: d\n---\nbody\n"
    for rel in [".agents/skills/pdf-tools", ".pi/skills/pdf-tools"]:
        d = proj / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content)
    findings = run_all(world_from(proj, home, ("pi",)), Config())
    dl = find(findings, "double-load")
    assert len(dl) == 1
    assert dl[0].severity == "error"
    assert dl[0].harnesses == ["pi"]


def test_no_double_load_when_symlink_resolves_to_same_real_path(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # one search directory symlinked into another within the same harness's
    # own search paths (e.g. ~/.pi/agent/skills/x -> ~/.agents/skills/x):
    # both instances resolve to the same real file, so this is one
    # contributor with two deployments, not a double-load.
    canonical = write(proj, ".agents/skills", "pdf-tools")
    d = proj / ".pi" / "skills"
    d.mkdir(parents=True)
    os.symlink(canonical, d / "pdf-tools")
    findings = run_all(world_from(proj, home, ("pi",)), Config())
    assert find(findings, "double-load") == []


def test_no_double_load_when_shadowed(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, ".pi/skills", "tool", body="pi version")
    write(proj, ".agents/skills", "tool", body="different version")
    findings = run_all(world_from(proj, home, ("pi",)), Config())
    assert find(findings, "double-load") == []
    assert len(find(findings, "name-shadow")) == 1


def test_broken_symlink(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(proj / "gone", d / "dead")
    findings = run_all(world_from(proj, home), Config())
    bs = find(findings, "broken-symlink")
    assert len(bs) == 1 and bs[0].severity == "error"
    assert "dead" in bs[0].message
