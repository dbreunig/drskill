"""Claude Code marketplace descriptor checks.

Format facts: code.claude.com/docs plugin-marketplaces.md (retrieved
2026-08-14). Required: name (kebab-case), owner.name, plugins[]. Seven
source forms: relative-path string; {source: github|url|git-subdir|npm|
archive|command, ...}. Pinning: git forms sha (precedence) / ref; npm
version; archive sha256. A command source runs a shell command at
install. Unrecognized source types warn rather than error: Claude Code
added archive (v2.1.224) and command (v2.1.229), and this fact base
will age. Every check no-ops when world.marketplace is None."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World
from drskill.text import one_line

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# source type -> fields required for that form
SOURCE_REQUIRED = {
    "github": ["repo"], "url": ["url"], "git-subdir": ["url", "path"],
    "npm": ["package"], "archive": ["url"], "command": ["command"],
}
_GIT_FORMS = {"github", "url", "git-subdir"}


def _mf(check_id, severity, world, message, reason, fix=None):
    payload = "|".join([
        check_id,
        hashlib.sha256((world.marketplace.text or reason).encode()).hexdigest(),
        reason,
    ])
    return make_finding(
        check_id, severity, [], message,
        fix_commands=fix or [], fingerprint_texts=[payload],
    )


def _entries(world):
    mp = world.marketplace
    if mp is None or mp.parse_error or mp.data is None:
        return []
    plugins = mp.data.get("plugins")
    return plugins if isinstance(plugins, list) else []


def _ename(entry, i):
    # Capped: a pathological multi-KB name must not balloon every message
    # (and reason slug) it appears in.
    name = entry.get("name") if isinstance(entry, dict) else None
    return one_line(name, 80) if isinstance(name, str) and name else f"entry {i}"


@check("marketplace-invalid")
def marketplace_invalid(world: World, config: Config) -> list[Finding]:
    mp = world.marketplace
    if mp is None:
        return []
    if mp.parse_error:
        return [_mf(
            "marketplace-invalid", "error", world,
            f"{mp.path} does not parse: {mp.parse_error}",
            "parse-error", fix=[f"Fix the JSON syntax in {mp.path}"],
        )]
    out = []
    name = mp.data.get("name")
    if not isinstance(name, str) or not name:
        out.append(_mf("marketplace-invalid", "error", world,
                       "marketplace is missing the required `name` string",
                       "missing-name"))
    elif not _KEBAB_RE.fullmatch(name):
        out.append(_mf("marketplace-invalid", "error", world,
                       f"marketplace name {name!r} is not kebab-case",
                       "name-not-kebab"))
    owner = mp.data.get("owner")
    if not (isinstance(owner, dict) and isinstance(owner.get("name"), str)
            and owner["name"]):
        out.append(_mf("marketplace-invalid", "error", world,
                       "marketplace is missing `owner` with a `name` string",
                       "missing-owner"))
    plugins = mp.data.get("plugins")
    if not isinstance(plugins, list):
        out.append(_mf("marketplace-invalid", "error", world,
                       "marketplace is missing the required `plugins` array",
                       "missing-plugins"))
        return out
    for i, entry in enumerate(plugins):
        ename = _ename(entry, i)
        if not isinstance(entry, dict):
            out.append(_mf("marketplace-invalid", "error", world,
                           f"plugins[{i}] is not an object", f"entry-shape:{i}"))
            continue
        n = entry.get("name")
        if not isinstance(n, str) or not n:
            out.append(_mf("marketplace-invalid", "error", world,
                           f"plugins[{i}] is missing the required `name`",
                           f"entry-missing-name:{i}"))
        elif not _KEBAB_RE.fullmatch(n):
            out.append(_mf("marketplace-invalid", "error", world,
                           f"plugin entry name {n!r} is not kebab-case",
                           f"entry-name-not-kebab:{n}"))
        src = entry.get("source")
        if src is None:
            out.append(_mf("marketplace-invalid", "error", world,
                           f"{ename} is missing the required `source`",
                           f"entry-missing-source:{ename}"))
        elif isinstance(src, dict):
            stype = src.get("source")
            required = SOURCE_REQUIRED.get(stype) if isinstance(stype, str) else None
            if required is None:
                out.append(_mf(
                    "marketplace-invalid", "warning", world,
                    f"{ename} has unrecognized source type {stype!r} "
                    "(newer than this lint's fact base, or a typo); known "
                    f"types: {', '.join(sorted(SOURCE_REQUIRED))}",
                    f"unknown-source-type:{ename}",
                ))
            else:
                for req in required:
                    if not isinstance(src.get(req), str) or not src.get(req):
                        out.append(_mf(
                            "marketplace-invalid", "error", world,
                            f"{ename} source form {stype!r} requires `{req}`",
                            f"source-missing:{ename}:{req}",
                        ))
        elif not isinstance(src, str):
            out.append(_mf("marketplace-invalid", "error", world,
                           f"{ename} `source` must be a string or object",
                           f"source-shape:{ename}"))
    return out


@check("marketplace-unpinned-source")
def marketplace_unpinned(world: World, config: Config) -> list[Finding]:
    out = []
    for i, entry in enumerate(_entries(world)):
        if not isinstance(entry, dict):
            continue
        ename = _ename(entry, i)
        src = entry.get("source")
        if not isinstance(src, dict):
            continue  # local paths have nothing to pin
        stype = src.get("source")
        for field in ("url", "repo", "registry"):
            v = src.get(field)
            if isinstance(v, str) and v.startswith("http://"):
                out.append(_mf(
                    "marketplace-unpinned-source", "warning", world,
                    f"{ename} uses insecure (non-TLS) source URL {v!r}",
                    f"insecure-url:{ename}",
                ))
        if stype in _GIT_FORMS:
            sha = src.get("sha")
            if isinstance(sha, str) and _SHA_RE.fullmatch(sha):
                continue
            if isinstance(src.get("ref"), str):
                out.append(_mf(
                    "marketplace-unpinned-source", "note", world,
                    f"{ename} pins only a ref ({src['ref']!r}); a ref can "
                    "be a movable branch — pin `sha` for immutability",
                    f"ref-only:{ename}",
                ))
            else:
                out.append(_mf(
                    "marketplace-unpinned-source", "warning", world,
                    f"{ename} has no `sha` or `ref` — installs track the "
                    "remote's default branch and can change under you",
                    f"unpinned:{ename}",
                ))
        elif stype == "npm" and not isinstance(src.get("version"), str):
            msg = f"{ename} has no npm `version` — installs float to latest"
            out.append(_mf(
                "marketplace-unpinned-source", "warning", world,
                msg,
                f"npm-unpinned:{ename}",
            ))
        elif stype == "archive" and not isinstance(src.get("sha256"), str):
            msg = f"{ename} archive has no `sha256` — contents unverifiable"
            out.append(_mf(
                "marketplace-unpinned-source", "warning", world,
                msg,
                f"archive-unpinned:{ename}",
            ))
    return out


@check("marketplace-command-source")
def marketplace_command_source(world: World, config: Config) -> list[Finding]:
    out = []
    for i, entry in enumerate(_entries(world)):
        if not isinstance(entry, dict):
            continue
        src = entry.get("source")
        if isinstance(src, dict) and src.get("source") == "command":
            cmd = src.get("command")
            shown = one_line(cmd, 120) if isinstance(cmd, str) else "<missing>"
            out.append(_mf(
                "marketplace-command-source", "warning", world,
                f"{_ename(entry, i)} installs by RUNNING a shell command: "
                f"{shown!r} — review it before trusting this marketplace",
                f"command:{_ename(entry, i)}",
            ))
    return out


@check("marketplace-entry-missing")
def marketplace_entry_missing(world: World, config: Config) -> list[Finding]:
    mp = world.marketplace
    out = []
    for i, entry in enumerate(_entries(world)):
        if not isinstance(entry, dict):
            continue
        src = entry.get("source")
        if not isinstance(src, str):
            continue
        base = Path(mp.root)
        meta = mp.data.get("metadata")
        plugin_root = meta.get("pluginRoot") if isinstance(meta, dict) else None
        if isinstance(plugin_root, str):
            base = base / plugin_root
        resolved = base / src
        if not resolved.is_dir():
            out.append(_mf(
                "marketplace-entry-missing", "error", world,
                f"{_ename(entry, i)} points at {src!r} but "
                f"{resolved} does not exist",
                f"missing:{_ename(entry, i)}:{src}",
            ))
    return out
