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


def run_sync(creds: dict, base_url: str, device_info: dict) -> dict:
    """The full protocol: diff → mint → upload (durable pending) → download
    → apply → persist state. Returns the printed summary counts."""
    from drskill import ledger, service

    ledger_file = _home() / ".drskill.toml"
    config = ledger.load_config(ledger_file) if ledger_file.exists() else None
    acks = config.ack if config else []

    state = load_state()
    fresh, _ = mint_events(acks, state["fingerprints"])
    # A failed upload leaves its events in pending; a retry re-mints the same
    # diff with new uuids. Skip anything pending already covers so the server
    # never sees two ids for one logical change.
    pending_keys = {(e["fingerprint"], e["action"]) for e in state["pending"]}
    fresh = [e for e in fresh if (e["fingerprint"], e["action"]) not in pending_keys]
    if fresh:
        state["pending"] = state["pending"] + fresh
        save_state(state)

    warnings: list[str] = []
    pushed = {"acks": 0, "reopens": 0}
    # Always POST at least once (an empty batch if there is nothing pending)
    # so the device block reaches the server every sync, not only when there
    # is something to push — the server registers/refreshes the device from
    # this call regardless of whether events accompany it.
    #
    # With something pending, a failed upload must still hard-fail before
    # download: pending durability and retry semantics depend on the upload
    # completing (or not) before anything else changes. With nothing
    # pending, the POST is a bare device-registration heartbeat with no
    # events at stake, so a failure there is best-effort — it must not
    # block a pure-download sync from completing.
    if state["pending"]:
        for start in range(0, len(state["pending"]), 500):
            batch = state["pending"][start:start + 500]
            service.api_request(
                "POST", "/api/v1/acknowledgment_sync",
                token=creds["token"],
                json_body={"device": device_info, "events": batch},
                base_url=base_url,
            )
        pushed = {
            "acks": sum(1 for e in state["pending"] if e["action"] == "acknowledged"),
            "reopens": sum(1 for e in state["pending"] if e["action"] == "reopened"),
        }
        state["pending"] = []
        save_state(state)
    else:
        try:
            service.api_request(
                "POST", "/api/v1/acknowledgment_sync",
                token=creds["token"],
                json_body={"device": device_info, "events": []},
                base_url=base_url,
            )
        except service.ServiceError as err:
            warnings.append(
                f"device registration failed ({err.message}); continuing with download"
            )

    pulled = {"acks": 0, "reopens": 0}
    cursor = state["cursor"]
    while True:
        data = service.api_request(
            "GET", f"/api/v1/acknowledgment_sync?after={cursor}",
            token=creds["token"], base_url=base_url,
        )
        events = data.get("events", [])
        if events:
            counts = apply_remote(events, ledger_file)
            pulled["acks"] += counts["acks"]
            pulled["reopens"] += counts["reopens"]
        cursor = data.get("cursor", cursor)
        if not data.get("has_more"):
            break

    final = ledger.load_config(ledger_file) if ledger_file.exists() else None
    state["fingerprints"] = sorted({a.fingerprint for a in (final.ack if final else [])})
    state["cursor"] = cursor
    save_state(state)

    return {
        "pushed_acks": pushed["acks"], "pushed_reopens": pushed["reopens"],
        "pulled_acks": pulled["acks"], "pulled_reopens": pulled["reopens"],
        "warnings": warnings,
    }
