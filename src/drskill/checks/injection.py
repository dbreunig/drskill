"""Tier 3 injection surface checks: static flagging, never verification.

Every finding quotes the lines it judged. Lexicons and thresholds are module
constants tuned against real corpora (scripts/corpus.py); the ack ledger is
the user's escape hatch."""

from __future__ import annotations

import shlex
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from drskill.models import Contributor
from drskill.resolution import normalize_content

SCRIPT_EXTS = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".ts", ".rb", ".pl", ".ps1"}
)


@dataclass
class Source:
    relpath: str  # "SKILL.md" or the bundled file's relpath
    kind: str  # "skillmd" | "script" | "prose"
    text: str
    lines: list[str]
    body_start: int = 1  # 1-based first body line; only meaningful for skillmd


Hit = tuple[Source, int, str]  # source, 1-based line number, the line

# Keyed by content state, so a stale entry is impossible and the cache never
# returns a view for edited files. Bounded by the number of distinct skill
# states seen by one process; tests and scans stay small.
_VIEW_CACHE: dict[tuple, list[Source]] = {}


def _cache_key(c: Contributor) -> tuple:
    return (
        c.id,
        c.content_hash,
        tuple((f.relpath, f.content_hash) for f in c.bundled_files),
    )


def _body_start(lines: list[str]) -> int:
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 2
    return 1


def scan_view(c: Contributor) -> list[Source]:
    """All scannable text of a skill, each file read once per content state."""
    key = _cache_key(c)
    if key in _VIEW_CACHE:
        return _VIEW_CACHE[key]
    sources: list[Source] = []
    skill_file = Path(c.id)
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if text:
        lines = text.splitlines()
        sources.append(
            Source(
                relpath=skill_file.name,
                kind="skillmd",
                text=text,
                lines=lines,
                body_start=_body_start(lines),
            )
        )
    base = skill_file.parent
    for bf in c.bundled_files:
        if not bf.is_text or bf.oversize:
            continue
        try:
            ftext = (base / bf.relpath).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ext = Path(bf.relpath).suffix.lower()
        kind = "script" if ext in SCRIPT_EXTS or ftext.startswith("#!") else "prose"
        sources.append(
            Source(relpath=bf.relpath, kind=kind, text=ftext, lines=ftext.splitlines())
        )
    _VIEW_CACHE[key] = sources
    return sources


def find_hits(sources: list[Source], patterns, kinds: set[str]) -> list[Hit]:
    out: list[Hit] = []
    for s in sources:
        if s.kind not in kinds:
            continue
        for i, line in enumerate(s.lines, start=1):
            if any(p.search(line) for p in patterns):
                out.append((s, i, line))
    return out


_SNIPPET_MAX = 100


def _printable(line: str) -> str:
    """Render control and format characters visibly; keep tabs."""
    return "".join(
        f"\\u{ord(ch):04x}"
        if ch != "\t" and unicodedata.category(ch) in ("Cc", "Cf")
        else ch
        for ch in line
    )


def evidence_message(
    c: Contributor, summary: str, hits: list[Hit], note: str | None = None
) -> str:
    skill_dir = str(Path(c.id).parent)
    lines = [f"'{c.name}' {summary} ({skill_dir}):"]
    for s, n, line in hits[:3]:
        snippet = _printable(line.strip())
        if len(snippet) > _SNIPPET_MAX:
            snippet = snippet[: _SNIPPET_MAX - 1].rstrip() + "…"
        lines.append(f'        {s.relpath}:{n}: "{snippet}"')
    if len(hits) > 3:
        lines.append(f"        (and {len(hits) - 3} more)")
    if note:
        lines.append(f"        ({note})")
    lines.append(
        "        (static flag: drskill shows the evidence; it cannot verify intent)"
    )
    return "\n".join(lines)


def fingerprint_texts(hits: list[Hit]) -> list[str]:
    """Contents of the files containing hits: an ack survives edits to files
    without hits and resurfaces when a hit file changes."""
    seen: dict[str, str] = {}
    for s, _n, _line in hits:
        seen[s.relpath] = (
            normalize_content(s.text) if s.kind == "skillmd" else s.text
        )
    return [seen[k] for k in sorted(seen)]


def removal_commands(c: Contributor) -> list[str]:
    if c.source.kind in ("skills-lock", "linked"):
        return [f"npx skills remove {shlex.quote(c.name)}"]
    skill_file = Path(c.id)
    if skill_file.name == "SKILL.md":
        return [f"rm -r {shlex.quote(str(skill_file.parent))}"]
    return [f"rm {shlex.quote(str(skill_file))}"]
