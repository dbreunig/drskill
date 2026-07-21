from pathlib import Path

from drskill import deep
from drskill.models import Contributor, TokenCost


def contributor(name: str, description: str, cid: str | None = None) -> Contributor:
    return Contributor(
        id=cid or f"/skills/{name}/SKILL.md",
        name=name,
        scope="project",
        routing_text=description,
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash=f"hash-{name}",
    )


def test_pair_key_is_order_independent():
    a = contributor("alpha", "Use when writing documentation pages.")
    b = contributor("beta", "Use when writing documentation summaries.")
    assert deep.pair_key(a, b) == deep.pair_key(b, a)
    assert len(deep.pair_key(a, b)) == 64


def test_pair_key_changes_when_a_description_changes():
    a = contributor("alpha", "Use when writing documentation pages.")
    b = contributor("beta", "Use when writing documentation summaries.")
    b2 = contributor("beta", "Use when writing release notes.")
    assert deep.pair_key(a, b) != deep.pair_key(a, b2)


def test_cache_round_trip(tmp_path):
    v = deep.Verdict(
        verdict="distinct", rationale="different targets", detail="pages vs notes",
        model="anthropic/claude-haiku-4-5", program_version="0.2.0", date="2026-07-21",
    )
    deep.save_verdict(tmp_path / "cache", "ab" * 32, v)
    loaded = deep.load_cache(tmp_path / "cache")
    assert loaded == {"ab" * 32: v}


def test_load_cache_ignores_corrupt_entries(tmp_path):
    cdir = tmp_path / "cache"
    cdir.mkdir()
    (cdir / ("ff" * 32 + ".json")).write_text("{not json")
    assert deep.load_cache(cdir) == {}


