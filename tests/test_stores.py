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


def _codex_config(home: Path, text: str) -> Path:
    p = home / ".codex" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _codex_cache(home: Path, marketplace: str, plugin: str, version: str,
                 manifest: str = "plugin.json") -> Path:
    d = home / ".codex" / "plugins" / "cache" / marketplace / plugin / version
    _skill(d / "skills", f"{plugin}-skill")
    (d / manifest).parent.mkdir(parents=True, exist_ok=True)
    (d / manifest).write_text(
        json.dumps({"name": plugin, "version": version}), encoding="utf-8"
    )
    return d


def test_codex_prefers_local_version_dir(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "sp", "1.0.0")
    local = _codex_cache(home, "mkt", "sp", "local")
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [local / "skills"]
    assert p.version == "local"


def test_codex_highest_version_when_no_local(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "sp", "1.9.0")
    high = _codex_cache(home, "mkt", "sp", "1.10.0")  # numeric, not lexicographic
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    assert plugins[0].skills_roots == [high / "skills"]


def test_codex_disabled_and_missing_cache(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "off", "1.0.0")
    _codex_config(home, (
        '[plugins."off@mkt"]\nenabled = false\n'
        '[plugins."gone@mkt"]\nenabled = true\n'  # no cache dir: skipped
    ))
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.name == "off" and p.enabled is False


def test_codex_agent_plugin_manifest_is_shallow_legacy_recursive(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "ap", "1.0.0", manifest="plugin.json")
    _codex_cache(home, "mkt", "leg", "1.0.0",
                 manifest=".codex-plugin/plugin.json")
    _codex_config(home, (
        '[plugins."ap@mkt"]\nenabled = true\n'
        '[plugins."leg@mkt"]\nenabled = true\n'
    ))
    plugins, _ = discover_plugins("codex", home, proj)
    by_name = {p.name: p for p in plugins}
    assert by_name["ap"].recursive is False
    assert by_name["leg"].recursive is True


def test_codex_manifest_skills_paths_and_migrated_dir(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = home / ".codex" / "plugins" / "cache" / "mkt" / "sp" / "1.0.0"
    (d / ".codex-plugin" / "migrated-command-skills").mkdir(parents=True)
    (d / "myskills").mkdir()
    (d / ".codex-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "sp", "version": "1.0.0", "paths": {"skills": ["myskills"]}}
    ))
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [
        d / "myskills", d / ".codex-plugin" / "migrated-command-skills"
    ]


