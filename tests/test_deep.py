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
