from __future__ import annotations

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World


@check("budget-catalog-tokens")
def budget_catalog_tokens(world: World, config: Config) -> list[Finding]:
    out = []
    for hid, hdef in world.harnesses.items():
        # Only skills count toward the startup catalog budget; commands are
        # explicitly invoked and never join it (see docstring in
        # harnesses.py), so they must not be in the fingerprint either —
        # otherwise editing a command file would churn this finding.
        contributors = [c for c in world.effective(hid) if c.kind == "skill"]
        total = sum(c.token_cost.catalog_tokens for c in contributors)
        if total > config.budget.catalog_tokens_max:
            out.append(
                make_finding(
                    "budget-catalog-tokens", "warning", contributors,
                    f"{hdef.display_name} startup catalog is ~{total} tokens "
                    f"(budget {config.budget.catalog_tokens_max})",
                    harnesses=[hid],
                    extra_key=hid,
                    fix_commands=[f"drskill list --tokens --harness {hid}"],
                )
            )
    return out


@check("budget-body-tokens")
def budget_body_tokens(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "budget-body-tokens", "warning", [c],
            f"'{c.name}' body is ~{c.token_cost.body_tokens} tokens "
            f"(warn ceiling {config.budget.body_tokens_warn})",
            fix_commands=[f"Split or trim {c.id}; move reference material into bundled files"],
        )
        for c in world.contributors.values()
        if c.kind == "skill" and c.token_cost.body_tokens > config.budget.body_tokens_warn
    ]