def test_codex_valid_toml_wrong_shaped_plugins_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = _codex_config(home, 'plugins = "not-a-table"\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def _gemini_ext(home: Path, name: str, version: str = "1.0.0") -> Path:
    d = home / ".gemini" / "extensions" / name
    _skill(d / "skills", f"{name}-skill")
    (d / "gemini-extension.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    return d


def test_gemini_extension_discovered_nonrecursive(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = _gemini_ext(home, "ext-a")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "ext-a" and p.marketplace is None
    assert p.version == "1.0.0" and p.enabled
    assert p.recursive is False  # loader globs SKILL.md + */SKILL.md only
    assert p.skills_roots == [d / "skills"]
    assert p.provenance_source == "ext-a==1.0.0"


def test_gemini_enablement_rules(tmp_path):
    # Semantics from extensionEnablement.ts (commit c0d1924): default
    # enabled; overrides iterate in file order, each matching rule sets
    # enabled = not-disable; "!" = disable; trailing "*" = include
    # subdirs; exact rule matches only that dir.
    home = tmp_path / "home"
    proj = tmp_path / "work" / "sub"
    proj.mkdir(parents=True)
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    work = str((tmp_path / "work").resolve())
    enablement.write_text(json.dumps({
        "ext-a": {"overrides": [f"!{work}/*", f"{str(proj.resolve())}/"]}
    }), encoding="utf-8")
    # disabled under work/*, but the later exact rule re-enables at proj
    plugins, _ = discover_plugins("gemini-cli", home, proj)
    assert plugins[0].enabled is True
    # a sibling dir under work/ only matches the disable rule
    sib = tmp_path / "work" / "other"
    sib.mkdir()
    plugins, _ = discover_plugins("gemini-cli", home, sib)
    assert plugins[0].enabled is False


def test_gemini_exact_rule_does_not_match_subdir(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "work" / "sub"
    proj.mkdir(parents=True)
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text(json.dumps({
        "ext-a": {"overrides": [f"!{str((tmp_path / 'work').resolve())}/"]}
    }), encoding="utf-8")
    plugins, _ = discover_plugins("gemini-cli", home, proj)
    assert plugins[0].enabled is True  # exact rule matches work/ only


def test_gemini_corrupt_enablement_defaults_enabled(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text("{broken", encoding="utf-8")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    # matches gemini's own behavior: corrupt file -> everything enabled;
    # drskill additionally surfaces the file as unreadable
    assert plugins[0].enabled is True
    assert unreadable == [str(enablement)]


def test_gemini_wrong_shaped_enablement_defaults_enabled_and_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    # valid JSON but wrong shape (array instead of dict) -> extension still enabled;
    # file surfaces as unreadable
    assert plugins[0].enabled is True
    assert unreadable == [str(enablement)]


def test_copilot_installed_plugin_with_jsonc_header(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    store = home / ".copilot" / "installed-plugins" / "mkt" / "sp"
    _skill(store / "skills", "sp-skill")
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # copilot writes comment lines BEFORE the JSON body (observed 1.0.80)
    cfg.write_text(
        "// User settings belong in settings.json.\n"
        "// This file is managed automatically.\n"
        + json.dumps({"installedPlugins": [
            {"name": "sp", "marketplace": "mkt", "version": "1.2.3",
             "cache_path": str(store)},
        ]}),
        encoding="utf-8",
    )
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"sp@mkt": True}}), encoding="utf-8"
    )
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "1.2.3"
    assert p.skills_roots == [store / "skills"]
    assert p.enabled and p.scope == "user"


def test_copilot_disabled_and_default_store_path(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    store = home / ".copilot" / "installed-plugins" / "mkt" / "sp"
    _skill(store / "skills", "sp-skill")
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # no cache_path: fall back to installed-plugins/<mkt>/<name>
    cfg.write_text(json.dumps({"installedPlugins": [
        {"name": "sp", "marketplace": "mkt", "version": "1.2.3"},
    ]}), encoding="utf-8")
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"sp@mkt": False}}), encoding="utf-8"
    )
    plugins, _ = discover_plugins("copilot", home, proj)
    (p,) = plugins
    assert p.skills_roots == [store / "skills"]
    assert p.enabled is False


def test_copilot_config_json_valid_but_wrong_shape_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert plugins == [] and unreadable == [str(cfg)]


def test_copilot_installed_plugins_present_but_not_list_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"installedPlugins": "not-a-list"}), encoding="utf-8")
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert plugins == [] and unreadable == [str(cfg)]


