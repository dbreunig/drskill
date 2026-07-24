"""Shared helpers for trace adapters. Deterministic, dependency free."""

from __future__ import annotations

import datetime as dt
import re

from drskill.text import one_line

EXCERPT_LIMIT = 200

_SKILL_MD = re.compile(r"([A-Za-z0-9._-]+)/SKILL\.md")
_SINCE_DAYS = re.compile(r"(\d+)d")


def excerpt(s: str | None) -> str | None:
    if s is None:
        return None
    return one_line(s, EXCERPT_LIMIT)


def skill_md_names(text: str) -> list[str]:
    """Skill directory names from SKILL.md paths, first-seen order, deduped."""
    seen: list[str] = []
    for m in _SKILL_MD.finditer(text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def parse_since(spec: str, now: dt.datetime) -> dt.datetime:
    """'7d' / '30d' / '2026-06-01' -> an aware UTC cutoff. Raises ValueError."""
    m = _SINCE_DAYS.fullmatch(spec)
    if m:
        return now - dt.timedelta(days=int(m.group(1)))
    d = dt.date.fromisoformat(spec)
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)


def parse_ts(value: object) -> dt.datetime | None:
    """ISO string (Z suffix fine) -> aware UTC datetime, else None."""
    if not isinstance(value, str):
        return None
    try:
        ts = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc)


def munge_path(p: str) -> str:
    """A cwd the way Claude Code names its per-project trace directory."""
    return re.sub(r"[/.]", "-", p)
