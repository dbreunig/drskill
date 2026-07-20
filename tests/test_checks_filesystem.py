import os

import pytest

from drskill.checks import run_all
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world


def running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


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


def test_unreadable_skill_warning_finding(tmp_path):
    if running_as_root():
        pytest.skip("root ignores file permissions")
    proj, home = tmp_path / "p", tmp_path / "h"
    d = write(proj, ".claude/skills", "locked")
    (d / "SKILL.md").chmod(0)
    try:
        findings = run_all(world_from(proj, home), Config())
        unreadable = [f for f in findings if f.check_id == "unreadable-skill"]
        assert len(unreadable) == 1
        assert unreadable[0].severity == "warning"
        assert "cannot read" in unreadable[0].message
        assert "locked" in unreadable[0].message
    finally:
        (d / "SKILL.md").chmod(0o644)
