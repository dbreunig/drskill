"""Configured routing expectations, checked lexically on every scan.
Never calls a model: scans must stay offline; drskill explain --deep is
where the model-judged version of the same question lives."""

from __future__ import annotations

from drskill import explain
from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World


@check("query-routing")
def query_routing(world: World, config: Config) -> list[Finding]:
    out: list[Finding] = []
    for q in config.queries:
        rankings = explain.rank(world, q.query, margin=config.thresholds.routing_margin)
        for harness_ids, r in explain.group_rankings(rankings):
            problem = None
            if r.verdict == "contested":
                a, b = r.rows[0].contributor.name, r.rows[1].contributor.name
                problem = f'Query "{q.query}" is contested between {a} and {b}'
            elif q.expect and r.verdict == "routes" and r.top_name != q.expect:
                problem = f'Query "{q.query}" routes to {r.top_name}, expected {q.expect}'
            elif q.expect and r.verdict == "none":
                problem = f'Query "{q.query}" matches nothing, expected {q.expect}'
            if problem is None:
                continue
            contributors = [row.contributor for row in r.rows]
            # The ack must die when the expectation changes, not only when
            # routing text does: editing `expect` on an otherwise-unchanged
            # query would otherwise stay silently acked forever.
            out.append(make_finding(
                "query-routing", "warning", contributors, problem,
                harnesses=harness_ids, extra_key=f"{q.query}|{q.expect or ''}",
                fingerprint_texts=[q.query, *[
                    f"{row.contributor.name}: {row.contributor.routing_text}"
                    for row in r.rows]],
            ))
    return out
