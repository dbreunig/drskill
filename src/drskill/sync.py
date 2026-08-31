"""Machine-ledger acknowledgment sync: state file, event minting, and
applying remote events. The merge protocol is specified in drskill-web's
docs/superpowers/specs/2026-08-31-phase-5-ack-sync-design.md; the rule is
last event wins per fingerprint, ordered by server_sequence."""

from __future__ import annotations

import datetime as dt
import os
import tomllib
import uuid
from pathlib import Path

import tomli_w

from drskill.ledger import Ack

_DEFAULT_STATE = {"cursor": 0, "fingerprints": [], "pending": []}


def _home() -> Path:
    env = os.environ.get("DRSKILL_HOME")
    return Path(env) if env else Path.home()


def sync_state_path() -> Path:
    return _home() / ".drskill" / "sync.toml"


def load_state() -> dict:
    path = sync_state_path()
    if not path.exists():
        return dict(_DEFAULT_STATE, fingerprints=[], pending=[])
    data = tomllib.loads(path.read_text())
    return {
        "cursor": int(data.get("cursor", 0)),
        "fingerprints": list(data.get("fingerprints", [])),
        "pending": list(data.get("pending", [])),
    }


def save_state(state: dict) -> None:
    path = sync_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = {
        "cursor": state["cursor"],
        "fingerprints": state["fingerprints"],
        # tomli_w rejects None values; drop them from pending events.
        "pending": [
            {k: v for k, v in event.items() if v is not None}
            for event in state["pending"]
        ],
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(tomli_w.dumps(payload))
    os.chmod(path, 0o600)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mint_events(acks: list[Ack], base_fingerprints: list[str]) -> tuple[list[dict], dict]:
    current = {a.fingerprint: a for a in acks}
    base = set(base_fingerprints)
    events: list[dict] = []

    for fingerprint, ack in current.items():
        if fingerprint in base:
            continue
        recorded = (
            dt.datetime.combine(ack.date, dt.time.min, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if ack.date else _now_iso()
        )
        events.append({
            "client_event_id": str(uuid.uuid4()),
            "fingerprint": fingerprint,
            "action": "acknowledged",
            "check_id": ack.check,
            "skill_identity": ",".join(ack.skills) or None,
            "note": ack.note,
            "client_recorded_at": recorded,
        })
    for fingerprint in base - set(current):
        events.append({
            "client_event_id": str(uuid.uuid4()),
            "fingerprint": fingerprint,
            "action": "reopened",
            "check_id": "sync",
            "skill_identity": None,
            "note": None,
            "client_recorded_at": _now_iso(),
        })

    summary = {
        "acks": sum(1 for e in events if e["action"] == "acknowledged"),
        "reopens": sum(1 for e in events if e["action"] == "reopened"),
    }
    return events, summary


def apply_remote(events: list[dict], ledger_file: Path) -> dict:
    """Fold a batch of downloaded events into the machine ledger.
    Within the batch, the highest server_sequence per fingerprint wins."""
    latest: dict[str, dict] = {}
    for event in sorted(events, key=lambda e: e["server_sequence"]):
        latest[event["fingerprint"]] = event

    raw = tomllib.loads(ledger_file.read_text()) if ledger_file.exists() else {}
    existing = list(raw.get("ack", []))
    existing_fps = {a.get("fingerprint") for a in existing}

    to_add = []
    to_remove = set()
    for fingerprint, event in latest.items():
        if event["action"] == "acknowledged" and fingerprint not in existing_fps:
            to_add.append(event)
        elif event["action"] == "reopened" and fingerprint in existing_fps:
            to_remove.add(fingerprint)

    if to_remove:
        raw["ack"] = [a for a in existing if a.get("fingerprint") not in to_remove]
        if not raw["ack"]:
            raw.pop("ack")
        # Rewrite loses comments in the machine ledger; accepted per spec.
        ledger_file.write_text(tomli_w.dumps(raw))

    from drskill import ledger

    for event in to_add:
        skills = [s for s in (event.get("skill_identity") or "").split(",") if s]
        ledger.append_ack(ledger_file, Ack(
            check=event["check_id"],
            skills=skills or ["unknown"],
            fingerprint=event["fingerprint"],
            note=event.get("note"),
            date=dt.datetime.fromisoformat(
                event["client_recorded_at"].replace("Z", "+00:00")
            ).date(),
        ))

    return {"acks": len(to_add), "reopens": len(to_remove)}
