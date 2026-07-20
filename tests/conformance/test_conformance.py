import tomllib
from pathlib import Path

import pytest

from drskill.pipeline import run_scan

CASES_DIR = Path(__file__).parent / "cases"
CASES = sorted(p for p in CASES_DIR.iterdir() if p.is_dir()) if CASES_DIR.is_dir() else []


def matches(finding, entry) -> bool:
    return finding.check_id == entry["check"] and set(entry["skills"]) <= set(
        finding.contributor_names
    )


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_conformance_case(case_dir, tmp_path):
    expect = tomllib.loads((case_dir / "expect.toml").read_text())
    home = case_dir / "home"
    if not home.is_dir():
        home = tmp_path / "empty-home"
        home.mkdir()
    _world, findings = run_scan(case_dir / "tree", home)
    for entry in expect.get("expect", []):
        assert any(matches(f, entry) for f in findings), (
            f"expected {entry['check']} on {entry['skills']}; got "
            f"{[(f.check_id, f.contributor_names) for f in findings]}"
        )
    for entry in expect.get("forbid", []):
        hits = [f for f in findings if matches(f, entry)]
        assert not hits, f"forbidden finding fired: {hits}"
