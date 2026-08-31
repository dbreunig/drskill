import stat
import tomllib

import pytest

from drskill import sync
from drskill.ledger import Ack, load_config

FP_A = "sha256:" + "aa" * 32
FP_B = "sha256:" + "bb" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    return tmp_path


def ack(fp, check="injection-egress", skills=("citation-style",), note=None):
    return Ack(check=check, skills=list(skills), fingerprint=fp, note=note)


def remote_event(fp, action, seq, check="injection-egress", skill_identity="a,b", note=None):
    return {
        "client_event_id": f"00000000-0000-0000-0000-{seq:012d}",
        "fingerprint": fp, "action": action, "check_id": check,
        "skill_identity": skill_identity, "note": note,
        "client_recorded_at": "2026-08-31T00:00:00Z", "server_sequence": seq,
    }


def test_state_round_trip_with_restrictive_mode(home):
    state = {"cursor": 7, "fingerprints": [FP_A], "pending": []}
    sync.save_state(state)
    path = home / ".drskill" / "sync.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sync.load_state() == state


def test_load_state_defaults_when_missing(home):
    assert sync.load_state() == {"cursor": 0, "fingerprints": [], "pending": []}


def test_mint_events_diffs_against_the_base(home):
    events, summary = sync.mint_events([ack(FP_A)], [FP_B])
    assert summary == {"acks": 1, "reopens": 1}
    by_action = {e["action"]: e for e in events}
    added = by_action["acknowledged"]
    assert added["fingerprint"] == FP_A
    assert added["check_id"] == "injection-egress"
    assert added["skill_identity"] == "citation-style"
    assert added["client_event_id"]
    removed = by_action["reopened"]
    assert removed["fingerprint"] == FP_B


def test_mint_events_no_change_is_empty(home):
    events, summary = sync.mint_events([ack(FP_A)], [FP_A])
    assert events == []
    assert summary == {"acks": 0, "reopens": 0}


def test_mint_events_uuids_are_unique_per_event(home):
    events, _ = sync.mint_events([ack(FP_A), ack(FP_B)], [])
    ids = [e["client_event_id"] for e in events]
    assert len(set(ids)) == 2


def test_apply_remote_appends_and_removes_preserving_other_keys(home, tmp_path):
    ledger_file = tmp_path / ".drskill.toml"
    ledger_file.write_text(
        '[budget]\ncatalog_tokens_max = 1234\n\n'
        '[[ack]]\ncheck = "old-check"\nskills = ["s"]\nfingerprint = "' + FP_B + '"\n'
    )

    counts = sync.apply_remote(
        [remote_event(FP_A, "acknowledged", 1), remote_event(FP_B, "reopened", 2)],
        ledger_file,
    )
    assert counts == {"acks": 1, "reopens": 1}

    config = load_config(ledger_file)
    fingerprints = {a.fingerprint for a in config.ack}
    assert fingerprints == {FP_A}
    raw = tomllib.loads(ledger_file.read_text())
    assert raw["budget"]["catalog_tokens_max"] == 1234
    applied = config.ack[0]
    assert applied.check == "injection-egress"
    assert applied.skills == ["a", "b"]


def test_apply_remote_last_event_wins_within_a_batch(home, tmp_path):
    ledger_file = tmp_path / ".drskill.toml"
    ledger_file.write_text("")
    counts = sync.apply_remote(
        [
            remote_event(FP_A, "acknowledged", 1),
            remote_event(FP_A, "reopened", 2),
            remote_event(FP_A, "acknowledged", 3),
        ],
        ledger_file,
    )
    assert counts == {"acks": 1, "reopens": 0}
    assert {a.fingerprint for a in load_config(ledger_file).ack} == {FP_A}


def test_apply_remote_noop_when_state_matches(home, tmp_path):
    ledger_file = tmp_path / ".drskill.toml"
    ledger_file.write_text(
        '[[ack]]\ncheck = "c"\nskills = ["s"]\nfingerprint = "' + FP_A + '"\n'
    )
    before = ledger_file.read_text()
    counts = sync.apply_remote([remote_event(FP_A, "acknowledged", 1)], ledger_file)
    assert counts == {"acks": 0, "reopens": 0}
    assert ledger_file.read_text() == before
