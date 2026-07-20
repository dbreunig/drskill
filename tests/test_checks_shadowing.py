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


def test_double_load_copy_plus_symlink(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    # the gh-skill x npx-skills interaction: a copy in .claude/skills and a
    # symlink in .agents/skills, both read by pi
    canonical = write(proj, ".agents/skills", "pdf-tools")
    d = proj / ".pi" / "skills"
    d.mkdir(parents=True)
    os.symlink(canonical, d / "pdf-tools-link")
    findings = run_all(world_from(proj, home, ("pi",)), Config())
    dl = find(findings, "double-load")
    assert len(dl) == 1
    assert dl[0].severity == "error"
    assert dl[0].harnesses == ["pi"]


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
