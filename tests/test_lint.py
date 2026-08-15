import json
from pathlib import Path

import pytest

from drskill.lint import LintUsageError, classify


def make_plugin(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "demo-plugin",
    }))


def test_classify_plugin_dir(tmp_path):
    make_plugin(tmp_path / "p")
    t = classify(tmp_path / "p")
    assert t.kind == "plugin"


def test_classify_skill_dir_and_file(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nb\n")
    assert classify(d).kind == "skill"
    assert classify(d / "SKILL.md").kind == "skill"


def test_plugin_wins_over_skill(tmp_path):
    d = tmp_path / "both"
    make_plugin(d)
    (d / "SKILL.md").write_text("---\nname: both\ndescription: d\n---\nb\n")
    assert classify(d).kind == "plugin"


def test_classify_mcp_agent_plugins_flavor(tmp_path):
    f = tmp_path / "anything.json"
    f.write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {},
    }))
    t = classify(f)
    assert t.kind == "mcp" and t.mcp_flavor == "agent-plugins"


def test_classify_mcp_next_to_plugin_json(tmp_path):
    make_plugin(tmp_path)
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {}}))
    assert classify(f).mcp_flavor == "agent-plugins"


def test_classify_mcp_harness_flavor(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"s": {"command": "srv"}}}))
    t = classify(f)
    assert t.kind == "mcp" and t.mcp_flavor == "harness"


def test_classify_unparseable_mcp_json_still_mcp(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text("{not json")
    assert classify(f).kind == "mcp"


def test_classify_rejects_unknown(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(LintUsageError):
        classify(d)
    with pytest.raises(LintUsageError):
        classify(tmp_path / "missing")


def test_forced_type_overrides(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nb\n")
    make_plugin(d)
    assert classify(d, forced="skill").kind == "skill"


def test_forced_skill_rejects_non_skill_md_file(tmp_path):
    # Regression: forced --type skill used to accept ANY existing file, then
    # the SKILL.md-spec checks silently skip non-SKILL.md targets, producing
    # a false-clean lint. A forced skill file target must be named SKILL.md.
    f = tmp_path / "some.mcp.json"
    f.write_text("{}")
    with pytest.raises(LintUsageError):
        classify(f, forced="skill")


from drskill.lint import build_lint_world


def write_skill(d: Path, name: str):
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when testing {name}.\n---\nbody\n"
    )


def test_build_world_skill_target(tmp_path):
    write_skill(tmp_path / "s", "s")
    w = build_lint_world(classify(tmp_path / "s"))
    assert len(w.contributors) == 1
    c = next(iter(w.contributors.values()))
    assert c.name == "s" and c.deployments == []
    assert w.plugin is None


def test_build_world_plugin_target(tmp_path):
    root = tmp_path / "p"
    make_plugin(root)
    write_skill(root / "skills" / "alpha", "alpha")
    write_skill(root / "skills" / "beta", "beta")
    # nested too deep: not discovered as a contributor
    write_skill(root / "skills" / "group" / "gamma", "gamma")
    (root / "mcp.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"srv": {"type": "stdio", "command": "server-bin",
                               "env": {"API_KEY": "sk-live-1234567890abcdef"}}},
    }))
    w = build_lint_world(classify(root))
    assert w.plugin is not None and w.plugin.name == "demo-plugin"
    assert sorted(c.name for c in w.contributors.values()) == ["alpha", "beta"]
    assert len(w.mcp_servers) == 1
    s = w.mcp_servers[0]
    assert s.harness == "" and s.in_project is True
    assert w.plugin_mcp is not None and w.plugin_mcp.data is not None


def test_build_world_bad_manifest_and_bad_mcp(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "plugin.json").write_text("{broken")
    (root / "mcp.json").write_text("{also broken")
    w = build_lint_world(classify(root))
    assert w.plugin.parse_error is not None
    assert w.plugin_mcp.data is None
    assert len(w.mcp_config_errors) == 1


def test_build_world_standalone_mcp_provisional_root(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {},
    }))
    w = build_lint_world(classify(f))
    assert w.plugin_mcp.provisional_root is True
    assert w.plugin_mcp.root == str(tmp_path.resolve())


def test_build_world_harness_mcp(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"a": {"command": "foo"}}}))
    w = build_lint_world(classify(f))
    assert w.plugin_mcp is None
    assert [s.name for s in w.mcp_servers] == ["a"]


def test_suites_only_name_registered_checks():
    from drskill.checks import REGISTRY, run_all  # noqa: F401  (run_all imports all modules)
    from drskill.resolution import World
    from drskill.ledger import Config
    from drskill import lint as lint_mod

    run_all(World(), Config())  # force-register every check module
    # plugin_spec / mcp_spec ids only exist after Tasks 6-7; tolerate both
    # phases by checking the suites that must already resolve.
    assert set(lint_mod.SKILL_CONTENT_CHECKS) <= set(REGISTRY)
    assert set(lint_mod.MCP_STATIC_CHECKS) <= set(REGISTRY)
    assert set(lint_mod.MCP_CONNECT_CHECKS) <= set(REGISTRY)