def test_cache_dir_locations(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    assert deep.cache_dir(proj, home, False) == proj / ".drskill" / "cache"
    assert deep.cache_dir(proj, home, True) == home / ".drskill" / "cache"


from drskill.models import Finding


def finding_for(check_id, members, severity="warning"):
    return Finding(
        check_id=check_id, severity=severity,
        contributors=[m.id for m in members],
        contributor_names=sorted({m.name for m in members}),
        harnesses=["claude-code"], message="msg",
        fingerprint=f"sha256:{'0' * 60}{len(members)}{check_id[:3]}",
    )


class FakeWorld:
    def __init__(self, members):
        self.contributors = {m.id: m for m in members}


def test_flagged_pairs_largest_cluster_first_then_names():
    a, b, c = (contributor(n, f"Use for {n} docs.") for n in ("a", "b", "c"))
    x, y = (contributor(n, f"Use for {n} docs.") for n in ("x", "y"))
    world = FakeWorld([a, b, c, x, y])
    findings = [
        finding_for("description-overlap", [x, y]),
        finding_for("description-overlap", [c, b, a]),
        finding_for("missing-activation", [a]),
    ]
    pairs = deep.flagged_pairs(world, findings)
    assert [(p[0].name, p[1].name) for p in pairs] == [
        ("a", "b"), ("a", "c"), ("b", "c"), ("x", "y"),
    ]


def test_unjudged_count(tmp_path):
    a, b = contributor("a", "Use for a docs."), contributor("b", "Use for b docs.")
    world = FakeWorld([a, b])
    findings = [finding_for("description-overlap", [a, b])]
    assert deep.unjudged_count(world, findings, {}) == 1
    cache = {deep.pair_key(a, b): deep.Verdict(
        verdict="distinct", rationale="r", detail="d",
        model="m", program_version="v", date="2026-07-21",
    )}
    assert deep.unjudged_count(world, findings, cache) == 0


def _verdict(cls, rationale="r", detail="d", model="test-model", date="2026-07-21"):
    return deep.Verdict(
        verdict=cls, rationale=rationale, detail=detail,
        model=model, program_version="v", date=date,
    )


def _pair_world():
    a = contributor("alpha", "Use when writing documentation pages.")
    b = contributor("beta", "Use when writing documentation summaries.")
    return a, b, FakeWorld([a, b])


def test_apply_verdicts_empty_cache_is_identity():
    a, b, world = _pair_world()
    findings = [finding_for("description-overlap", [a, b])]
    assert deep.apply_verdicts(world, findings, {}, set()) is findings


def test_all_distinct_downgrades_to_note():
    a, b, world = _pair_world()
    f = finding_for("description-overlap", [a, b])
    cache = {deep.pair_key(a, b): _verdict("distinct")}
    (out,) = deep.apply_verdicts(world, [f], cache, set())
    assert out.severity == "note"
    assert out.message == "overlap flagged (alpha, beta); judged distinct by test-model, 2026-07-21"
    assert out.fix_commands == []
    assert out.fingerprint == f.fingerprint


def test_collision_verdict_keeps_warning_with_evidence():
    a, b, world = _pair_world()
    f = finding_for("description-overlap", [a, b])
    cache = {deep.pair_key(a, b): _verdict(
        "description_collision", rationale="same scope words", detail="write the docs page",
    )}
    (out,) = deep.apply_verdicts(world, [f], cache, set())
    assert out.severity == "warning"
    assert "deep: alpha vs beta: description_collision; same scope words" in out.message
    assert "confusion example: 'write the docs page'" in out.message


def test_partial_verdicts_note_unjudged_pairs():
    a = contributor("alpha", "Use for alpha docs.")
    b = contributor("beta", "Use for beta docs.")
    c = contributor("gamma", "Use for gamma docs.")
    world = FakeWorld([a, b, c])
    f = finding_for("description-overlap", [a, b, c])
    cache = {deep.pair_key(a, b): _verdict("distinct")}
    (out,) = deep.apply_verdicts(world, [f], cache, set())
    assert out.severity == "warning"
    assert "deep: 2 of 3 pairs unjudged" in out.message


def test_active_injection_blocks_downgrade_and_ack_unblocks():
    a, b, world = _pair_world()
    overlap = finding_for("description-overlap", [a, b])
    injection = finding_for("injection-egress", [a])
    cache = {deep.pair_key(a, b): _verdict("distinct")}
    (out, _) = deep.apply_verdicts(world, [overlap, injection], cache, set())
    assert out.severity == "warning"
    assert "downgrade withheld" in out.message
    assert "alpha" in out.message
    (out2, _) = deep.apply_verdicts(
        world, [overlap, injection], cache, {injection.fingerprint}
    )
    assert out2.severity == "note"


def test_judge_pairs_respects_budget_and_writes_cache(tmp_path):
    a = contributor("alpha", "Use for alpha docs.")
    b = contributor("beta", "Use for beta docs.")
    c = contributor("gamma", "Use for gamma docs.")
    world = FakeWorld([a, b, c])
    findings = [finding_for("description-overlap", [a, b, c])]
    calls = []

    def judge(x, y):
        calls.append((x.name, y.name))
        return deep.JudgeResult(verdict="distinct", rationale="r", detail="d")

    cache = {}
    cdir = tmp_path / "cache"
    judged, remaining = deep.judge_pairs(world, findings, cache, cdir, judge, "m", max_calls=2)
    assert judged == 2 and remaining == 1
    assert calls == [("alpha", "beta"), ("alpha", "gamma")]
    assert len(deep.load_cache(cdir)) == 2
    assert all(v.model == "m" for v in cache.values())
    # a second run continues where the first stopped
    judged2, remaining2 = deep.judge_pairs(world, findings, cache, cdir, judge, "m", max_calls=2)
    assert judged2 == 1 and remaining2 == 0
    assert calls[-1] == ("beta", "gamma")


def test_judge_pairs_failed_call_not_cached(tmp_path):
    a, b, world = _pair_world()
    findings = [finding_for("description-overlap", [a, b])]
    cache = {}
    judged, remaining = deep.judge_pairs(
        world, findings, cache, tmp_path / "c", lambda x, y: None, "m", max_calls=5
    )
    assert judged == 0 and remaining == 1
    assert cache == {} and deep.load_cache(tmp_path / "c") == {}


from drskill.ledger import Config
from drskill.pipeline import run_scan

PILE_A = "Use when the user asks to write project documentation pages."
PILE_B = "Use when the user asks to write project documentation summaries."


def write_skill(proj, name, description, body):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )


def test_run_scan_judges_and_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = tmp_path / "p", tmp_path / "home"
    write_skill(proj, "doc-a", PILE_A, body="a" * 40)
    write_skill(proj, "doc-b", PILE_B, body="b" * 40)

    def judge(x, y):
        return deep.JudgeResult(verdict="distinct", rationale="r", detail="d")

    world, findings = run_scan(proj, home, config=Config(), judge=judge)
    overlap = [f for f in findings if f.check_id == "description-overlap"]
    assert [f.severity for f in overlap] == ["note"]
    # the verdict persisted: a later plain scan applies it with no judge
    world2, findings2 = run_scan(proj, home, config=Config())
    overlap2 = [f for f in findings2 if f.check_id == "description-overlap"]
    assert [f.severity for f in overlap2] == ["note"]


