"""Per-machine seen-finding memory. Not the ledger: this is what the report
has already shown you, not what you decided. Lives under the home directory
so drskill never writes into a scanned repo uninvited."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path


def state_path(project_root: Path, home: Path, global_mode: bool) -> Path:
    if global_mode:
        key = "global"
    else:
        key = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:16]
    return home / ".drskill" / "state" / f"{key}.json"


def load_seen(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if isinstance(k, str)}


def mark_seen(path: Path, fingerprints: Iterable[str], today: dt.date) -> None:
    """Merge, prune to the current set, write. Best effort: an unwritable
    state directory degrades to everything-looks-new, never an error."""
    current = set(fingerprints)
    seen = load_seen(path)
    merged = {fp: seen.get(fp, today.isoformat()) for fp in sorted(current)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, indent=0))
    except OSError:
        pass
