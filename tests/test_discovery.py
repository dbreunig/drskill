import os
from pathlib import Path

import pytest

from drskill.discovery import discover
from drskill.harnesses import load_harnesses


def get(hid):
    return next(h for h in load_harnesses() if h.id == hid)


def write_skill(root: Path, name: str, description: str = "d") -> Path:
    d = root / name
    d.mkdir(parents=True)
    f = d / "SKILL.md"
    f.write_text(f"---\nname: {name}\ndescription: {description}\n---\nbody\n")
    return d


@pytest.fixture
def tree(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude").mkdir(parents=True)
    write_skill(proj / ".claude" / "skills", "alpha")
    write_skill(home / ".claude" / "skills", "beta")
    return proj, home


def test_discovers_project_and_global(tree):
    proj, home = tree
    instances, broken = discover(get("claude-code"), proj, home)
    names = sorted(i.skill_file.parent.name for i in instances)
    assert names == ["alpha", "beta"]
    assert broken == []
    by_name = {i.skill_file.parent.name: i for i in instances}
    assert by_name["alpha"].scope == "project" and by_name["alpha"].order == 0
    assert by_name["beta"].scope == "user" and by_name["beta"].order == 1


def test_follows_directory_symlinks(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    canonical = write_skill(proj / ".agents" / "skills", "linked")
    target_dir = proj / ".claude" / "skills"
    target_dir.mkdir(parents=True)
    os.symlink(canonical, target_dir / "linked")
    instances, _ = discover(get("claude-code"), proj, home)
    assert len(instances) == 1
    assert instances[0].via_symlink is True


def test_reports_broken_symlinks(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(proj / "nowhere", d / "dead")
    instances, broken = discover(get("claude-code"), proj, home)
    assert instances == []
    assert [b.path.name for b in broken] == ["dead"]


def test_symlink_loop_terminates(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(d, d / "loop")
    instances, broken = discover(get("claude-code"), proj, home)
    assert instances == []  # terminates, finds nothing


def test_pi_root_md_only_in_native_dirs(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    native = proj / ".pi" / "skills"
    native.mkdir(parents=True)
    (native / "note.md").write_text("a bare skill\n")
    universal = proj / ".agents" / "skills"
    universal.mkdir(parents=True)
    (universal / "ignored.md").write_text("not a skill here\n")
    instances, _ = discover(get("pi"), proj, home)
    assert [i.skill_file.name for i in instances] == ["note.md"]


def test_global_only(tree):
    proj, home = tree
    instances, _ = discover(get("claude-code"), proj, home, global_only=True)
    assert [i.skill_file.parent.name for i in instances] == ["beta"]
