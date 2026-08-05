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
