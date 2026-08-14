import json
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
    instances, broken, _u = discover(get("claude-code"), proj, home)
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
    instances, _, _u = discover(get("claude-code"), proj, home)
    assert len(instances) == 1
    assert instances[0].via_symlink is True


def test_reports_broken_symlinks(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(proj / "nowhere", d / "dead")
    instances, broken, _u = discover(get("claude-code"), proj, home)
    assert instances == []
    assert [b.path.name for b in broken] == ["dead"]


def test_symlink_loop_terminates(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    d = proj / ".claude" / "skills"
    d.mkdir(parents=True)
    os.symlink(d, d / "loop")
    instances, broken, _u = discover(get("claude-code"), proj, home)
    assert instances == []  # terminates, finds nothing


def test_pi_root_md_only_in_native_dirs(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    native = proj / ".pi" / "skills"
    native.mkdir(parents=True)
    (native / "note.md").write_text("a bare skill\n")
    universal = proj / ".agents" / "skills"
    universal.mkdir(parents=True)
    (universal / "ignored.md").write_text("not a skill here\n")
    instances, _, _u = discover(get("pi"), proj, home)
    assert [i.skill_file.name for i in instances] == ["note.md"]


def test_global_only(tree):
    proj, home = tree
    instances, _, _u = discover(get("claude-code"), proj, home, global_only=True)
    assert [i.skill_file.parent.name for i in instances] == ["beta"]


def test_dangling_skill_md_symlink_is_broken_not_instance(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    d = proj / ".claude" / "skills" / "ghost"
    d.mkdir(parents=True)
    os.symlink(proj / "nowhere", d / "SKILL.md")
    instances, broken, _u = discover(get("claude-code"), proj, home)
    assert instances == []
    assert [b.path.name for b in broken] == ["SKILL.md"]


def test_broken_symlink_sweep_respects_recursive_flag(tmp_path):
    from drskill.discovery import _find_broken_symlinks
    base = tmp_path / "skills"
    deep = base / "a" / "b"
    deep.mkdir(parents=True)
    os.symlink(tmp_path / "nowhere", base / "top-dead")
    os.symlink(tmp_path / "nowhere", base / "a" / "mid-dead")
    os.symlink(tmp_path / "nowhere", deep / "deep-dead")
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    os.symlink(tmp_path / "nowhere", canonical / "linked-dead")
    os.symlink(canonical, base / "linked")
    shallow = {p.name for p in _find_broken_symlinks(base, recursive=False)}
    assert shallow == {"top-dead", "mid-dead", "linked-dead"}
    full = {p.name for p in _find_broken_symlinks(base, recursive=True)}
    assert full == {"top-dead", "mid-dead", "deep-dead", "linked-dead"}


def _mk_skill(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    return d / "SKILL.md"


def _install_cc_plugin(home, proj_unused, name="sp"):
    active = home / ".claude" / "plugins" / "cache" / "mkt" / name / "1.0.0"
    _mk_skill(active / "skills", f"{name}-plugin-skill")
    state = home / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 1, "plugins": {f"{name}@mkt": [
        {"scope": "user", "installPath": str(active), "version": "1.0.0"},
    ]}}), encoding="utf-8")
    return active


def test_discover_appends_plugin_roots_after_native(tmp_path):
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    _mk_skill(proj / ".claude" / "skills", "native-skill")
    _mk_skill(home / ".claude" / "skills", "global-skill")
    _install_cc_plugin(home, proj)
    instances, _broken, unreadable = discover(h, proj, home)
    assert unreadable == []
    by_name = {i.skill_file.parent.name: i for i in instances}
    plug = by_name["sp-plugin-skill"]
    native_orders = [i.order for i in instances if i.plugin is None]
    assert plug.plugin is not None and plug.plugin.name == "sp"
    assert plug.scope == "user"
    assert plug.order > max(native_orders)  # plugin roots rank below native


def test_discover_skips_disabled_plugins(tmp_path):
    import json as _json
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _install_cc_plugin(home, proj)
    (home / ".claude" / "settings.json").write_text(
        _json.dumps({"enabledPlugins": {"sp@mkt": False}}), encoding="utf-8"
    )
    instances, _b, _u = discover(h, proj, home)
    assert all(i.plugin is None for i in instances)


def test_discover_global_only_keeps_user_scope_plugins(tmp_path):
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "claude-code")
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _install_cc_plugin(home, proj)
    instances, _b, _u = discover(h, proj, home, global_only=True)
    assert any(i.plugin is not None for i in instances)


def test_discover_gemini_plugin_roots_nonrecursive(tmp_path):
    from drskill.harnesses import load_harnesses
    from drskill.discovery import discover

    h = next(x for x in load_harnesses() if x.id == "gemini-cli")
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    ext = home / ".gemini" / "extensions" / "ext-a"
    _mk_skill(ext / "skills", "top-skill")
    _mk_skill(ext / "skills" / "nest1" / "nest2", "deep-skill")
    (ext / "gemini-extension.json").write_text(
        json.dumps({"name": "ext-a", "version": "1.0.0"}), encoding="utf-8"
    )
    instances, _b, _u = discover(h, proj, home)
    names = {i.skill_file.parent.name for i in instances}
    assert "top-skill" in names and "deep-skill" not in names
