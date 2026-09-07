from drskill.checks import query_routing  # noqa: F401  (registers the check)
from drskill.harnesses import HarnessDef
from drskill.ledger import Config, Query
from drskill.models import Deployment
from drskill.resolution import World
from tests.test_models import make_contributor


def world_with(*specs, harnesses=("h1",)):
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


def run_check(check_id, world, config=None):
    from drskill.checks import REGISTRY

    return REGISTRY[check_id](world, config or Config())


def test_contested_query_warns():
    w = world_with(("pdf-a", "summarize pdf documents"),
                   ("pdf-b", "summarize pdf documents"))
    cfg = Config(queries=[Query(query="summarize pdf documents")])
    findings = run_check("query-routing", w, cfg)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "contested" in findings[0].message


def test_expect_miss_warns():
    w = world_with(("pdf", "summarize pdf documents"),
                   ("notes", "take meeting notes"))
    cfg = Config(queries=[Query(query="summarize pdf documents", expect="notes")])
    findings = run_check("query-routing", w, cfg)
    assert len(findings) == 1
    assert "expected notes" in findings[0].message


def test_clean_query_is_silent():
    w = world_with(("pdf", "summarize pdf documents"),
                   ("deploy", "deploy kubernetes clusters"))
    cfg = Config(queries=[Query(query="summarize pdf documents", expect="pdf")])
    assert run_check("query-routing", w, cfg) == []


def test_no_queries_is_silent():
    w = world_with(("pdf", "summarize pdf documents"))
    assert run_check("query-routing", w, Config()) == []


def test_fingerprint_changes_when_expect_changes():
    # Editing `expect` alone (routing text untouched) must re-fire an ack.
    w = world_with(("pdf", "summarize pdf documents"),
                   ("notes", "take meeting notes"))
    cfg1 = Config(queries=[Query(query="summarize pdf documents", expect="notes")])
    cfg2 = Config(queries=[Query(query="summarize pdf documents", expect="something-else")])
    findings1 = run_check("query-routing", w, cfg1)
    findings2 = run_check("query-routing", w, cfg2)
    assert len(findings1) == 1 and len(findings2) == 1
    assert findings1[0].fingerprint != findings2[0].fingerprint
