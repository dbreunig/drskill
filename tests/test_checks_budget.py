from drskill.checks import run_all
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world


def world_from(proj, home):
    h = next(x for x in load_harnesses() if x.id == "claude-code")
    i, b = discover(h, proj, home)
    return build_world(i, {h.id: h}, b)


def write(proj, name, description, body):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")


def test_budgets(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "wordy", "long description " * 30, "long body " * 200)
    write(proj, "terse", "short", "short")
    cfg = Config()
    cfg.budget.catalog_tokens_max = 20
    cfg.budget.body_tokens_warn = 50
    findings = run_all(world_from(proj, home), cfg)
    catalog = [f for f in findings if f.check_id == "budget-catalog-tokens"]
    body = [f for f in findings if f.check_id == "budget-body-tokens"]
    assert len(catalog) == 1 and catalog[0].harnesses == ["claude-code"]
    assert "~" in catalog[0].message  # approximate marker
    assert [f.contributor_names for f in body] == [["wordy"]]


def test_quiet_under_budget(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "terse", "short", "short")
    findings = run_all(world_from(proj, home), Config())
    assert [f for f in findings if f.check_id.startswith("budget-")] == []
