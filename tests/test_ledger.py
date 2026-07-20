import datetime as dt
import tomllib
from pathlib import Path

import pytest

from drskill.ledger import (
    Ack,
    Config,
    LedgerError,
    append_ack,
    filter_findings,
    ledger_path,
    load_config,
)
from drskill.models import Finding


def make_finding(fp="sha256:aaa"):
    return Finding(
        check_id="near-duplicate", severity="warning",
        contributors=["/a", "/b"], contributor_names=["a", "b"],
        harnesses=["claude-code"], message="m", fingerprint=fp,
    )


def test_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "drskill.toml")
    assert cfg.budget.catalog_tokens_max == 6000
    assert cfg.budget.body_tokens_warn == 20000
    assert cfg.thresholds.near_duplicate == 0.85
    assert cfg.ack == []


def test_ledger_path(tmp_path):
    assert ledger_path(tmp_path, tmp_path / "h", False) == tmp_path / "drskill.toml"
    assert ledger_path(tmp_path, tmp_path / "h", True) == tmp_path / "h" / ".drskill.toml"


def test_load_full_file(tmp_path):
    p = tmp_path / "drskill.toml"
    p.write_text(
        "[budget]\ncatalog_tokens_max = 100\nbody_tokens_warn = 200\n"
        "[thresholds]\nnear_duplicate = 0.9\n"
        '[[ack]]\ncheck = "near-duplicate"\nskills = ["a", "b"]\n'
        'fingerprint = "sha256:aaa"\nnote = "keeping both"\ndate = 2026-07-19\n'
    )
    cfg = load_config(p)
    assert cfg.budget.catalog_tokens_max == 100
    assert cfg.thresholds.near_duplicate == 0.9
    assert cfg.ack[0].date == dt.date(2026, 7, 19)


def test_load_config_malformed_toml_raises_ledger_error(tmp_path):
    p = tmp_path / "drskill.toml"
    p.write_text("[budget\n")  # invalid TOML syntax
    with pytest.raises(LedgerError):
        load_config(p)


def test_load_config_schema_invalid_raises_ledger_error(tmp_path):
    p = tmp_path / "drskill.toml"
    p.write_text('budget = "oops"\n')  # budget must be a table, not a string
    with pytest.raises(LedgerError):
        load_config(p)


def test_filter_findings_matches_fingerprint(tmp_path):
    cfg = Config(ack=[Ack(check="near-duplicate", skills=["a", "b"], fingerprint="sha256:aaa")])
    hit, miss = make_finding("sha256:aaa"), make_finding("sha256:bbb")
    active, acked = filter_findings([hit, miss], cfg)
    assert acked == [hit] and active == [miss]


def test_append_ack_round_trip(tmp_path):
    p = tmp_path / "drskill.toml"
    p.write_text("[budget]\ncatalog_tokens_max = 100\n")
    append_ack(p, Ack(check="c", skills=["s"], fingerprint="sha256:x",
                      note=None, date=dt.date(2026, 7, 19)))
    append_ack(p, Ack(check="c2", skills=["s2"], fingerprint="sha256:y"))
    data = tomllib.loads(p.read_text())
    assert data["budget"]["catalog_tokens_max"] == 100
    assert [a["check"] for a in data["ack"]] == ["c", "c2"]
    assert "note" not in data["ack"][1]  # None fields omitted for tomli-w
    cfg = load_config(p)
    assert len(cfg.ack) == 2
