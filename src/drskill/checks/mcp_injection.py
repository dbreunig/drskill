"""Injection heuristics over MCP tool text (tool poisoning). Tool
descriptions and schema doc strings are instruction text the agent reads;
published attacks hide directives there. Static flagging only, reading
the committed snapshots, so the whole team gets findings after one person
connects. Lexicons are reused from checks/injection.py; the cross-tool
lexicon is unique to MCP (a server steering the agent away from other
servers' tools)."""

from __future__ import annotations

import re
from collections import defaultdict

from drskill.checks import check
from drskill.checks.injection import (
    _B64_RUN,
    _CRED_STORE,
    _FETCH_DIRECTIVE,
    _HEX_RUN,
    _LOCAL_URL,
    _OVERRIDE,
    _SUSPECT_CHARS,
    _URL,
    _URLISH,
    _pipe_to_shell,
    _printable,
    _split_lines,
)
from drskill.checks.mcp_tools import _fp
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World

# "(?!\s+this\b)" keeps self-scoping out: "do not use this tool for large
# files" is documentation, "do not use the search tool" is interference.
_CROSS_TOOL = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(do not|don't|never) use\b(?!\s+this\b).{0,30}\btool\b",
        r"\binstead of (using )?(the )?[\w-]+ tool\b",
        r"\bbefore (using|calling) any other tool\b",
        r"\balways use this tool\b",
        r"\buse this tool (first|instead)\b",
        r"\bignore (all )?other tools\b",
    )
]

_SNIPPET_MAX = 100

# (category key, severity, message summary)
_CATEGORIES = [
    ("unicode", "error", "contains invisible or bidirectional control characters"),
    ("credential-read", "error", "references credential paths"),
    ("override", "warning", "contains instruction-override phrasing"),
    ("encoded-blob", "warning", "contains long encoded blobs a reviewer cannot read"),
    ("remote-fetch", "warning", "tells the agent to fetch remote content and act on it"),
    ("cross-tool", "warning", "steers the agent toward or away from other tools"),
]


def _tool_text(t) -> str:
    return "\n".join([t.name, t.description, *t.schema_text])


def _tool_lines(t):
    for segment in (t.name, t.description, *t.schema_text):
        for line in _split_lines(segment):
            if line.strip():
                yield line


def _matches(category: str, line: str) -> bool:
    if category == "unicode":
        return any(ch in _SUSPECT_CHARS for ch in line)
    if category == "encoded-blob":
        stripped = _URL.sub("", line)
        return bool(_B64_RUN.search(stripped) or _HEX_RUN.search(stripped))
    if category == "remote-fetch":
        cleaned = _LOCAL_URL.sub("", line)
        return _pipe_to_shell(cleaned) or bool(
            _URLISH.search(cleaned) and _FETCH_DIRECTIVE.search(cleaned)
        )
    patterns = {
        "credential-read": _CRED_STORE,
        "override": _OVERRIDE,
        "cross-tool": _CROSS_TOOL,
    }[category]
    return any(p.search(line) for p in patterns)


@check("mcp-tool-poisoning")
def tool_poisoning(world: World, config: Config) -> list[Finding]:
    out = []
    servers_by_cfg: dict[str, list] = defaultdict(list)
    for s in world.mcp_servers:
        servers_by_cfg[s.config_hash].append(s)
    for cfg, snap in sorted(world.mcp_snapshots.items()):
        servers = servers_by_cfg.get(cfg)
        if not servers:
            continue
        server = servers[0]
        harnesses = sorted({s.harness for s in servers})
        for category, severity, summary in _CATEGORIES:
            hits: list[tuple[str, str]] = []  # (tool name, line)
            hit_texts: dict[str, str] = {}
            for t in snap.tools:
                for line in _tool_lines(t):
                    if _matches(category, line):
                        hits.append((t.name, line))
                        hit_texts[t.name] = _tool_text(t)
            if not hits:
                continue
            lines = ""
            for name, line in hits[:3]:
                snippet = _printable(line.strip())
                if len(snippet) > _SNIPPET_MAX:
                    snippet = snippet[: _SNIPPET_MAX - 1].rstrip() + "…"
                lines += f'\n        {name}: "{snippet}"'
            if len(hits) > 3:
                lines += f"\n        (and {len(hits) - 3} more)"
            lines += (
                "\n        (static flag: drskill shows the evidence; it"
                " cannot verify intent)"
            )
            out.append(Finding(
                check_id="mcp-tool-poisoning", severity=severity,
                contributors=sorted({s.source for s in servers}),
                contributor_names=[server.name], harnesses=harnesses,
                message=(
                    f"server '{server.name}' ({', '.join(harnesses)}) tool "
                    f"text {summary}:{lines}"
                ),
                fix_commands=[
                    f"Review the '{server.name}' server; remove it from "
                    f"{server.source} if you did not expect this text"
                ],
                fingerprint=_fp(
                    "mcp-tool-poisoning",
                    [server.name, category, *sorted(hit_texts.values())],
                ),
            ))
    return out
