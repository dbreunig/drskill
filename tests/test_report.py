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
        fingerprint="sha256:f0f1f2f3f4",
    )


def world_with(verified=True):
    # verified toggles both facets; facet-specific worlds are built inline
    return World(
        contributors={"/a": make_contributor(id="/a", name="pdf-tools")},
        harnesses={
            "claude-code": HarnessDef(
                id="claude-code", display_name="Claude Code",
                paths_verified=verified, precedence_verified=verified
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
    # header leads with the id; with no seen state everything is tagged new
    assert "[f0f1] new double-load:" in text
    assert "drskill ack f0f1" in text  # recap example line
    recap = text[text.index("ack findings by id"):]
    assert "f0f1 new double-load" in recap and "pdf-tools" in recap
    assert "npx skills remove pdf-tools" in text
    assert "1 error" in text and "2 warnings" not in text  # 1 active warning
    assert "1 acknowledged" in text
    assert "token counts are approximate" in text


def test_unverified_paths_marks_harness_and_legend():
    text = render_to_text(world_with(verified=False), [sample_finding()], [])
    assert "claude-code?" in text
    assert "has not verified" in text


def test_verified_findings_carry_no_marker_or_legend():
    text = render_to_text(world_with(), [sample_finding()], [])
    assert "claude-code?" not in text
    assert "has not verified" not in text


def test_precedence_marker_only_on_precedence_checks():
    world = world_with()
    world.harnesses["claude-code"] = world.harnesses["claude-code"].model_copy(
        update={"precedence_verified": False}
    )
    shadow = sample_finding(check="name-shadow", severity="warning")
    diverged = sample_finding(check="diverged-copies", severity="warning")
    text = render_to_text(world, [shadow, diverged], [])
    lines = text.splitlines()
    shadow_idx = next(i for i, ln in enumerate(lines) if "name-shadow" in ln)
    diverged_idx = next(i for i, ln in enumerate(lines) if "diverged-copies" in ln)
    assert "claude-code?" in lines[shadow_idx + 1]
    assert "claude-code?" not in lines[diverged_idx + 1]


def test_clean_report():
    text = render_to_text(world_with(), [], [])
    assert "No findings" in text


def test_to_json_stable():
    data = json.loads(to_json([sample_finding()]))
    assert data[0]["check_id"] == "double-load"
    assert data[0]["fingerprint"] == "sha256:f0f1f2f3f4"
    assert list(data[0].keys()) == sorted(data[0].keys())


def test_ack_hint_for_contributorless_finding_has_no_trailing_names():
    f = Finding(
        check_id="lockfile-drift", severity="warning",
        contributors=[], contributor_names=[],
        harnesses=["claude-code"], message="'ghost' is in skills-lock.json but not found",
        fingerprint="sha256:g",
    )
    text = render_to_text(world_with(), [f], [])
    line = next(l for l in text.splitlines() if "drskill ack" in l)
    recap = text[text.index("ack findings by id"):]
    line = next(ln for ln in recap.splitlines() if "lockfile-drift" in ln)
    assert line.strip().endswith("lockfile-drift")  # no trailing names


def test_suggested_ack_command_is_ids_only_even_with_adversarial_name():
    import re

    f = Finding(
        check_id="near-duplicate", severity="warning",
        contributors=["/a"], contributor_names=[PAYLOAD],
        harnesses=["claude-code"], message="adversarial name",
        fingerprint="sha256:abcd1234",
    )
    text = render_to_text(world_with(), [f], [])
    # the only copy-pasteable command in the report is the recap example,
    # and it must contain nothing but hex ids (shell-safe by construction)
    line = next(l for l in text.splitlines() if "drskill ack" in l)
    cmd = re.search(r"drskill ack ([0-9a-f ]+)`", line).group(1)
    assert cmd.split() == ["abcd"]
    assert PAYLOAD in text  # the name still renders, as display text only


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


def world_two_harnesses():
    from drskill.models import Deployment

    c = make_contributor(id="/a", name="alpha")
    c.deployments.append(
        Deployment(
            harness="claude-code", path="/a", scope="project",
            via_symlink=False, order=0,
        )
    )
    return World(
        contributors={"/a": c},
        harnesses={
            "claude-code": HarnessDef(
                id="claude-code", display_name="Claude Code",
                paths_verified=True, precedence_verified=True
            ),
            "qwen-code": HarnessDef(
                id="qwen-code", display_name="Qwen Code"
            ),
        },
    )


def tables_to_text(world, **kwargs):
    from drskill.report import render_harness_tables

    console = Console(record=True, width=120, force_terminal=False)
    render_harness_tables(world, console, **kwargs)
    return console.export_text()


def test_empty_harness_hidden_by_default():
    text = tables_to_text(world_two_harnesses())
    assert "Claude Code" in text and "alpha" in text
    assert "Qwen Code" not in text
    assert "1 more harness detected with no skills (qwen-code); show with --all" in text


def test_show_all_includes_empty():
    text = tables_to_text(world_two_harnesses(), show_all=True)
    assert "Qwen Code" in text
    assert "show with --all" not in text


def test_harness_filter_suppresses_closing_line():
    text = tables_to_text(world_two_harnesses(), harness="claude-code")
    assert "Claude Code" in text and "show with --all" not in text


def test_header_splits_empty_harness_count():
    text = render_to_text(world_two_harnesses(), [], [])
    assert "1 harness (1 more empty), 1 skills" in text


def test_header_plain_when_no_empty():
    world = world_two_harnesses()
    del world.harnesses["qwen-code"]
    text = render_to_text(world, [], [])
    assert "more empty" not in text
    assert "1 harness, 1 skills" in text


def test_render_reports_unscanned_bundled_files(tmp_path):
    from rich.console import Console

    from drskill.discovery import discover
    from drskill.harnesses import HarnessDef
    from drskill.resolution import build_world
    from drskill.report import render

    d = tmp_path / ".claude" / "skills" / "assets"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: assets\ndescription: Use when testing.\n---\nBody.\n"
    )
    (d / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    h = HarnessDef(
        id="t3", display_name="T3",
        paths_verified=True, precedence_verified=True,
        project_paths=[".claude/skills"], recursive=True,
    )
    instances, broken = discover(h, tmp_path, tmp_path / "no-home")
    world = build_world(instances, {"t3": h}, broken)
    console = Console(record=True, width=120)
    render(world, [], [], console)
    text = console.export_text()
    assert "1 bundled file not content scanned (1 binary) across 1 skill" in text


def test_render_sanitizes_bidi_in_messages():
    from rich.console import Console

    from drskill.models import Finding
    from drskill.report import render
    from drskill.resolution import World

    f = Finding(
        check_id="injection-override",
        severity="warning",
        contributors=["x"],
        contributor_names=["evil\u202ename"],
        harnesses=[],
        message="header \u202e hidden",
        fix_commands=["rm 'evil\u202e'"],
        fingerprint="sha256:abcd1234",
    )
    console = Console(record=True, width=200)
    render(World(), [f], [], console)
    text = console.export_text()
    assert "\u202e" not in text
    assert text.count("\\u202e") >= 3  # message, fix command, recap names


def _mini_world(tmp_path, harness_defs=None, system_vendor=True):
    from drskill.discovery import discover
    from drskill.harnesses import HarnessDef
    from drskill.resolution import build_world

    base = tmp_path / ".claude" / "skills"
    (base / "mine").mkdir(parents=True)
    (base / "mine" / "SKILL.md").write_text(
        "---\nname: mine\ndescription: Use when testing.\n---\nBody.\n"
    )
    vdir = base / (".system" if system_vendor else "other") / "vendor"
    vdir.mkdir(parents=True)
    (vdir / "SKILL.md").write_text(
        "---\nname: vendor\ndescription: Use when vending.\n---\nBody.\n"
    )
    if harness_defs is None:
        harness_defs = [
            HarnessDef(
                id="t3", display_name="T3",
                paths_verified=True, precedence_verified=True,
                project_paths=[".claude/skills"], recursive=True,
            )
        ]
    instances, broken = [], []
    for h in harness_defs:
        i, b = discover(h, tmp_path, tmp_path / "no-home")
        instances += i
        broken += b
    return build_world(instances, {h.id: h for h in harness_defs}, broken)


def _finding(check, names, fingerprint, contributors, harnesses):
    from drskill.models import Finding

    return Finding(
        check_id=check, severity="warning", contributors=contributors,
        contributor_names=names, harnesses=harnesses,
        message=f"{check} on {', '.join(names)}", fingerprint=fingerprint,
    )


def test_render_orders_new_then_user_then_system(tmp_path):
    from rich.console import Console

    from drskill.report import render

    world = _mini_world(tmp_path)
    by_name = {c.name: c.id for c in world.contributors.values()}
    f_seen_user = _finding("aaa-check", ["mine"], "sha256:1111aaaa", [by_name["mine"]], ["t3"])
    f_new_system = _finding("bbb-check", ["vendor"], "sha256:2222bbbb", [by_name["vendor"]], ["t3"])
    f_new_user = _finding("ccc-check", ["mine"], "sha256:3333cccc", [by_name["mine"]], ["t3"])
    console = Console(record=True, width=200)
    render(world, [f_seen_user, f_new_system, f_new_user], [], console,
           seen={"sha256:1111aaaa"})
    text = console.export_text()
    assert text.index("ccc-check") < text.index("bbb-check") < text.index("aaa-check")
    assert "2 new" in text
    assert "[system skill]" in text


def test_render_new_tag_only_on_new_findings(tmp_path):
    from rich.console import Console

    from drskill.report import render

    world = _mini_world(tmp_path)
    by_name = {c.name: c.id for c in world.contributors.values()}
    f_new = _finding("aaa-check", ["mine"], "sha256:aaaa1111", [by_name["mine"]], ["t3"])
    f_seen = _finding("bbb-check", ["mine"], "sha256:bbbb2222", [by_name["mine"]], ["t3"])
    console = Console(record=True, width=200)
    render(world, [f_new, f_seen], [], console, seen={"sha256:bbbb2222"})
    text = console.export_text()
    assert "new aaa-check" in text
    assert "new bbb-check" not in text


def test_render_collapses_full_harness_list(tmp_path):
    from rich.console import Console

    from drskill.harnesses import HarnessDef
    from drskill.report import render

    defs = [
        HarnessDef(
            id="h1", display_name="H1",
            paths_verified=True, precedence_verified=True,
            project_paths=[".claude/skills"], recursive=True,
        ),
        HarnessDef(
            id="h2", display_name="H2",
            paths_verified=False, precedence_verified=False,
            project_paths=[".claude/skills"], recursive=True,
        ),
    ]
    world = _mini_world(tmp_path, harness_defs=defs)
    by_name = {c.name: c.id for c in world.contributors.values()}
    f_all = _finding("aaa-check", ["mine"], "sha256:cccc3333", [by_name["mine"]], ["h1", "h2"])
    f_one = _finding("bbb-check", ["mine"], "sha256:dddd4444", [by_name["mine"]], ["h1"])
    console = Console(record=True, width=200)
    render(world, [f_all, f_one], [], console)
    text = console.export_text()
    assert "all 2 harnesses (h2?)" in text
    assert "harnesses: h1\n" in text


def test_note_severity_renders_in_notes_section():
    note = sample_finding(check="description-overlap", severity="note")
    note = note.model_copy(update={
        "message": "overlap flagged (a, b); judged distinct by m, 2026-07-21",
        "fix_commands": [],
    })
    text = render_to_text(world_with(), [note], [])
    assert "NOTES" in text
    assert "judged distinct" in text
    assert "1 note" in text
    assert "0 errors, 0 warnings" in text
