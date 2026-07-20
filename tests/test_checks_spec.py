from pathlib import Path

from drskill.checks import REGISTRY, run_all
from drskill.discovery import discover
from drskill.harnesses import load_harnesses
from drskill.ledger import Config
from drskill.resolution import build_world


def world_from(proj: Path, home: Path):
    h = next(x for x in load_harnesses() if x.id == "claude-code")
    instances, broken = discover(h, proj, home)
    return build_world(instances, {h.id: h}, broken)


def write(proj: Path, folder: str, content: str):
    d = proj / ".claude" / "skills" / folder
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(content)


def by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check_id, []).append(f)
    return out


def test_spec_checks(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "mismatch", "---\nname: other-name\ndescription: d\n---\nb\n")
    write(proj, "nodesc", "---\nname: nodesc\n---\nb\n")
    write(proj, "longdesc", f"---\nname: longdesc\ndescription: {'x' * 1100}\n---\nb\n")
    write(proj, "badyaml", "---\n: bad: [yaml\n---\nb\n")
    write(proj, "angle", "---\nname: angle\ndescription: use <this> tool\n---\nb\n")
    write(proj, "clean", "---\nname: clean\ndescription: fine\n---\nb\n")
    findings = run_all(world_from(proj, home), Config())
    got = by_check(findings)
    assert [f.contributor_names for f in got["spec-name-mismatch"]] == [["other-name"]]
    assert got["spec-name-mismatch"][0].severity == "error"
    assert len(got["spec-missing-description"]) >= 1
    assert len(got["spec-description-too-long"]) == 1
    assert [f.contributor_names for f in got["spec-invalid-frontmatter"]] == [["badyaml"]]
    assert [f.contributor_names for f in got["frontmatter-angle-brackets"]] == [["angle"]]
    assert got["frontmatter-angle-brackets"][0].severity == "warning"
    clean_ids = [i for f in findings for i in f.contributor_names if i == "clean"]
    assert clean_ids == []


def test_folded_scalar_description_is_not_angle_brackets(tmp_path):
    # YAML's folded block scalar indicator ">-" is raw-text punctuation, not
    # a value containing an angle bracket; a multi-line description written
    # this way must not trip frontmatter-angle-brackets.
    proj, home = tmp_path / "p", tmp_path / "h"
    write(
        proj, "folded",
        "---\nname: folded\ndescription: >-\n  Line one.\n  Line two.\n---\nb\n",
    )
    findings = run_all(world_from(proj, home), Config())
    assert [f for f in findings if f.check_id == "frontmatter-angle-brackets"] == []


def test_errors_sort_before_warnings(tmp_path):
    proj, home = tmp_path / "p", tmp_path / "h"
    write(proj, "angle", "---\nname: angle\ndescription: <x>\n---\nb\n")
    write(proj, "nodesc", "---\nname: nodesc\n---\nb\n")
    findings = run_all(world_from(proj, home), Config())
    severities = [f.severity for f in findings]
    assert severities == sorted(severities)  # "error" < "warning"


def test_fingerprint_stable_and_distinct():
    from drskill.checks import fingerprint
    from tests.test_models import make_contributor

    a = make_contributor(content_hash="sha256:1")
    b = make_contributor(content_hash="sha256:2")
    assert fingerprint("c", [a, b]) == fingerprint("c", [b, a])
    assert fingerprint("c", [a, b]) != fingerprint("c2", [a, b])
    assert fingerprint("c", [a]) != fingerprint("c", [a], extra="k")
