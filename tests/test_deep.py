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
        model="anthropic/claude-sonnet-5", program_version="0.2.0", date="2026-07-21",
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
    with pytest.raises(deep_llm.DeepUnavailableError, match=r"drskill\[deep\]"):
        deep_llm.build_judge("anthropic/claude-sonnet-5")


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
