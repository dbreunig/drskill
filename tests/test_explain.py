from drskill import explain
from drskill.harnesses import HarnessDef
from drskill.models import Deployment
from drskill.resolution import World
from tests.test_models import make_contributor


def _world(*specs, harnesses=("h1",)):
    # specs: (name, routing_text) or (name, routing_text, kind)
    contributors = {}
    for i, spec in enumerate(specs):
        name, routing_text, *rest = spec
        kind = rest[0] if rest else "skill"
        c = make_contributor(id=f"/x/{name}", name=name, kind=kind,
                             routing_text=routing_text)
        for h in harnesses:
            c.deployments.append(Deployment(harness=h, path=f"/x/{name}",
                                            scope="project", via_symlink=False,
                                            order=i))
        contributors[c.id] = c
    return World(contributors=contributors,
                 harnesses={h: HarnessDef(id=h, display_name=h) for h in harnesses})


def test_rank_orders_and_floors():
    w = _world(("pdf", "summarize pdf documents"),
               ("deploy", "deploy kubernetes clusters"))
    [r] = explain.rank(w, "summarize a pdf", margin=0.1)
    assert r.verdict == "routes"
    assert r.top_name == "pdf"
    assert [row.contributor.name for row in r.rows] == ["pdf"]


def test_rank_contested_when_gap_is_small():
    w = _world(("pdf-a", "summarize pdf documents"),
               ("pdf-b", "summarize pdf documents"))
    [r] = explain.rank(w, "summarize pdf documents", margin=0.1)
    assert r.verdict == "contested"


def test_rank_none_when_nothing_matches():
    w = _world(("deploy", "deploy kubernetes clusters"))
    [r] = explain.rank(w, "translate portuguese poetry", margin=0.1)
    assert r.verdict == "none"
    assert r.rows == []


def test_rank_skips_commands():
    w = _world(("pdf", "summarize pdf documents"),
               ("pdf-cmd", "summarize pdf documents", "command"))
    [r] = explain.rank(w, "summarize pdf", margin=0.1)
    assert [row.contributor.name for row in r.rows] == ["pdf"]


def test_rank_single_harness_filter():
    w = _world(("pdf", "summarize pdf documents"), harnesses=("h1", "h2"))
    rankings = explain.rank(w, "summarize pdf", margin=0.1, harness="h2")
    assert [r.harness for r in rankings] == ["h2"]


def test_group_rankings_collapses_identical():
    w = _world(("pdf", "summarize pdf documents"), harnesses=("h1", "h2"))
    grouped = explain.group_rankings(explain.rank(w, "summarize pdf", margin=0.1))
    assert len(grouped) == 1
    ids, rep = grouped[0]
    assert ids == ["h1", "h2"]
    assert rep.top_name == "pdf"
