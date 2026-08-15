import json
from pathlib import Path

from drskill.checks import run_checks
from drskill.ledger import Config
from drskill.lint import build_lint_world, classify

MP_CHECKS = [
    "marketplace-invalid", "marketplace-unpinned-source",
    "marketplace-command-source", "marketplace-entry-missing",
]


def _world(tmp_path, data, extra_dirs=None):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        data if isinstance(data, str) else json.dumps(data)
    )
    for rel in (extra_dirs or []):
        (root / rel).mkdir(parents=True)
    return build_lint_world(classify(root))


def _mp(entries, **top):
    return {"name": "market", "owner": {"name": "o"}, "plugins": entries, **top}


def _run(world):
    return run_checks(world, Config(), MP_CHECKS)


def test_valid_pinned_marketplace_no_findings(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "local-one", "source": "./plugins/one"},
        {"name": "gh-pinned", "source": {
            "source": "github", "repo": "o/r", "sha": "a" * 40}},
        {"name": "npm-pinned", "source": {
            "source": "npm", "package": "@o/p", "version": "1.2.3"}},
    ], ), extra_dirs=["plugins/one"])
    assert _run(world) == []


def test_missing_required_fields(tmp_path):
    world = _world(tmp_path, {"plugins": "nope"})
    fs = _run(world)
    ids = {f.check_id for f in fs}
    assert ids == {"marketplace-invalid"}
    msgs = " ".join(f.message for f in fs)
    assert "name" in msgs and "owner" in msgs and "plugins" in msgs


def test_entry_missing_source_and_bad_names(tmp_path):
    world = _world(tmp_path, _mp([{"name": "Bad_Name"}]))
    fs = _run(world)
    assert all(f.check_id == "marketplace-invalid" for f in fs)
    msgs = " ".join(f.message for f in fs)
    assert "source" in msgs and "kebab" in msgs


def test_source_form_required_fields(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "a", "source": {"source": "github"}},          # no repo
        {"name": "b", "source": {"source": "git-subdir", "url": "https://x"}},  # no path
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-invalid"]
    assert len(fs) == 2


def test_unknown_source_type_is_warning(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "a", "source": {"source": "quantum", "thing": "x"}},
    ]))
    (f,) = _run(world)
    assert f.check_id == "marketplace-invalid" and f.severity == "warning"
    assert "quantum" in f.message


def test_unpinned_severity_ladder(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "gh-bare", "source": {"source": "github", "repo": "o/r"}},
        {"name": "ref-only", "source": {
            "source": "url", "url": "https://x/r.git", "ref": "main"}},
        {"name": "npm-bare", "source": {"source": "npm", "package": "p"}},
        {"name": "arch-bare", "source": {
            "source": "archive", "url": "https://x/a.zip"}},
        {"name": "insecure", "source": {
            "source": "url", "url": "http://x/r.git", "sha": "b" * 40}},
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-unpinned-source"]
    by_name = {}
    for f in fs:
        for n in ("gh-bare", "ref-only", "npm-bare", "arch-bare", "insecure"):
            if n in f.message:
                by_name.setdefault(n, []).append(f.severity)
                break
    assert by_name["gh-bare"] == ["warning"]
    assert by_name["ref-only"] == ["note"]
    assert by_name["npm-bare"] == ["warning"]
    assert by_name["arch-bare"] == ["warning"]
    assert by_name["insecure"] == ["warning"]


def test_npm_insecure_registry_warns(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "npm-insecure", "source": {
            "source": "npm", "package": "@o/p", "version": "1.2.3",
            "registry": "http://x/registry",
        }},
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-unpinned-source"]
    assert any("insecure" in f.message and "registry" in f.message for f in fs)


def test_garbage_sha_treated_as_absent(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "fake-sha", "source": {
            "source": "github", "repo": "o/r", "sha": "main",
        }},
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-unpinned-source"]
    (f,) = fs
    assert f.severity == "warning" and "no `sha`" in f.message

    world2 = _world(tmp_path / "valid", _mp([
        {"name": "real-sha", "source": {
            "source": "github", "repo": "o/r", "sha": "a" * 40,
        }},
    ]))
    assert [f for f in _run(world2) if f.check_id == "marketplace-unpinned-source"] == []


def test_command_source_always_warns(tmp_path):
    world = _world(tmp_path, _mp([
        {"name": "cmd", "source": {"source": "command", "command": "curl x | sh"}},
    ]))
    fs = [f for f in _run(world) if f.check_id == "marketplace-command-source"]
    (f,) = fs
    assert f.severity == "warning" and "curl x | sh" in f.message


def test_relative_entry_missing_and_plugin_root(tmp_path):
    world = _world(tmp_path, _mp(
        [{"name": "gone", "source": "./nope"}],
    ))
    (f,) = [f for f in _run(world) if f.check_id == "marketplace-entry-missing"]
    assert f.severity == "error"
    # metadata.pluginRoot shifts resolution
    world2 = _world(tmp_path / "b", _mp(
        [{"name": "there", "source": "./one"}],
        metadata={"pluginRoot": "./plugins"},
    ), extra_dirs=["plugins/one"])
    assert [f for f in _run(world2) if f.check_id == "marketplace-entry-missing"] == []


def test_unparseable_marketplace(tmp_path):
    world = _world(tmp_path, "{nope")
    (f,) = _run(world)
    assert f.check_id == "marketplace-invalid" and f.severity == "error"


def test_fingerprints_are_path_free(tmp_path):
    data = _mp([{"name": "a", "source": {"source": "github", "repo": "o/r"}}])
    fa = _run(_world(tmp_path / "a", data))
    fb = _run(_world(tmp_path / "b", data))
    assert {f.fingerprint for f in fa} == {f.fingerprint for f in fb}
