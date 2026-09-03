import pytest

from drskill import skill_pub


def make_skill_dir(tmp_path):
    d = tmp_path / "my-skill"
    (d / "reference").mkdir(parents=True)
    (d / ".git").mkdir()
    (d / "SKILL.md").write_text("---\nname: My Skill\ndescription: Does things.\n---\nbody\n")
    (d / "reference" / "tips.md").write_text("tips\n")
    (d / "run.sh").write_text("#!/bin/sh\n")
    (d / "run.sh").chmod(0o755)
    (d / ".git" / "config").write_text("ignored\n")
    return d


def test_collect_dir_shapes_and_exclusions(tmp_path):
    files = skill_pub.collect_dir(make_skill_dir(tmp_path))
    by_path = {f["path"]: f for f in files}
    assert sorted(by_path) == ["SKILL.md", "reference/tips.md", "run.sh"]
    assert by_path["run.sh"]["executable"]
    assert not by_path["SKILL.md"]["executable"]
    assert by_path["reference/tips.md"]["data"] == b"tips\n"


def test_collect_dir_requires_a_skill_md(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    (d / "notes.md").write_text("x")
    with pytest.raises(ValueError):
        skill_pub.collect_dir(d)


def test_frontmatter_meta_normalizes_the_name(tmp_path):
    files = skill_pub.collect_dir(make_skill_dir(tmp_path))
    name, description = skill_pub.frontmatter_meta(files, fallback="my-skill")
    assert name == "my-skill"
    assert description == "Does things."


def test_frontmatter_meta_falls_back(tmp_path):
    d = tmp_path / "bare"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter\n")
    files = skill_pub.collect_dir(d)
    name, description = skill_pub.frontmatter_meta(files, fallback="bare")
    assert name == "bare"
    assert description is None


def test_parse_skill_ref():
    assert skill_pub.parse_skill_ref("drew/citation-style") == ("drew", "citation-style", None)
    assert skill_pub.parse_skill_ref("drew/citation-style@3") == ("drew", "citation-style", 3)
    for junk in ("nope", "a/b/c", "drew/x@0", "drew/x@abc"):
        with pytest.raises(ValueError):
            skill_pub.parse_skill_ref(junk)


def test_blocking_findings_keeps_errors_and_warnings():
    class F:
        def __init__(self, severity):
            self.severity = severity

    findings = [F("error"), F("warning"), F("note")]
    assert [f.severity for f in skill_pub.blocking_findings(findings)] == ["error", "warning"]