def test_droid_installed_plugins(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    install = home / ".factory" / "plugins" / "cache" / "mkt-ab" / "sp-cd" / "v1"
    _skill(install / "skills", "sp-skill")
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    (state_dir / "sp-mkt-user-1234.json").write_text(json.dumps({
        "schemaVersion": 1,
        "pluginId": "sp@mkt",
        "entry": {"scope": "user", "installPath": str(install), "version": "v1"},
    }), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "v1"
    assert p.skills_roots == [install / "skills"]
    assert p.scope == "user" and p.enabled


def test_droid_malformed_entry_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_droid_per_install_json_valid_but_wrong_shape_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_droid_entry_missing_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "missing_entry.json"
    bad.write_text(json.dumps({"schemaVersion": 1, "pluginId": "sp@mkt"}), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_droid_scope_not_user_is_skipped_not_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    install = home / ".factory" / "plugins" / "cache" / "mkt-ab" / "sp-cd" / "v1"
    _skill(install / "skills", "sp-skill")
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    (state_dir / "sp-mkt-org-1234.json").write_text(json.dumps({
        "schemaVersion": 1,
        "pluginId": "sp@mkt",
        "entry": {"scope": "organization", "installPath": str(install), "version": "v1"},
    }), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == []


# --- adversarial / corrupt state (final review, critical 1 & minor 6) -----

def test_claude_code_int_install_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = _cc_state(home, {"sp@mkt": [
        {"scope": "user", "installPath": 123, "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_int_project_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "1.0.0")
    p = _cc_state(home, {"sp@mkt": [
        {"scope": "local", "projectPath": 5,
         "installPath": str(active), "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_nul_byte_project_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "1.0.0")
    p = _cc_state(home, {"sp@mkt": [
        {"scope": "local", "projectPath": "bad\x00path",
         "installPath": str(active), "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_int_version_is_stringified_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "6.0.0")
    _cc_state(home, {"sp@mkt": [
        {"scope": "user", "installPath": str(active), "version": 6},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.version == "6"  # other adapters str()-wrap version; match them


def test_claude_code_empty_plugin_name_is_skipped(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "1.0.0")
    _cc_state(home, {"@mkt": [
        {"scope": "user", "installPath": str(active), "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    # key "@mkt" partitions to name "" -- an empty name silently defeats
    # suites' pre-attribution skip, so it must be dropped entirely
    assert plugins == [] and unreadable == []


def test_codex_non_dict_manifest_paths_no_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = home / ".codex" / "plugins" / "cache" / "mkt" / "sp" / "1.0.0"
    _skill(d / "skills", "sp-skill")
    # "paths" is truthy but not a dict/table -- (manifest.get("paths") or {})
    # would previously call .get() on the string and raise AttributeError
    (d / "plugin.json").write_text(
        json.dumps({"name": "sp", "version": "1.0.0", "paths": "skills"})
    )
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.skills_roots == [d / "skills"]  # falls back to the default


def test_codex_non_ascii_digit_version_dir_no_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    good = _codex_cache(home, "mkt", "sp", "1.0.0")
    # "²" (superscript two) satisfies str.isdigit() but int() raises
    # ValueError on it -- a directory named this way must not crash sorting
    bad_dir = home / ".codex" / "plugins" / "cache" / "mkt" / "sp" / "².0"
    bad_dir.mkdir(parents=True)
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [good / "skills"]
    assert p.version == "1.0.0"


def test_codex_missing_plugins_table_is_normal_not_unreadable(tmp_path):
    # config.toml exists for MCP/model settings with no [plugins] table at
    # all -- this is the NORMAL case, not unreadable state (critical 2).
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_config(home, '[model]\nname = "x"\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert plugins == [] and unreadable == []


def test_codex_invalid_toml_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = _codex_config(home, 'not = valid = toml =\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_copilot_int_cache_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"installedPlugins": [
        {"name": "sp", "marketplace": "mkt", "version": "1.2.3", "cache_path": 123},
    ]}), encoding="utf-8")
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert plugins == [] and unreadable == [str(cfg)]


def test_droid_int_install_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "sp-mkt-user-1234.json"
    bad.write_text(json.dumps({
        "schemaVersion": 1,
        "pluginId": "sp@mkt",
        "entry": {"scope": "user", "installPath": 123, "version": "v1"},
    }), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_discover_plugins_never_crashes_on_unexpected_adapter_exception(tmp_path, monkeypatch):
    from drskill import stores as stores_mod

    def boom(home, project_root):
        raise RuntimeError("simulated adapter bug, unforeseen by per-field guards")

    monkeypatch.setitem(stores_mod.ADAPTERS, "claude-code", boom)
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == []
    assert unreadable == [str(home / ".claude" / "plugins" / "installed_plugins.json")]
