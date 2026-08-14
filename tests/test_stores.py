import json
from pathlib import Path

from drskill.stores import InstalledPlugin, discover_plugins


def _skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def _cc_state(home: Path, plugins: dict) -> Path:
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "plugins": plugins}), encoding="utf-8")
    return p


def _cc_cache(home: Path, marketplace: str, plugin: str, version: str) -> Path:
    d = home / ".claude" / "plugins" / "cache" / marketplace / plugin / version
    _skill(d / "skills", f"{plugin}-skill")
    return d


def test_claude_code_active_user_install(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "6.0.0")
    _cc_cache(home, "mkt", "sp", "4.0.0")  # stale: must NOT be returned
    _cc_state(home, {"sp@mkt": [
        {"scope": "user", "installPath": str(active), "version": "6.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "6.0.0"
    assert p.scope == "user" and p.enabled and p.recursive
    assert p.skills_roots == [active / "skills"]
    assert p.provenance_source == "sp@mkt==6.0.0"


def test_claude_code_local_scope_only_matches_its_project(tmp_path):
    home = tmp_path / "home"
    mine, other = tmp_path / "mine", tmp_path / "other"
    mine.mkdir(); other.mkdir()
    active = _cc_cache(home, "mkt", "sp", "4.0.0")
    _cc_state(home, {"sp@mkt": [
        {"scope": "local", "projectPath": str(mine),
         "installPath": str(active), "version": "4.0.0"},
    ]})
    plugins, _ = discover_plugins("claude-code", home, mine)
    (p,) = plugins
    assert p.scope == "project" and p.project_path == mine
    assert discover_plugins("claude-code", home, other)[0] == []


def test_claude_code_enabled_plugins(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    a = _cc_cache(home, "mkt", "a", "1.0.0")
    b = _cc_cache(home, "mkt", "b", "1.0.0")
    _cc_state(home, {
        "a@mkt": [{"scope": "user", "installPath": str(a), "version": "1.0.0"}],
        "b@mkt": [{"scope": "user", "installPath": str(b), "version": "1.0.0"}],
    })
    settings = home / ".claude" / "settings.json"
    # explicit false disables; a MISSING key counts as enabled
    settings.write_text(json.dumps({"enabledPlugins": {"a@mkt": False}}))
    plugins, _ = discover_plugins("claude-code", home, proj)
    by_name = {p.name: p for p in plugins}
    assert by_name["a"].enabled is False
    assert by_name["b"].enabled is True


def test_claude_code_malformed_state_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_missing_state_and_unknown_harness_are_empty(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    home.mkdir(); proj.mkdir()
    assert discover_plugins("claude-code", home, proj) == ([], [])
    assert discover_plugins("pi", home, proj) == ([], [])


def test_claude_code_valid_json_wrong_shape_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_missing_plugins_key_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"version": 1}), encoding="utf-8")
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]
