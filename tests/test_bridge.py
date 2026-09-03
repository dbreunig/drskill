from pathlib import Path

from drskill import bridge


def make_project(tmp_path, dirs):
    root = tmp_path / "proj"
    for d in dirs:
        (root / d).mkdir(parents=True)
    return root


def test_discover_finds_dot_skill_stores_and_known_blind_harnesses(tmp_path):
    root = make_project(tmp_path, [".git", ".hermes/skills", ".nanoclaw/skills",
                                   ".agents/skills", ".claude"])
    home = tmp_path / "home"
    home.mkdir()
    found = bridge.discover_bridge_dirs(root)
    paths = {str(p.relative_to(root)) for _, p in found}
    assert paths == {".claude/skills", ".hermes/skills", ".nanoclaw/skills"}
    labels = {label for label, _ in found}
    assert "Claude Code" in labels          # known blind harness, by marker
    assert ".nanoclaw" in labels            # unknown harness, by convention


def test_discover_skips_the_shared_store_and_absent_stores(tmp_path):
    root = make_project(tmp_path, [".agents/skills"])
    assert bridge.discover_bridge_dirs(root) == []


def test_discover_claude_needs_only_its_marker(tmp_path):
    root = make_project(tmp_path, [".claude"])  # no skills subdir yet
    found = bridge.discover_bridge_dirs(root)
    assert [(label, p.name) for label, p in found] == [("Claude Code", "skills")]


def test_retarget_inside_a_harness_skills_dir(tmp_path):
    root = make_project(tmp_path, [".hermes/skills"])
    got = bridge.retarget_cwd(root / ".hermes" / "skills")
    assert got == (root, root / ".hermes" / "skills")

    got = bridge.retarget_cwd(root / ".hermes")
    assert got == (root, root / ".hermes" / "skills")

    nested = root / ".hermes" / "skills" / "existing-skill"
    nested.mkdir()
    assert bridge.retarget_cwd(nested) == (root, root / ".hermes" / "skills")


def test_retarget_ignores_normal_dirs_and_the_shared_store(tmp_path):
    root = make_project(tmp_path, [".agents/skills", "src"])
    assert bridge.retarget_cwd(root / "src") is None
    assert bridge.retarget_cwd(root) is None
    assert bridge.retarget_cwd(root / ".agents" / "skills") is None


def test_create_link_is_relative_and_safe(tmp_path):
    root = make_project(tmp_path, [".agents/skills/my-skill", ".hermes/skills"])
    canonical = root / ".agents" / "skills" / "my-skill"
    (canonical / "SKILL.md").write_text("x")
    store = root / ".hermes" / "skills"

    assert bridge.create_link(store, "my-skill", canonical) == "linked"
    link = store / "my-skill"
    assert link.is_symlink()
    assert not Path(link.readlink()).is_absolute()
    assert (link / "SKILL.md").read_text() == "x"

    assert bridge.create_link(store, "my-skill", canonical) == "refreshed"

    real = store / "occupied"
    real.mkdir()
    assert bridge.create_link(store, "occupied", canonical) == "exists"
    assert not real.is_symlink()


def test_create_link_makes_the_store_dir(tmp_path):
    root = make_project(tmp_path, [".agents/skills/my-skill", ".claude"])
    canonical = root / ".agents" / "skills" / "my-skill"
    canonical.mkdir(exist_ok=True)
    assert bridge.create_link(root / ".claude" / "skills", "my-skill", canonical) == "linked"