def test_run_checks_runs_only_named(tmp_path):
    from drskill.checks import run_checks
    from drskill.ledger import Config

    write_skill(tmp_path / "s", "other-name")  # folder 's', name mismatch
    w = build_lint_world(classify(tmp_path / "s"))
    findings = run_checks(w, Config(), ["spec-name-mismatch"])
    assert {f.check_id for f in findings} == {"spec-name-mismatch"}


def test_checks_for_shapes():
    from drskill.lint import LintTarget, checks_for, SKILL_CONTENT_CHECKS

    skill = LintTarget(kind="skill", path=Path("."))
    assert checks_for(skill, mcp_connect=False) == SKILL_CONTENT_CHECKS
    plug = LintTarget(kind="plugin", path=Path("."))
    ids = checks_for(plug, mcp_connect=False)
    assert "exact-duplicate" in ids and "mcp-secret-in-config" in ids
    assert "name-shadow" not in ids and "lockfile-drift" not in ids
    harness = LintTarget(kind="mcp", path=Path("."), mcp_flavor="harness")
    ids = checks_for(harness, mcp_connect=False)
    assert "mcp-dead-server" in ids and "mcp-insecure-url" in ids
    agent = LintTarget(kind="mcp", path=Path("."), mcp_flavor="agent-plugins")
    assert "mcp-dead-server" not in checks_for(agent, mcp_connect=False)
    assert "mcp-tool-poisoning" in checks_for(agent, mcp_connect=True)


def test_find_config_root_walks_up(tmp_path):
    from drskill.lint import find_config_root

    (tmp_path / "drskill.toml").write_text("")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config_root(nested) == tmp_path
    # no drskill.toml above tmp_path's parent is not guaranteed; use an
    # isolated dir instead
    iso = tmp_path / "iso"
    iso.mkdir()
    (iso / "x").mkdir()
    # config root falls back to the start dir when nothing is found before
    # the filesystem root that contains one; assert the found root is an
    # ancestor-or-self of the start
    got = find_config_root(iso / "x")
    assert got in [iso / "x", *(iso / "x").parents]


def test_run_lint_skill_target(tmp_path):
    from drskill.ledger import Config
    from drskill.lint import run_lint

    write_skill(tmp_path / "s", "other-name")  # name mismatch -> error
    target = classify(tmp_path / "s")
    world, findings = run_lint(target, Config(), tmp_path, tmp_path / "home")
    assert any(f.check_id == "spec-name-mismatch" for f in findings)
    assert all(f.harnesses == [] for f in findings)


def test_run_lint_plugin_end_to_end(tmp_path):
    from drskill.ledger import Config
    from drskill.lint import run_lint

    root = tmp_path / "p"
    make_plugin(root)
    write_skill(root / "skills" / "alpha", "alpha")
    (root / "mcp.json").write_text(json.dumps({
        "mcpServers": {"bad": {"type": "websocket", "url": "wss://x"}}}))
    world, findings = run_lint(
        classify(root), Config(), tmp_path, tmp_path / "home")
    got = {f.check_id for f in findings}
    assert "mcp-spec-invalid" in got  # missing $schema + bad transport
    assert all(f.harnesses == [] for f in findings)


def test_run_lint_applies_cached_deep_verdicts_without_judge(tmp_path, monkeypatch):
    # Regression: a plain `drskill lint` (no --deep, judge=None) must still
    # reshape findings using a cache populated by an earlier --deep run —
    # mirroring run_scan's tail, where cdir/cache/acked_fps are computed
    # unconditionally and apply_verdicts always runs (only judge_pairs is
    # gated on judge). Monkeypatch deep.apply_verdicts to record whether it
    # was reached with judge=None, and to prove the harness-strip step runs
    # AFTER apply_verdicts (it must clobber a harness apply_verdicts sets).
    from drskill import deep
    from drskill.ledger import Config
    from drskill.lint import run_lint

    write_skill(tmp_path / "s", "other-name")  # name mismatch -> error
    target = classify(tmp_path / "s")

    calls = []

    def fake_apply_verdicts(world, findings, cache, acked_fps):
        calls.append((cache, acked_fps))
        return [
            f.model_copy(update={"harnesses": ["fake-harness"], "message": "APPLIED"})
            for f in findings
        ]

    monkeypatch.setattr(deep, "apply_verdicts", fake_apply_verdicts)

    world, findings = run_lint(target, Config(), tmp_path, tmp_path / "home", judge=None)

    assert calls, "apply_verdicts must run even without --deep (judge=None)"
    cache, acked_fps = calls[0]
    assert isinstance(cache, dict) and isinstance(acked_fps, set)
    assert findings and all(f.message == "APPLIED" for f in findings)
    # The strip-harnesses step ran after apply_verdicts: it nulled out the
    # harness the fake apply_verdicts injected.
    assert all(f.harnesses == [] for f in findings)


