import json
from pathlib import Path

from drskill.checks import run_checks
from drskill.ledger import Config
from drskill.lint import build_lint_world, classify

CC_CHECKS = [
    "cc-manifest-invalid", "cc-manifest-unknown-field",
    "cc-component-missing", "cc-manifest-mismatch",
]


def _world(tmp_path, manifest, extra=None):
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        manifest if isinstance(manifest, str) else json.dumps(manifest)
    )
    for rel in (extra or []):
        (root / rel).mkdir(parents=True)
    return build_lint_world(classify(root)), root


def _run(world):
    return run_checks(world, Config(), CC_CHECKS)


def test_valid_manifest_no_findings(tmp_path):
    world, _ = _world(tmp_path, {"name": "my-plugin", "version": "1.0.0"})
    assert _run(world) == []


def test_unparseable_and_missing_name_are_errors(tmp_path):
    world, _ = _world(tmp_path, "{nope")
    (f,) = _run(world)
    assert f.check_id == "cc-manifest-invalid" and f.severity == "error"

    world2, _ = _world(tmp_path / "b", {"version": "1.0.0"})
    (f2,) = _run(world2)
    assert f2.check_id == "cc-manifest-invalid" and "name" in f2.message


def test_name_not_kebab_is_error(tmp_path):
    world, _ = _world(tmp_path, {"name": "My_Plugin"})
    (f,) = _run(world)
    assert f.check_id == "cc-manifest-invalid" and "kebab" in f.message


def test_pointer_wrong_type_is_error(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "commands": 42})
    fs = [f for f in _run(world) if f.check_id == "cc-manifest-invalid"]
    assert fs and "commands" in fs[0].message


def test_inline_object_ok_for_hooks_not_commands(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "hooks": {"PreToolUse": []}})
    assert _run(world) == []
    world2, _ = _world(tmp_path / "b", {"name": "p", "commands": {"x": 1}})
    assert any(f.check_id == "cc-manifest-invalid" for f in _run(world2))


def test_unknown_field_is_warning(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "colour": "red"})
    (f,) = _run(world)
    assert f.check_id == "cc-manifest-unknown-field" and f.severity == "warning"
    assert "colour" in f.message


def test_declared_component_missing_is_error(tmp_path):
    world, _ = _world(tmp_path, {"name": "p", "commands": "./cmds"})
    (f,) = _run(world)
    assert f.check_id == "cc-component-missing" and f.severity == "error"
    world2, _ = _world(
        tmp_path / "b", {"name": "p", "commands": "./cmds"}, extra=["cmds"]
    )
    assert _run(world2) == []


def test_absent_defaults_never_flagged(tmp_path):
    # no skills/, no commands/ etc. and no pointers: zero findings
    world, _ = _world(tmp_path, {"name": "p"})
    assert _run(world) == []


def test_dual_manifest_mismatch_is_warning(tmp_path):
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "my-plugin", "version": "2.0.0"})
    )
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "my-plugin", "version": "1.0.0", "description": "d",
    }))
    world = build_lint_world(classify(root))
    fs = [f for f in run_checks(world, Config(), CC_CHECKS)
          if f.check_id == "cc-manifest-mismatch"]
    (f,) = fs
    assert f.severity == "warning" and "1.0.0" in f.message and "2.0.0" in f.message


def _dual_world(tmp_path, subdir, cc_version, root_version):
    root = tmp_path / subdir
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "my-plugin", "version": cc_version})
    )
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "my-plugin", "version": root_version, "description": "d",
    }))
    return build_lint_world(classify(root))


def test_mismatch_fingerprint_covers_both_manifests(tmp_path):
    # Same cc manifest, but the root plugin.json version differs: the
    # mismatch check judges BOTH manifests, so an ack keyed only on the cc
    # manifest would silently keep matching a still-mismatched pair.
    world_a = _dual_world(tmp_path / "a", "plug", "2.0.0", "1.0.0")
    world_b = _dual_world(tmp_path / "b", "plug", "2.0.0", "1.5.0")
    (fa,) = [f for f in run_checks(world_a, Config(), CC_CHECKS)
             if f.check_id == "cc-manifest-mismatch"]
    (fb,) = [f for f in run_checks(world_b, Config(), CC_CHECKS)
             if f.check_id == "cc-manifest-mismatch"]
    assert fa.fingerprint != fb.fingerprint


def test_mismatch_fingerprint_is_path_free(tmp_path):
    world_a = _dual_world(tmp_path / "a", "plug", "2.0.0", "1.0.0")
    world_b = _dual_world(tmp_path / "b", "different-dir-name", "2.0.0", "1.0.0")
    (fa,) = [f for f in run_checks(world_a, Config(), CC_CHECKS)
             if f.check_id == "cc-manifest-mismatch"]
    (fb,) = [f for f in run_checks(world_b, Config(), CC_CHECKS)
             if f.check_id == "cc-manifest-mismatch"]
    assert fa.fingerprint == fb.fingerprint


def test_fingerprints_are_path_free(tmp_path):
    world_a, _ = _world(tmp_path / "a", {"name": "My_Plugin"})
    world_b, _ = _world(tmp_path / "b", {"name": "My_Plugin"})
    (fa,), (fb,) = _run(world_a), _run(world_b)
    assert fa.fingerprint == fb.fingerprint
