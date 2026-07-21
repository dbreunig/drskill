"""Tier 3 injection surface checks: static flagging, never verification.

Every finding quotes the lines it judged. Lexicons and thresholds are module
constants tuned against real corpora (scripts/corpus.py); the ack ledger is
the user's escape hatch."""

from __future__ import annotations

import re
import shlex
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Contributor, Finding
from drskill.resolution import World, normalize_content

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


def _split_lines(text: str) -> list[str]:
    """Split on newlines only. str.splitlines also consumes U+2028/U+2029 and
    friends, which would hide those separators from the unicode check and
    skew reported line numbers."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _body_start(lines: list[str]) -> int:
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 2
    return 1


def scan_view(c: Contributor) -> list[Source]:
    """All scannable text of a skill, each file read once per content state.
    MCP tools have no files to scan and are never skill-shaped, so they get
    an empty view: the injection checks skip them entirely."""
    if c.kind != "skill":
        return []
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
        lines = _split_lines(text)
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
            Source(relpath=bf.relpath, kind=kind, text=ftext, lines=_split_lines(ftext))
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
    """Render control, format, and invisible-separator characters visibly;
    keep tabs."""
    return "".join(
        f"\\u{ord(ch):04x}"
        if ch != "\t" and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")
        else ch
        for ch in line
    )


def evidence_message(
    c: Contributor, summary: str, hits: list[Hit], note: str | None = None
) -> str:
    skill_dir = str(Path(c.id).parent)
    lines = [f"'{_printable(c.name)}' {summary} ({skill_dir}):"]
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
    # A name starting with "-" would be parsed as a flag by the installer
    # even shell-quoted, so those fall through to the path form.
    if c.source.kind in ("skills-lock", "linked") and not c.name.startswith("-"):
        return [f"npx skills remove {shlex.quote(c.name)}"]
    skill_file = Path(c.id)
    if skill_file.name == "SKILL.md":
        return [f"rm -r {shlex.quote(str(skill_file.parent))}"]
    return [f"rm {shlex.quote(str(skill_file))}"]


# ---- the checks ----

# Bidi controls and zero width space have no business in a skill file. The
# zero width joiner and non joiner are excluded outright: emoji sequences and
# several writing systems use them. A byte order mark is legitimate only as
# the first character of a file.
_SUSPECT_CHARS = frozenset(
    "\u200b\ufeff\u2028\u2029"
    "\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069"
)
_ALL_KINDS = {"skillmd", "script", "prose"}


@check("injection-unicode")
def injection_unicode(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        hits: list[Hit] = []
        names: set[str] = set()
        for s in scan_view(c):
            for i, line in enumerate(s.lines, start=1):
                scanned = line[1:] if i == 1 and line.startswith("\ufeff") else line
                found = [ch for ch in scanned if ch in _SUSPECT_CHARS]
                if found:
                    hits.append((s, i, line))
                    names.update(
                        unicodedata.name(ch, f"U+{ord(ch):04X}") for ch in found
                    )
        if hits:
            out.append(
                make_finding(
                    "injection-unicode", "error", [c],
                    evidence_message(
                        c,
                        "contains invisible or bidirectional control characters",
                        hits,
                        note="characters: " + ", ".join(sorted(names)),
                    ),
                    fix_commands=removal_commands(c),
                    extra_key=c.name,
                    fingerprint_texts=fingerprint_texts(hits),
                )
            )
    return out


# URLs share the base64 alphabet plus "/" and easily reach 120 characters, so
# they are stripped before matching. The hex floor sits above sha256's 64 and
# sha512's 128 hex digits, the digest lengths lockfiles and docs quote.
_URL = re.compile(r"https?://\S+")
_B64_RUN = re.compile(r"[A-Za-z0-9+/=]{120,}")
_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{129,}\b")


@check("injection-encoded-blob")
def injection_encoded_blob(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        hits: list[Hit] = []
        for s in scan_view(c):
            for i, line in enumerate(s.lines, start=1):
                stripped = _URL.sub("", line)
                if _B64_RUN.search(stripped) or _HEX_RUN.search(stripped):
                    hits.append((s, i, line))
        if hits:
            out.append(
                make_finding(
                    "injection-encoded-blob", "warning", [c],
                    evidence_message(
                        c, "contains long encoded blobs a reviewer cannot read", hits
                    ),
                    fix_commands=[
                        "Decode the blob yourself before trusting the skill, or remove it"
                    ],
                    extra_key=c.name,
                    fingerprint_texts=fingerprint_texts(hits),
                )
            )
    return out


_PROSE_KINDS = {"skillmd", "prose"}
_OVERRIDE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore\b.{0,20}\b(previous|prior|earlier|above|all other)\b.{0,20}\b(instructions?|rules|guidance|guidelines|prompts?)\b",
        r"\bdisregard\b.{0,30}\b(instructions?|rules|guidance|guidelines|system prompt)\b",
        # "(?!\s+to\b)" keeps the advice sense out: "do not tell the user to
        # run X" is UX guidance, "do not tell the user about X" is concealment
        # (real-loadout triage 2026-07-20).
        r"\bdo not (tell|inform|notify|warn|alert)\b.{0,15}\bthe user\b(?!\s+to\b)",
        r"\bwithout (informing|telling|notifying|alerting)\b.{0,15}\bthe user\b",
        r"\bhide (this|it) from\b",
        r"\bdo not reveal\b",
        r"\byou are no longer (bound|restricted|limited)\b",
        r"\bforget (everything|all previous)\b",
        r"\boverride (the )?(system|safety)\b",
    )
]


@check("injection-override")
def injection_override(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        hits = find_hits(scan_view(c), _OVERRIDE, _PROSE_KINDS)
        if hits:
            out.append(
                make_finding(
                    "injection-override", "warning", [c],
                    evidence_message(
                        c, "contains instruction-override phrasing", hits
                    ),
                    fix_commands=[
                        "Read the quoted lines; remove the skill if you did not expect them"
                    ],
                    extra_key=c.name,
                    fingerprint_texts=fingerprint_texts(hits),
                )
            )
    return out


# Matched in two linear steps (tool first, pipe-to-shell tail after it): a
# single backtracking regex is quadratic on crafted lines full of "curl"
# tokens, and a hostile skill stalling the scanner is itself an attack.
_FETCH_TOOL = re.compile(r"\b(curl|wget)\b")
_SHELL_TAIL = re.compile(r"\|\s*(sudo\s+)?(ba|z|da)?sh\b")


def _pipe_to_shell(line: str) -> bool:
    tool = _FETCH_TOOL.search(line)
    return bool(tool and _SHELL_TAIL.search(line, tool.end()))
_URLISH = re.compile(r"https?://")
# Local URLs are dev-server chatter, not remote content (corpus tuning
# 2026-07-20: `npm run dev` beside http://localhost was the main noise).
_LOCAL_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])\S*")
# A bare "run" near a URL is not a fetch instruction. Require a fetch verb
# joined to an act verb, or the explicit "follow the instructions" phrasing.
_FETCH_DIRECTIVE = re.compile(
    r"\b(download|fetch|retrieve|curl|wget|get)\b.{0,60}\b(and|then)\s+"
    r"(run|execute|eval|follow|apply)\b"
    r"|\bfollow (the|these|its) instructions\b",
    re.IGNORECASE,
)


@check("injection-remote-fetch")
def injection_remote_fetch(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        hits: list[Hit] = []
        for s in scan_view(c):
            if s.kind not in _PROSE_KINDS:
                continue
            for i, line in enumerate(s.lines, start=1):
                cleaned = _LOCAL_URL.sub("", line)
                if _pipe_to_shell(cleaned) or (
                    _URLISH.search(cleaned) and _FETCH_DIRECTIVE.search(cleaned)
                ):
                    hits.append((s, i, line))
        if hits:
            out.append(
                make_finding(
                    "injection-remote-fetch", "warning", [c],
                    evidence_message(
                        c,
                        "instructs the agent to fetch remote content and act on it",
                        hits,
                    ),
                    fix_commands=[
                        "Fetched content becomes instructions the skill author"
                        " controls after install; remove the step or pin the content"
                    ],
                    extra_key=c.name,
                    fingerprint_texts=fingerprint_texts(hits),
                )
            )
    return out


_SCRIPT_KINDS = {"script"}
_EGRESS = [
    re.compile(p)
    for p in (
        r"\bcurl\b",
        r"\bwget\b",
        r"\bnc\b",
        r"\bInvoke-WebRequest\b",
        r"\bInvoke-RestMethod\b",
        r"\brequests\.",
        # urllib.parse is string handling, not egress; Unix-domain sockets
        # are local IPC (corpus tuning 2026-07-20).
        r"\burllib\.request\b",
        r"\bhttpx\b",
        r"\baiohttp\b",
        r"\bsocket\.create_connection\b",
        r"\bAF_INET\b",
        r"\bfetch\s*\(",
        r"\baxios\b",
        r"\bXMLHttpRequest\b",
        r"\bNet::HTTP\b",
        r"\bhttps?\.(request|get)\b",
    )
]


@check("injection-egress")
def injection_egress(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        hits = find_hits(scan_view(c), _EGRESS, _SCRIPT_KINDS)
        if hits:
            out.append(
                make_finding(
                    "injection-egress", "warning", [c],
                    evidence_message(
                        c, "ships scripts that talk to the network", hits
                    ),
                    fix_commands=[
                        "Check each call's destination; a skill script can send"
                        " your files or context anywhere"
                    ],
                    extra_key=c.name,
                    fingerprint_texts=fingerprint_texts(hits),
                )
            )
    return out


_CRED_STORE = [
    re.compile(p)
    for p in (
        r"\.ssh\b",
        r"\bid_rsa\b",
        r"\bid_ed25519\b",
        # No bare .key pattern: JS/dict property access like `obj.key` fires
        # it constantly (corpus tuning 2026-07-20).
        r"\.pem\b",
        r"\.aws\b",
        r"\.config/gcloud",
        r"\.netrc\b",
        r"\.kube/config",
        r"\.mozilla/firefox",
        r"\.config/google-chrome",
        r"Google/Chrome",
    )
]
_ENV_FILE = re.compile(r"(?<![\w.])\.env\b")


@check("injection-credential-read")
def injection_credential_read(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        store_hits = find_hits(scan_view(c), _CRED_STORE, _SCRIPT_KINDS)
        env_hits = [
            h for h in find_hits(scan_view(c), [_ENV_FILE], _SCRIPT_KINDS)
            if h not in store_hits
        ]
        hits = store_hits + env_hits
        if not hits:
            continue
        # A project reading its own .env is common; credential stores are not.
        severity = "error" if store_hits else "warning"
        fixes = (
            removal_commands(c)
            if severity == "error"
            else ["Check what the script does with the values it reads"]
        )
        out.append(
            make_finding(
                "injection-credential-read", severity, [c],
                evidence_message(
                    c, "ships scripts that reference credential paths", hits
                ),
                fix_commands=fixes,
                extra_key=c.name,
                fingerprint_texts=fingerprint_texts(hits),
            )
        )
    return out


# The SkillJect pattern: the skill text demands its own bundled file runs
# first or always. Both parts are required on one line. A plain pointer like
# "run scripts/convert.py to convert the file" does not fire.
_MANDATORY = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(you )?must\b.{0,30}\brun\b",
        r"\bbefore (doing )?anything( else)?\b.{0,30}\brun\b",
        r"\b(always|first),? (begin|start) by running\b",
        r"\balways run\b",
        r"\bfirst,? (run|execute)\b",
        r"\brequired (first )?step\b.{0,30}\b(run|execute)\b",
    )
]


@check("injection-mandatory-script")
def injection_mandatory_script(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        if not c.bundled_files:
            continue
        # Word-bounded match on the relpath or basename; names shorter than
        # 3 characters are skipped because they match almost anything.
        names = {bf.relpath for bf in c.bundled_files}
        names |= {Path(bf.relpath).name for bf in c.bundled_files}
        names = {n for n in names if len(n) >= 3}
        if not names:
            continue
        path_re = re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
            + r")(?!\w)"
        )
        hits: list[Hit] = []
        for s in scan_view(c):
            if s.kind != "skillmd":
                continue
            for i, line in enumerate(s.lines, start=1):
                if i < s.body_start:
                    continue
                # The path must follow the framing: "must first run scripts/x"
                # fires, a filename before the noun phrase "first run" does
                # not (corpus tuning 2026-07-20).
                match = None
                for p in _MANDATORY:
                    match = p.search(line)
                    if match:
                        break
                if match and path_re.search(line, match.end()):
                    hits.append((s, i, line))
        if hits:
            out.append(
                make_finding(
                    "injection-mandatory-script", "warning", [c],
                    evidence_message(
                        c,
                        "demands that its own bundled script runs first",
                        hits,
                    ),
                    fix_commands=[
                        "Read the script before letting any agent run it"
                    ],
                    extra_key=c.name,
                    fingerprint_texts=fingerprint_texts(hits),
                )
            )
    return out