# Tests for Claude Code plugin layout and marketplace classification
import json


def _cc_plugin(tmp_path, manifest=None):
    root = tmp_path / "ccplug"
    (root / ".claude-plugin").mkdir(parents=True)
    m = {"name": "my-plugin"} if manifest is None else manifest
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(m))
    return root


def test_classify_claude_plugin_dir(tmp_path):
    root = _cc_plugin(tmp_path)
    t = classify(root)
    assert t.kind == "plugin" and t.plugin_flavor == "claude-code"
    assert t.dual_manifest is False


def test_classify_agent_plugins_sets_flavor(tmp_path):
    root = tmp_path / "applug"
    root.mkdir()
    (root / "plugin.json").write_text('{"name": "x"}')
    t = classify(root)
    assert t.kind == "plugin" and t.plugin_flavor == "agent-plugins"


def test_classify_dual_manifest(tmp_path):
    root = _cc_plugin(tmp_path)
    (root / "plugin.json").write_text('{"name": "my-plugin"}')
    t = classify(root)
    assert t.plugin_flavor == "agent-plugins" and t.dual_manifest is True


def test_classify_marketplace_dir_and_file(tmp_path):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    mp = root / ".claude-plugin" / "marketplace.json"
    mp.write_text('{"name": "m", "owner": {"name": "o"}, "plugins": []}')
    assert classify(root).kind == "marketplace"
    assert classify(mp).kind == "marketplace"


def test_classify_forced_marketplace(tmp_path):
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text("{}")
    assert classify(root, forced="marketplace").kind == "marketplace"
    plain = tmp_path / "plain"
    plain.mkdir()
    try:
        classify(plain, forced="marketplace")
        raise AssertionError("expected LintUsageError")
    except LintUsageError:
        pass


def test_classify_forced_plugin_accepts_claude_layout(tmp_path):
    root = _cc_plugin(tmp_path)
    t = classify(root, forced="plugin")
    assert t.kind == "plugin" and t.plugin_flavor == "claude-code"


def test_classify_empty_dir_error_mentions_claude_plugin(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    try:
        classify(d)
        raise AssertionError("expected LintUsageError")
    except LintUsageError as e:
        assert ".claude-plugin" in str(e)


def _mk_skill_dir(base, name):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when testing {name}.\n---\nbody\n"
    )


def test_cc_world_collects_manifest_and_skills(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path, {"name": "my-plugin", "skills": ["extra-skills"]})
    _mk_skill_dir(root / "skills", "alpha")
    _mk_skill_dir(root / "extra-skills", "beta")
    world = build_lint_world(classify(root))
    assert world.cc_plugin is not None and world.cc_plugin.name == "my-plugin"
    assert world.plugin is None  # agent-plugins checks must no-op
    names = {c.name for c in world.contributors.values()}
    assert names == {"alpha", "beta"}


def test_cc_world_root_single_skill(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path)
    (root / "SKILL.md").write_text(
        "---\nname: solo\ndescription: Use when testing solo.\n---\nbody\n"
    )
    world = build_lint_world(classify(root))
    assert {c.name for c in world.contributors.values()} == {"solo"}


def test_cc_world_mcp_from_inline_and_default(tmp_path):
    from drskill.lint import build_lint_world

    inline = _cc_plugin(tmp_path, {"name": "p", "mcpServers": {
        "srv": {"command": "run-srv"}
    }})
    world = build_lint_world(classify(inline))
    assert [s.name for s in world.mcp_servers] == ["srv"]

    filed = _cc_plugin(tmp_path / "sub", {"name": "p2"})
    (filed / ".mcp.json").write_text('{"mcpServers": {"filed": {"command": "x"}}}')
    world2 = build_lint_world(classify(filed))
    assert [s.name for s in world2.mcp_servers] == ["filed"]


def test_dual_manifest_world_has_both(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path, {"name": "my-plugin", "version": "2.0.0"})
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "my-plugin", "version": "1.0.0", "description": "d",
    }))
    world = build_lint_world(classify(root))
    assert world.plugin is not None and world.cc_plugin is not None


def test_marketplace_world(tmp_path):
    from drskill.lint import build_lint_world

    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    mp = root / ".claude-plugin" / "marketplace.json"
    mp.write_text('{"name": "m", "owner": {"name": "o"}, "plugins": []}')
    world = build_lint_world(classify(root))
    assert world.marketplace is not None
    assert world.marketplace.root == str(root.resolve())
    assert world.marketplace.data == {"name": "m", "owner": {"name": "o"}, "plugins": []}
    # file target resolves the same root
    world2 = build_lint_world(classify(mp))
    assert world2.marketplace.root == str(root.resolve())


def test_plugin_with_sibling_marketplace_loads_it(tmp_path):
    from drskill.lint import build_lint_world

    root = _cc_plugin(tmp_path)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "m", "owner": {"name": "o"}, "plugins": []}'
    )
    world = build_lint_world(classify(root))
    assert world.marketplace is not None
