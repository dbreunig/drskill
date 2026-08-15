"""Claude Code .claude-plugin manifest checks.

Format facts: SchemaStore claude-code-plugin-manifest.json (generated
2026-04-23) and code.claude.com/docs plugins-reference.md (retrieved
2026-08-14). `name` is the only required field (kebab-case, no spaces);
component-pointer fields take string|array (skills/commands/agents/
workflows/outputStyles/themes/monitors) or string|array|object (hooks/
mcpServers/lspServers); default component dirs are optional and never
flagged. Every check no-ops when world.cc_plugin is None."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World

CC_KNOWN_FIELDS = {
    "$schema", "name", "version", "displayName", "description", "author",
    "homepage", "repository", "license", "keywords", "metadata",
    "defaultEnabled", "userConfig", "channels", "dependencies", "settings",
    "skills", "commands", "agents", "workflows", "hooks", "mcpServers",
    "lspServers", "outputStyles", "themes", "monitors", "experimental",
}
# pointer field -> allowed value types
_PATHISH = (str, list)
_PATHISH_OR_INLINE = (str, list, dict)
CC_POINTER_TYPES = {
    "skills": _PATHISH, "commands": _PATHISH, "agents": _PATHISH,
    "workflows": _PATHISH, "outputStyles": _PATHISH, "themes": _PATHISH,
    "monitors": _PATHISH,
    "hooks": _PATHISH_OR_INLINE, "mcpServers": _PATHISH_OR_INLINE,
    "lspServers": _PATHISH_OR_INLINE,
}
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _ccf(check_id, severity, world, message, reason, fix=None):
    """Path-free fingerprint: manifest content + reason slug, so acks
    survive across checkouts (mcp_spec convention)."""
    text = world.cc_plugin.raw_text or reason
    payload = "|".join(
        [check_id, hashlib.sha256(text.encode()).hexdigest(), reason]
    )
    return make_finding(
        check_id, severity, [], message,
        fix_commands=fix or [], fingerprint_texts=[payload],
    )


def _mismatch_fp(check_id, severity, world, message, reason, fix=None):
    """cc-manifest-mismatch judges BOTH manifests, so its fingerprint must
    cover both raw_texts — otherwise editing root plugin.json's version
    doesn't change the fingerprint and a stale ack keeps a still-mismatched
    pair silent. Path-free like `_ccf`, same reason-slug convention."""
    a_text = world.plugin.raw_text or ""
    b_text = world.cc_plugin.raw_text or ""
    combined = a_text + "\x00" + b_text
    payload = "|".join(
        [check_id, hashlib.sha256(combined.encode()).hexdigest(), reason]
    )
    return make_finding(
        check_id, severity, [], message,
        fix_commands=fix or [], fingerprint_texts=[payload],
    )


@check("cc-manifest-invalid")
def cc_manifest_invalid(world: World, config: Config) -> list[Finding]:
    m = world.cc_plugin
    if m is None:
        return []
    where = f"{m.root}/.claude-plugin/plugin.json"
    if m.parse_error:
        return [_ccf(
            "cc-manifest-invalid", "error", world,
            f"{where} does not parse: {m.parse_error}",
            "parse-error", fix=[f"Fix the JSON syntax in {where}"],
        )]
    out = []
    name = m.raw.get("name")
    if not isinstance(name, str) or not name:
        out.append(_ccf(
            "cc-manifest-invalid", "error", world,
            f"{where} is missing the required `name` string "
            "(the only required field)",
            "missing-name",
        ))
    elif not _KEBAB_RE.fullmatch(name):
        out.append(_ccf(
            "cc-manifest-invalid", "error", world,
            f"plugin name {name!r} is not kebab-case "
            "(lowercase letters/digits separated by single hyphens)",
            "name-not-kebab",
        ))
    for field, allowed in CC_POINTER_TYPES.items():
        v = m.raw.get(field)
        if v is not None and not isinstance(v, allowed):
            kinds = "string or array" if allowed is _PATHISH else (
                "string, array, or object"
            )
            out.append(_ccf(
                "cc-manifest-invalid", "error", world,
                f"`{field}` must be a {kinds}, got {type(v).__name__}",
                f"pointer-type:{field}",
            ))
    return out


@check("cc-manifest-unknown-field")
def cc_manifest_unknown_field(world: World, config: Config) -> list[Finding]:
    m = world.cc_plugin
    if m is None or m.parse_error:
        return []
    out = []
    for field in sorted(set(m.raw) - CC_KNOWN_FIELDS):
        out.append(_ccf(
            "cc-manifest-unknown-field", "warning", world,
            f"unknown manifest field `{field}`; known fields are "
            f"{', '.join(sorted(CC_KNOWN_FIELDS))}",
            f"unknown:{field}",
        ))
    return out


@check("cc-component-missing")
def cc_component_missing(world: World, config: Config) -> list[Finding]:
    m = world.cc_plugin
    if m is None or m.parse_error:
        return []
    root = Path(m.root)
    out = []
    for field, allowed in CC_POINTER_TYPES.items():
        v = m.raw.get(field)
        entries = [v] if isinstance(v, str) else (v if isinstance(v, list) else [])
        for e in entries:
            if not isinstance(e, str):
                continue  # wrong element types are cc-manifest-invalid's job
            if not (root / e).exists():
                out.append(_ccf(
                    "cc-component-missing", "error", world,
                    f"`{field}` declares {e!r} but {root / e} does not exist",
                    f"missing:{field}:{e}",
                ))
    return out


@check("cc-manifest-mismatch")
def cc_manifest_mismatch(world: World, config: Config) -> list[Finding]:
    a, b = world.plugin, world.cc_plugin
    if a is None or b is None or a.parse_error or b.parse_error:
        return []
    out = []
    for field in ("name", "version"):
        va, vb = a.raw.get(field), b.raw.get(field)
        if isinstance(va, str) and isinstance(vb, str) and va != vb:
            out.append(_mismatch_fp(
                "cc-manifest-mismatch", "warning", world,
                f"plugin.json and .claude-plugin/plugin.json disagree on "
                f"`{field}`: {va!r} vs {vb!r} — regenerate or reconcile "
                "the manifests",
                f"mismatch:{field}",
            ))
    return out
