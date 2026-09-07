"""Bindings from installed directories to the loadout entries they came
from. A pin is not provenance: Provenance answers where content came
from; a pin answers which loadout revision this installed copy belongs
to. One JSON document per scope; the project file is meant to be
committed, like the caches, so teammates resolve the same bindings."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError


class Pin(BaseModel):
    loadout: str
    revision: int | None = None
    selector: str
    source_type: str
    content_hash: str
    installed_at: str = ""


def pins_path(base: Path) -> Path:
    return base / ".drskill" / "pins.json"


def load_pins(base: Path) -> dict[str, Pin]:
    path = pins_path(base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Pin] = {}
    for key, value in data.items():
        try:
            out[str(key)] = Pin.model_validate(value)
        except ValidationError:
            continue  # a bad record is skipped so a hand-edit cannot erase unrelated bindings
    return out


def _write(base: Path, entries: dict[str, Pin]) -> None:
    path = pins_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {k: entries[k].model_dump() for k in sorted(entries)}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def record_pin(base: Path, target: Path, pin: Pin) -> None:
    try:
        key = str(target.relative_to(base))
    except ValueError:
        # A bridge-retargeted install can land outside the scope base; an
        # absolute key still binds on this machine.
        key = str(target)
    entries = load_pins(base)
    entries[key] = pin
    _write(base, entries)


def prune_pins(base: Path) -> None:
    entries = load_pins(base)
    if not entries:
        return
    kept = {k: v for k, v in entries.items()
            if (Path(k) if Path(k).is_absolute() else base / k).is_dir()}
    if len(kept) != len(entries):
        _write(base, kept)


def resolve_pins(project_root: Path, home: Path) -> dict[str, Pin]:
    """Both scopes merged, keyed by the resolved primary-file path so a
    scanned contributor's id looks itself up directly. Project pins win
    a collision."""
    out: dict[str, Pin] = {}
    for base in (home, project_root):
        for key, pin in load_pins(base).items():
            d = Path(key) if Path(key).is_absolute() else base / key
            out[str((d / "SKILL.md").resolve())] = pin
    return out
