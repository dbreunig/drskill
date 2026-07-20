import json
import shlex

from rich.console import Console

from drskill.harnesses import HarnessDef
from drskill.models import Finding, TokenCost
from drskill.report import render, to_json
from drskill.resolution import World
from tests.test_models import make_contributor

PAYLOAD = "'; echo pwned; '"


def sample_finding(check="double-load", severity="error", harnesses=("claude-code",)):
    return Finding(
        check_id=check, severity=severity,
        contributors=["/a"], contributor_names=["pdf-tools"],
        harnesses=list(harnesses), message=f"{check} happened",
        fix_commands=["npx skills remove pdf-tools"],
        fingerprint="sha256:f",
    )


def world_with(verified=True):
    return World(
        contributors={"/a": make_contributor(id="/a", name="pdf-tools")},
        harnesses={
            "claude-code": HarnessDef(
                id="claude-code", display_name="Claude Code", verified=verified
            )
        },
    )


def render_to_text(world, active, acked):
    console = Console(record=True, width=100, force_terminal=False)
    render(world, active, acked, console)
    return console.export_text()


def test_render_sections_and_ack_line():
    err = sample_finding()
    warn = sample_finding(check="near-duplicate", severity="warning")
    text = render_to_text(world_with(), [err, warn], [sample_finding(check="name-shadow", severity="warning")])
    assert "ERRORS" in text and "WARNINGS" in text
    assert "drskill ack double-load pdf-tools" in text
    assert "npx skills remove pdf-tools" in text
    assert "1 error" in text and "2 warnings" not in text  # 1 active warning
    assert "1 acknowledged" in text
    assert "token counts are approximate" in text


def test_best_effort_label_for_unverified():
    text = render_to_text(world_with(verified=False), [sample_finding()], [])
    assert "best effort" in text


def test_clean_report():
    text = render_to_text(world_with(), [], [])
    assert "No findings" in text


def test_to_json_stable():
    data = json.loads(to_json([sample_finding()]))
    assert data[0]["check_id"] == "double-load"
    assert data[0]["fingerprint"] == "sha256:f"
    assert list(data[0].keys()) == sorted(data[0].keys())


def test_ack_hint_quotes_adversarial_contributor_name():
    f = Finding(
        check_id="near-duplicate", severity="warning",
        contributors=["/a"], contributor_names=[PAYLOAD],
        harnesses=["claude-code"], message="adversarial name",
        fingerprint="sha256:f",
    )
    text = render_to_text(world_with(), [f], [])
    line = next(l for l in text.splitlines() if "drskill ack" in l)
    tokens = shlex.split(line.strip())
    assert tokens[-1] == PAYLOAD


def test_render_escapes_rich_markup_in_dynamic_text():
    f = Finding(
        check_id="spec-name-mismatch", severity="error",
        contributors=["/a"], contributor_names=["[red]sneaky[/red]"],
        harnesses=["claude-code"], message="name '[/weird]' does not match folder",
        fix_commands=["rename '[bold]x[/bold]'"],
        fingerprint="sha256:f",
    )
    text = render_to_text(world_with(), [f], [])
    assert "[/weird]" in text
    assert "[red]sneaky[/red]" in text
    assert "[bold]x[/bold]" in text
