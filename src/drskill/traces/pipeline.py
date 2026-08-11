"""Discover trace files, extract through the cache, filter by scope and time."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import BaseModel, Field

from drskill.traces import cache, claude_code, codex, copilot, pi
from drskill.traces.common import munge_path
from drskill.traces.model import Invocation

ADAPTERS = {
    claude_code.HARNESS: claude_code,
    codex.HARNESS: codex,
    pi.HARNESS: pi,
    copilot.HARNESS: copilot,
}


class UnknownTraceLocation(Exception):
    """A --file path outside every adapter's trace root."""


def infer_adapter(path: Path, home: Path):
    resolved = path.resolve()
    for adapter in ADAPTERS.values():
        if resolved.is_relative_to(adapter.trace_root(home).resolve()):
            return adapter
    raise UnknownTraceLocation(str(path))


class AuditData(BaseModel):
    invocations: list[Invocation] = Field(default_factory=list)
    unreadable: list[str] = Field(default_factory=list)
    drifted: dict[str, int] = Field(default_factory=dict)


def run_audit(
    home: Path,
    root: Path,
    global_mode: bool,
    harness: str | None,
    since: dt.datetime | None,
) -> AuditData:
    cdir = cache.audit_cache_dir(home)
    data = AuditData()
    live_keys: set[str] = set()
    selected = [ADAPTERS[harness]] if harness else list(ADAPTERS.values())
    for adapter in ADAPTERS.values():
        for trace in adapter.discover(home):
            live_keys.add(cache.entry_key(trace))
    for adapter in selected:
        for trace in adapter.discover(home):
            entry = cache.load_entry(cdir, trace)
            if entry is None or entry.adapter_version != adapter.VERSION:
                try:
                    st = trace.stat()
                    result = adapter.extract(trace)
                except Exception:
                    data.unreadable.append(str(trace))
                    continue
                entry = cache.TraceCacheEntry(
                    trace_path=str(trace), mtime_ns=st.st_mtime_ns,
                    size=st.st_size, adapter=adapter.HARNESS,
                    adapter_version=adapter.VERSION,
                    recognized=result.recognized,
                    invocations=result.invocations,
                )
                cache.store_entry(cdir, entry)
            if entry.recognized == 0 and entry.size > 0:
                data.drifted[adapter.HARNESS] = data.drifted.get(adapter.HARNESS, 0) + 1
            data.invocations.extend(entry.invocations)
    cache.prune_vanished(cdir, live_keys)
    data.invocations = _filtered(data.invocations, root, global_mode, since)
    data.invocations.sort(key=lambda i: i.timestamp)
    return data


def run_audit_file(
    home: Path,
    path: Path,
    harness: str | None,
    since: dt.datetime | None,
) -> AuditData:
    """Audit one explicit trace file: no cache, no project-scope filter."""
    adapter = ADAPTERS[harness] if harness else infer_adapter(path, home)
    result = adapter.extract(path)
    data = AuditData()
    data.invocations = [
        i for i in result.invocations
        if since is None or i.timestamp >= since
    ]
    if result.recognized == 0 and path.stat().st_size > 0:
        data.drifted[adapter.HARNESS] = 1
    data.invocations.sort(key=lambda i: i.timestamp)
    return data


def _filtered(
    invocations: list[Invocation],
    root: Path,
    global_mode: bool,
    since: dt.datetime | None,
) -> list[Invocation]:
    rp = str(root.resolve())
    munged = munge_path(rp)
    kept = []
    for inv in invocations:
        if since is not None and inv.timestamp < since:
            continue
        if not global_mode:
            if inv.project is not None:
                if inv.project != rp:
                    continue
            elif inv.harness == claude_code.HARNESS:
                if Path(inv.source_file).parent.name != munged:
                    continue
            else:
                continue
        kept.append(inv)
    return kept