import builtins

import pytest

from drskill import deep_llm


def test_build_judge_without_dspy_raises(monkeypatch):
    real_import = builtins.__import__

    def no_dspy(name, *args, **kwargs):
        if name == "dspy":
            raise ImportError("No module named 'dspy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_dspy)
    with pytest.raises(deep_llm.DeepUnavailableError, match="uv tool install drskill"):
        deep_llm.build_judge("anthropic/claude-haiku-4-5")


def test_pair_key_immune_to_newline_in_names():
    # (name="a", routing="b\nX") must not collide with (name="a\nb", routing="X")
    z = contributor("zeta", "Use for zeta docs.")
    crafted1 = contributor("a", "b\nX", cid="/skills/crafted1/SKILL.md")
    crafted2 = contributor("a\nb", "X", cid="/skills/crafted2/SKILL.md")
    assert deep.pair_key(z, crafted1) != deep.pair_key(z, crafted2)


def test_run_scan_default_config_honors_machine_acks(tmp_path, monkeypatch):
    """run_scan's config=None fallback must merge the machine ledger, so a
    machine-level ack of an injection finding unblocks the note downgrade."""
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = tmp_path / "p", tmp_path / "home"
    home.mkdir()
    write_skill(proj, "doc-a", PILE_A, body="a" * 40)
    # doc-b carries an injection surface: an egress call in a bundled script
    d = proj / ".claude" / "skills" / "doc-b"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: doc-b\ndescription: {PILE_B}\n---\n{'b' * 40}\n")
    (d / "scripts").mkdir()
    (d / "scripts" / "send.sh").write_text("#!/bin/sh\ncurl https://example.com\n")

    world, findings = run_scan(proj, home, config=Config())
    injections = [f for f in findings if f.check_id.startswith("injection-")]
    assert injections, "fixture must produce an injection finding"
    (pair,) = deep.flagged_pairs(world, findings)
    deep.save_verdict(
        deep.cache_dir(proj, home, False), deep.pair_key(*pair),
        deep.Verdict(verdict="distinct", rationale="r", detail="d",
                     model="m", program_version="v", date="2026-07-21"),
    )
    # unacked injection: downgrade withheld
    _, f1 = run_scan(proj, home)
    (o1,) = [f for f in f1 if f.check_id == "description-overlap"]
    assert o1.severity == "warning" and "downgrade withheld" in o1.message
    # ack the injection finding in the MACHINE ledger; default config must see it
    (home / ".drskill.toml").write_text(
        "[[ack]]\ncheck = \"" + injections[0].check_id + "\"\n"
        "skills = [\"doc-b\"]\nfingerprint = \"" + injections[0].fingerprint + "\"\n"
    )
    _, f2 = run_scan(proj, home)
    (o2,) = [f for f in f2 if f.check_id == "description-overlap"]
    assert o2.severity == "note"


def test_deep_budget_skips_acked_clusters(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = tmp_path / "p", tmp_path / "home"
    write_skill(proj, "doc-a", PILE_A, body="a" * 40)
    write_skill(proj, "doc-b", PILE_B, body="b" * 40)
    _, findings = run_scan(proj, home)
    (overlap,) = [f for f in findings if f.check_id == "description-overlap"]
    from drskill.ledger import Ack
    cfg = Config(ack=[Ack(check="description-overlap", skills=["doc-a", "doc-b"],
                          fingerprint=overlap.fingerprint)])
    calls = []

    def judge(x, y):
        calls.append((x.name, y.name))
        return deep.JudgeResult(verdict="distinct", rationale="r", detail="d")

    run_scan(proj, home, config=cfg, judge=judge)
    assert calls == []  # the acked cluster's pairs never spend budget


def test_judge_pairs_aborts_after_three_consecutive_failures(tmp_path):
    members = [contributor(n, f"Use for {n} docs.") for n in ("a", "b", "c", "d")]
    world = FakeWorld(members)
    findings = [finding_for("description-overlap", members)]  # 6 pairs
    calls = []

    def judge(x, y):
        calls.append((x.name, y.name))
        return None

    judged, remaining = deep.judge_pairs(
        world, findings, {}, tmp_path / "c", judge, "m", max_calls=10
    )
    assert judged == 0
    assert len(calls) == 3  # persistent failure stops burning the budget
    assert remaining == 6


def test_build_judge_allows_ambient_auth_providers(monkeypatch):
    """bedrock/vertex/azure authenticate via profiles or ADC, not env keys;
    the env-var gate must not block them. Call failures surface at judge time."""
    import litellm

    monkeypatch.setattr(
        litellm, "validate_environment",
        lambda model: {"keys_in_environment": False, "missing_keys": ["AWS_ACCESS_KEY_ID"]},
    )
    judge = deep_llm.build_judge("bedrock/anthropic.claude-sonnet-5")
    assert callable(judge)


def test_load_user_env_parses_and_never_overrides(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".drskill").mkdir(parents=True)
    (home / ".drskill" / "env").write_text(
        "# deep tier keys\n"
        "\n"
        "ANTHROPIC_API_KEY=sk-ant-from-file\n"
        "export OPENAI_API_KEY='sk-openai-quoted'\n"
        "ALREADY_SET=from-file\n"
        "not a valid line\n"
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-shell")
    loaded = deep.load_user_env(home)
    assert sorted(loaded) == ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-file"
    assert os.environ["OPENAI_API_KEY"] == "sk-openai-quoted"
    assert os.environ["ALREADY_SET"] == "from-shell"  # the shell always wins


def test_load_user_env_missing_file_is_noop(tmp_path):
    assert deep.load_user_env(tmp_path / "nohome") == []


def test_build_judge_missing_key_message_names_env_file_and_console(monkeypatch):
    import litellm

    monkeypatch.setattr(
        litellm, "validate_environment",
        lambda model: {"keys_in_environment": False, "missing_keys": ["ANTHROPIC_API_KEY"]},
    )
    with pytest.raises(deep_llm.DeepUnavailableError) as e:
        deep_llm.build_judge("anthropic/claude-haiku-4-5")
    msg = str(e.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "~/.drskill/env" in msg
    assert "console.anthropic.com" in msg


def test_judge_pairs_unlimited_budget(tmp_path):
    members = [contributor(n, f"Use for {n} docs.") for n in ("a", "b", "c", "d")]
    world = FakeWorld(members)
    findings = [finding_for("description-overlap", members)]  # 6 pairs

    def judge(x, y):
        return deep.JudgeResult(verdict="distinct", rationale="r", detail="d")

    judged, remaining = deep.judge_pairs(
        world, findings, {}, tmp_path / "c", judge, "m", max_calls=None
    )
    assert judged == 6 and remaining == 0


def test_cache_entry_without_rewrite_fields_loads(tmp_path):
    """A 0.3.0-shaped entry (no rewrite fields) must load unchanged."""
    cdir = tmp_path / "cache"
    cdir.mkdir()
    (cdir / ("aa" * 32 + ".json")).write_text(
        '{"verdict": "description_collision", "rationale": "r", "detail": "d",'
        ' "model": "m", "program_version": "0.3.0", "date": "2026-07-21"}'
    )
    (entry,) = deep.load_cache(cdir).values()
    assert entry.rewrite_text is None
    assert entry.rewrite_target is None
    assert entry.rewrite_reason is None


def test_rewrite_result_shape():
    r = deep.RewriteResult(target="idea-vault", text="Use when ...", reason="vaguer")
    assert (r.target, r.text, r.reason) == ("idea-vault", "Use when ...", "vaguer")


def _rewriter(calls=None):
    def rewrite(a, b, confusion):
        if calls is not None:
            calls.append((a.name, b.name, confusion))
        return deep.RewriteResult(target=a.name, text="Use when only alpha.", reason="vaguer")
    return rewrite


def test_collision_verdict_triggers_immediate_rewrite(tmp_path):
    a, b, world = _pair_world()
    findings = [finding_for("description-overlap", [a, b])]
    rcalls = []

    def judge(x, y):
        return deep.JudgeResult(
            verdict="description_collision", rationale="blur", detail="write the docs"
        )

    cache = {}
    judged, remaining = deep.judge_pairs(
        world, findings, cache, tmp_path / "c", judge, "m",
        max_calls=None, rewriter=_rewriter(rcalls),
    )
    assert judged == 1 and remaining == 0
    assert rcalls == [("alpha", "beta", "write the docs")]
    (entry,) = cache.values()
    assert entry.rewrite_target == "alpha"
    assert entry.rewrite_text == "Use when only alpha."
    (disk_entry,) = deep.load_cache(tmp_path / "c").values()
    assert disk_entry.rewrite_text == "Use when only alpha."


def test_rewrite_call_shares_the_budget(tmp_path):
    a, b, world = _pair_world()
    findings = [finding_for("description-overlap", [a, b])]

    def judge(x, y):
        return deep.JudgeResult(verdict="description_collision", rationale="r", detail="d")

    cache = {}
    judged, remaining = deep.judge_pairs(
        world, findings, cache, tmp_path / "c", judge, "m",
        max_calls=1, rewriter=_rewriter(),
    )
    # budget of 1 pays for the verdict; the rewrite must wait
    assert judged == 1 and remaining == 0
    (entry,) = cache.values()
    assert entry.verdict == "description_collision" and entry.rewrite_text is None


def test_missing_rewrite_retried_before_new_pairs(tmp_path):
    a, b, _ = _pair_world()
    c = contributor("gamma", "Use for gamma docs.")
    world = FakeWorld([a, b, c])
    findings = [finding_for("description-overlap", [a, b, c])]
    key_ab = deep.pair_key(a, b)
    cache = {key_ab: _verdict("description_collision", detail="which docs?")}
    deep.save_verdict(tmp_path / "c", key_ab, cache[key_ab])
    order = []

    def judge(x, y):
        order.append(("judge", x.name, y.name))
        return deep.JudgeResult(verdict="distinct", rationale="r", detail="d")

    def rewrite(x, y, confusion):
        order.append(("rewrite", x.name, y.name))
        return deep.RewriteResult(target=x.name, text="new text", reason="why")

    deep.judge_pairs(
        world, findings, cache, tmp_path / "c", judge, "m",
        max_calls=None, rewriter=rewrite,
    )
    assert order[0] == ("rewrite", "alpha", "beta")  # retry pass runs first
    assert cache[key_ab].rewrite_text == "new text"
    assert deep.load_cache(tmp_path / "c")[key_ab].rewrite_text == "new text"


def test_failed_rewrite_caches_verdict_alone(tmp_path):
    a, b, world = _pair_world()
    findings = [finding_for("description-overlap", [a, b])]

    def judge(x, y):
        return deep.JudgeResult(verdict="description_collision", rationale="r", detail="d")

    cache = {}
    deep.judge_pairs(
        world, findings, cache, tmp_path / "c", judge, "m",
        max_calls=None, rewriter=lambda x, y, q: None,
    )
    (entry,) = cache.values()
    assert entry.verdict == "description_collision" and entry.rewrite_text is None


def test_shared_failure_abort_covers_rewrites(tmp_path):
    members = [contributor(n, f"Use for {n} docs.") for n in ("a", "b", "c", "d")]
    world = FakeWorld(members)
    findings = [finding_for("description-overlap", members)]  # 6 pairs
    attempts = []

    def judge(x, y):
        attempts.append("judge")
        return deep.JudgeResult(verdict="description_collision", rationale="r", detail="d")

    def rewrite(x, y, q):
        attempts.append("rewrite")
        return None  # every rewrite fails

    deep.judge_pairs(
        world, findings, {}, tmp_path / "c", judge, "m",
        max_calls=None, rewriter=rewrite,
    )
    # each pair: judge succeeds (resets counter), rewrite fails. Three
    # consecutive failures never accumulate, so all six pairs are judged.
    assert attempts.count("judge") == 6


def test_rewrite_renders_as_diff_with_fix_line():
    a, b, world = _pair_world()
    f = finding_for("description-overlap", [a, b])
    v = _verdict("description_collision", rationale="blur", detail="q").model_copy(update={
        "rewrite_target": "alpha",
        "rewrite_text": "Use when only alpha applies.",
        "rewrite_reason": "alpha is vaguer",
    })
    cache = {deep.pair_key(a, b): v}
    (out,) = deep.apply_verdicts(world, [f], cache, set())
    assert "deep: rewrite for alpha (alpha is vaguer):" in out.message
    assert "\n      - Use when writing documentation pages." in out.message
    assert "\n      + Use when only alpha applies." in out.message
    assert any("edit /skills/alpha/SKILL.md" in c for c in out.fix_commands)


def test_collision_without_rewrite_renders_no_diff():
    a, b, world = _pair_world()
    f = finding_for("description-overlap", [a, b])
    cache = {deep.pair_key(a, b): _verdict("description_collision")}
    (out,) = deep.apply_verdicts(world, [f], cache, set())
    assert "rewrite for" not in out.message
    assert out.fix_commands == f.fix_commands


def test_build_rewriter_without_dspy_raises(monkeypatch):
    real_import = builtins.__import__

    def no_dspy(name, *args, **kwargs):
        if name == "dspy":
            raise ImportError("No module named 'dspy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_dspy)
    with pytest.raises(deep_llm.DeepUnavailableError, match="uv tool install drskill"):
        deep_llm.build_rewriter("anthropic/claude-haiku-4-5")
