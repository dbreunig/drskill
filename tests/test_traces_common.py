import datetime as dt

import pytest

from drskill.traces import common
from drskill.traces.model import Invocation

UTC = dt.timezone.utc


def test_excerpt_truncates_to_200_and_one_line():
    s = "a\nb " + "x" * 300
    out = common.excerpt(s)
    assert "\n" not in out
    assert len(out) <= 200


def test_excerpt_none_passthrough():
    assert common.excerpt(None) is None


def test_skill_md_names_extracts_dir_names():
    text = (
        "sed -n '1,240p' /Users/d/.agents/skills/overturemaps/SKILL.md; "
        "cat /Users/d/.pi/agent/skills/plain-writing/SKILL.md"
    )
    assert common.skill_md_names(text) == ["overturemaps", "plain-writing"]


def test_skill_md_names_dedupes_preserving_order():
    text = "a/skills/foo/SKILL.md b/skills/foo/SKILL.md"
    assert common.skill_md_names(text) == ["foo"]


def test_parse_since_days():
    now = dt.datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    assert common.parse_since("7d", now) == now - dt.timedelta(days=7)


def test_parse_since_date():
    now = dt.datetime(2026, 7, 23, tzinfo=UTC)
    got = common.parse_since("2026-06-01", now)
    assert got == dt.datetime(2026, 6, 1, tzinfo=UTC)


def test_parse_since_invalid_raises_value_error():
    with pytest.raises(ValueError):
        common.parse_since("yesterday", dt.datetime(2026, 7, 23, tzinfo=UTC))


def test_parse_ts_handles_z_suffix_and_garbage():
    got = common.parse_ts("2026-07-14T15:00:11.454Z")
    assert got == dt.datetime(2026, 7, 14, 15, 0, 11, 454000, tzinfo=UTC)
    assert common.parse_ts("not a date") is None
    assert common.parse_ts(None) is None


def test_parse_ts_naive_becomes_utc():
    got = common.parse_ts("2026-07-14T15:00:11")
    assert got.tzinfo is UTC


def test_munge_path_matches_claude_dir_names():
    assert common.munge_path("/Users/d/Development/drskill") == "-Users-d-Development-drskill"
    assert common.munge_path("/Users/d/.pencil/x") == "-Users-d--pencil-x"


def test_invocation_round_trips_json():
    inv = Invocation(
        harness="claude-code",
        session_id="s1",
        project="/p",
        timestamp=dt.datetime(2026, 7, 1, tzinfo=UTC),
        kind="skill",
        name="brainstorming",
        server=None,
        query="q",
        reasoning=None,
        sidechain=False,
        detection="explicit",
        source_file="/traces/s1.jsonl",
    )
    again = Invocation.model_validate_json(inv.model_dump_json())
    assert again == inv
