import json
import os
from pathlib import Path

from drskill.checks import run_checks
from drskill.checks.plugin_spec import valid_plugin_name
from drskill.ledger import Config
from drskill.lint import PLUGIN_SPEC_CHECKS, build_lint_world, classify


def run(root: Path):
    return run_checks(build_lint_world(classify(root)), Config(), PLUGIN_SPEC_CHECKS)


def by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check_id, []).append(f)
    return out


def plugin(tmp_path, manifest: dict | str) -> Path:
    root = tmp_path / "p"
    root.mkdir(exist_ok=True)
    text = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (root / "plugin.json").write_text(text)
    return root


GOOD = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "demo-plugin",
    "version": "1.0.0",
}


def test_name_rules():
    assert valid_plugin_name("my-plugin") and valid_plugin_name("acme.tools")
    assert valid_plugin_name("a") and valid_plugin_name("lint3r")
    for bad in ("", "-x", "x-", ".x", "x.", "a--b", "a..b", "a.-b", "UPPER",
                "has space", "x" * 65):
        assert not valid_plugin_name(bad), bad


def test_clean_plugin_no_findings(tmp_path):
    root = plugin(tmp_path, GOOD)
    assert run(root) == []


def test_unparseable_manifest_is_error(tmp_path):
    root = plugin(tmp_path, "{broken")
    got = by_check(run(root))
    assert got["plugin-manifest-invalid"][0].severity == "error"


def test_missing_required_fields(tmp_path):
    root = plugin(tmp_path, {"description": "no schema, no name"})
    msgs = " ".join(f.message for f in by_check(run(root))["plugin-manifest-invalid"])
    assert "$schema" in msgs and "name" in msgs


def test_bad_name_is_error(tmp_path):
    root = plugin(tmp_path, {**GOOD, "name": "Bad--Name"})
    assert by_check(run(root))["plugin-name-invalid"][0].severity == "error"


def test_unknown_field_and_bad_extensions_warn(tmp_path):
    root = plugin(tmp_path, {**GOOD, "surprise": 1, "extensions": "nope"})
    got = by_check(run(root))
    assert len(got["plugin-manifest-unknown-field"]) == 2
    assert got["plugin-manifest-unknown-field"][0].severity == "warning"


def test_unknown_schema_version_warns(tmp_path):
    root = plugin(tmp_path, {**GOOD,
        "$schema": "https://agent-plugins.org/schemas/9.9.9/plugin.schema.json"})
    assert "plugin-schema-unknown" in by_check(run(root))


def test_undiscoverable_skills_warn(tmp_path):
    root = plugin(tmp_path, GOOD)
    deep = root / "skills" / "group" / "nested"
    deep.mkdir(parents=True)
    (deep / "SKILL.md").write_text("---\nname: nested\ndescription: d\n---\nb\n")
    (root / "skills" / "empty-child").mkdir()
    got = by_check(run(root))
    assert len(got["plugin-skill-undiscoverable"]) == 2


def test_symlink_escape_is_error(tmp_path):
    root = plugin(tmp_path, GOOD)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (root / "skills").mkdir()
    os.symlink(outside, root / "skills" / "leak")
    got = by_check(run(root))
    assert got["plugin-path-escape"][0].severity == "error"


def test_extension_hygiene(tmp_path):
    root = plugin(tmp_path, GOOD)
    # invalid namespace: looks like a namespace (has a dot) but bad label
    (root / "com..bad").mkdir()
    # secret inside a valid namespace dir
    ns = root / "com.example.client"
    ns.mkdir()
    (ns / "settings.json").write_text(json.dumps(
        {"api_key": "sk-live-1234567890abcdef"}))
    # namespace dir shadowing portable components
    shadow = root / "com.example.other"
    (shadow / "skills" / "s").mkdir(parents=True)
    (shadow / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\nb\n")
    got = by_check(run(root))
    assert len(got["plugin-extension-hygiene"]) == 3
    assert all(f.severity == "warning" for f in got["plugin-extension-hygiene"])


def test_extension_hygiene_survives_dangling_json_symlink(tmp_path):
    root = plugin(tmp_path, GOOD)
    ns = root / "com.example.client"
    ns.mkdir()
    # Symlink points at a nonexistent path INSIDE the root, so it resolves
    # without escaping and plugin-path-escape has nothing to flag; only
    # extension-hygiene's secret scan touches this file, and it must not
    # crash on a dangling stat().
    os.symlink(root / "nonexistent-target.json", ns / "dangling.json")
    got = by_check(run(root))  # must not raise
    assert "plugin-path-escape" not in got
    assert "plugin-extension-hygiene" not in got
