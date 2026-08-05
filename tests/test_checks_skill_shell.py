from pathlib import Path

from drskill.checks import skill_shell
from drskill.discovery import discover
from drskill.harnesses import HarnessDef
from drskill.ledger import Config
from drskill.resolution import build_world


def make_world(root):
    h = HarnessDef(
        id="t3", display_name="T3",
        paths_verified=True, precedence_verified=True,
        project_paths=[".claude/skills"], recursive=True,
    )
    instances, broken = discover(h, root, root / "no-home")
    return build_world(instances, {"t3": h}, broken)


def write_skill(root, name, body, description="Use when testing."):
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )
    return d


def the_contributor(world):
    (c,) = world.contributors.values()
    return c


def run_check(check_id, world, config=None):
    from drskill.checks import REGISTRY

    return REGISTRY[check_id](world, config or Config())


# ---- extraction ----

def test_extract_inline_at_line_start_and_after_whitespace():
    text = "!`git status`\nSee !`git diff HEAD` for detail.\n"
    assert skill_shell.extract_commands(text) == [
        (1, "git status"), (2, "git diff HEAD"),
    ]


def test_extract_inline_after_other_char_is_inert():
    # documented rule: KEY=!`cmd` is left as literal text and never runs
    assert skill_shell.extract_commands("KEY=!`whoami`\n") == []


def test_extract_multiple_per_line():
    text = "- a: !`git log -1` b: !`git branch`\n"
    assert skill_shell.extract_commands(text) == [
        (1, "git log -1"), (1, "git branch"),
    ]


def test_extract_fenced_block():
    text = "## Env\n```!\nnode --version\n\ngit status --short\n```\nAfter.\n"
    assert skill_shell.extract_commands(text) == [
        (3, "node --version"), (5, "git status --short"),
    ]


def test_extract_unterminated_fence_runs_to_eof():
    text = "```!\necho one\necho two\n"
    assert skill_shell.extract_commands(text) == [(2, "echo one"), (3, "echo two")]


def test_extract_inside_fence_no_inline_parsing():
    # lines inside a ```! fence are commands wholesale, not re-parsed
    text = "```!\n!`not nested`\n```\n"
    assert skill_shell.extract_commands(text) == [(2, "!`not nested`")]


def test_extract_frontmatter_is_scanned():
    text = "---\nname: x\ndescription: shows !`uname -a` output\n---\nBody.\n"
    assert skill_shell.extract_commands(text) == [(3, "uname -a")]


def test_extract_empty_command_and_plain_text():
    assert skill_shell.extract_commands("!``\nno commands here\n") == []


def test_skillmd_source_none_for_mcp_tools(tmp_path):
    write_skill(tmp_path, "plain", "No commands.")
    c = the_contributor(make_world(tmp_path))
    tool = c.model_copy(update={"kind": "mcp_tool"})
    assert skill_shell._skillmd(tool) is None
    assert skill_shell._skillmd(c) is not None


# ---- baseline storage ----

def test_baseline_key_is_portable(tmp_path):
    # project-relative identity: same key from any machine's checkout
    write_skill(tmp_path, "keyed", "!`git status`\n")
    c = the_contributor(make_world(tmp_path))
    home = tmp_path / "no-home"
    k1 = skill_shell.baseline_key(c, tmp_path, home)
    assert k1 == skill_shell.baseline_key(c, tmp_path, home)  # stable
    assert len(k1) == 64
    # a home-scope skill keys ~-relative: independent of where home sits
    ident = skill_shell._norm_path(Path(c.id), tmp_path, home)
    assert ident.startswith("./")


def test_load_baselines_skips_corrupt(tmp_path):
    from drskill.models import ShellBaseline

    bdir = tmp_path / "skill-shell"
    bdir.mkdir()
    good = ShellBaseline(name="a", path="./x", commands=["git status"], date="2026-08-04")
    (bdir / "aa11.json").write_text(good.model_dump_json())
    (bdir / "bad.json").write_text("{not json")
    loaded = skill_shell.load_baselines(bdir)
    assert list(loaded) == ["aa11"]
    assert loaded["aa11"].commands == ["git status"]
    assert skill_shell.load_baselines(tmp_path / "missing") == {}


def test_run_scan_loads_matching_baseline(tmp_path):
    import json

    from drskill.pipeline import run_scan

    proj = tmp_path / "proj"
    home = tmp_path / "home"
    home.mkdir()
    write_skill(proj, "loader", "!`git status`\n")
    # compute the key the pipeline will look for, then plant a baseline
    world = make_world(proj)
    c = the_contributor(world)
    key = skill_shell.baseline_key(c, proj, home)
    bdir = skill_shell.shell_dir(proj, home, False)
    bdir.mkdir(parents=True)
    (bdir / f"{key}.json").write_text(json.dumps({
        "name": "loader", "path": "./.claude/skills/loader/SKILL.md",
        "commands": ["git status"], "date": "2026-08-04",
    }))
    world2, _findings = run_scan(proj, home)
    approved = {c2.name: b for cid, b in world2.shell_approved.items()
                for c2 in [world2.contributors[cid]]}
    assert approved["loader"].commands == ["git status"]
