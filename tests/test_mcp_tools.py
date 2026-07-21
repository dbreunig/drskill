from drskill.checks import run_all
from drskill.ledger import Config
from drskill.models import Contributor, Deployment, TokenCost
from drskill.resolution import World
from drskill.harnesses import HarnessDef


def skill(name, desc, cid, harness="claude-code", body="body text here"):
    return Contributor(
        id=cid, name=name, scope="project", routing_text=desc, body=body,
        token_cost=TokenCost(catalog_tokens=10, body_tokens=5), content_hash=cid,
        deployments=[Deployment(harness=harness, path=cid, scope="project",
                                via_symlink=False, order=0)],
    )


def tool(name, desc, cid, harness="claude-code"):
    return Contributor(
        id=cid, name=name, kind="mcp_tool", scope="user", routing_text=desc,
        token_cost=TokenCost(catalog_tokens=8, body_tokens=0), content_hash=cid,
        deployments=[Deployment(harness=harness, path=cid, scope="user",
                                via_symlink=False, order=0)],
    )


def world_of(*contribs):
    return World(
        contributors={c.id: c for c in contribs},
        harnesses={"claude-code": HarnessDef(
            id="claude-code", display_name="Claude Code",
            paths_verified=True, precedence_verified=True)},
    )


SKILL_CHECKS = {
    "spec-name-mismatch", "spec-missing-description", "spec-description-too-long",
    "spec-invalid-frontmatter", "missing-activation", "generic-description",
    "opposing-imperatives", "budget-body-tokens", "exact-duplicate",
    "near-duplicate", "frontmatter-angle-brackets",
}


def test_skill_checks_ignore_tools():
    # a tool whose text would trip skill checks if it were treated as a skill
    t = tool("vague", "Helps.", "hash1:vague")  # generic + missing-activation bait
    findings = run_all(world_of(t), Config())
    assert not any(f.check_id in SKILL_CHECKS for f in findings)


def test_two_identical_tools_are_not_exact_duplicate():
    a = tool("search", "Search the web.", "h1:search")
    b = tool("search", "Search the web.", "h2:search")
    findings = run_all(world_of(a, b), Config())
    assert not any(f.check_id == "exact-duplicate" for f in findings)


def test_description_overlap_sees_tool_vs_skill():
    s = skill("web-search", "Use when the user wants to search the web for pages.",
              "/skills/web-search/SKILL.md")
    t = tool("search", "Use when the user wants to search the web for pages.",
             "h1:search")
    findings = run_all(world_of(s, t), Config())
    overlaps = [f for f in findings if f.check_id == "description-overlap"]
    assert overlaps and {"web-search", "search"} <= set(overlaps[0].contributor_names)
