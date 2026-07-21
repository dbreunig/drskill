"""Static MCP checks: read the discovered server entries, launch nothing."""

from __future__ import annotations

import hashlib
import shutil
from collections import defaultdict
from pathlib import Path

from drskill.checks import check
from drskill.ledger import Config
from drskill.mcp import MCPServer
from drskill.models import Finding
from drskill.resolution import World

_PIN_RUNNERS = {"npx", "uvx", "bunx", "pnpm"}
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")


def _fp(check_id: str, parts: list[str]) -> str:
    payload = "|".join([check_id, *sorted(parts)])
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _finding(check_id, severity, s: MCPServer, message, fix=None, fp_parts=None):
    return Finding(
        check_id=check_id, severity=severity,
        contributors=[s.source], contributor_names=[s.name],
        harnesses=[s.harness], message=message,
        fix_commands=fix or [],
        fingerprint=_fp(check_id, fp_parts or [s.name, s.config_hash]),
    )


@check("mcp-config-invalid")
def config_invalid(world: World, config: Config) -> list[Finding]:
    out = []
    for hid, msg in world.mcp_config_errors:
        path = msg.split(":", 1)[0]
        out.append(Finding(
            check_id="mcp-config-invalid", severity="error",
            contributors=[path], contributor_names=[],
            harnesses=[hid],
            message=f"MCP config does not parse: {msg}",
            fix_commands=[f"Fix the syntax in {path}"],
            fingerprint=_fp("mcp-config-invalid", [hid, path]),
        ))
    return out


@check("mcp-secret-in-config")
def secret_in_config(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        if not s.suspect_env:
            continue
        names = ", ".join(s.suspect_env)
        sev = "error" if s.scope == "project" else "warning"
        where = "a committable project file" if s.scope == "project" else "a user-scope file"
        out.append(_finding(
            "mcp-secret-in-config", sev, s,
            f"server '{s.name}' holds credential-shaped values in {where}: "
            f"{names}\n        {s.source}",
            fix=[f"Move {names} out of {s.source} into your environment or a secret manager"],
            fp_parts=[s.name, s.source, *s.suspect_env],
        ))
    return out


@check("mcp-unpinned-server")
def unpinned_server(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        # Runners are often invoked through a version-manager shim's full
        # path (found on a real machine: ~/.asdf/shims/npx), so match on
        # the basename.
        runner = Path(s.command).name if s.command else ""
        if runner not in _PIN_RUNNERS:
            continue
        pkgs = [a for a in s.args if not a.startswith("-") and a not in ("dlx", "exec")]
        if not pkgs:
            continue
        pkg = pkgs[0]
        base, _, ver = pkg.rpartition("@")
        pinned = bool(base) and ver not in ("", "latest")
        if not pinned:
            out.append(_finding(
                "mcp-unpinned-server", "warning", s,
                f"server '{s.name}' runs an unpinned package "
                f"('{s.command} {' '.join(s.args)}'): whatever publishes next runs next"
                f"\n        {s.source}",
                fix=[f"Pin it, e.g. {s.command} {pkg.removesuffix('@latest')}@<version>"],
            ))
    return out


@check("mcp-insecure-url")
def insecure_url(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        if s.url and s.url.startswith("http://"):
            host = s.url.removeprefix("http://").split("/", 1)[0].split(":", 1)[0]
            if host in _LOCAL_HOSTS:
                continue
            out.append(_finding(
                "mcp-insecure-url", "warning", s,
                f"server '{s.name}' uses plaintext http: {s.url}\n        {s.source}",
                fix=["Use https for remote MCP servers"],
            ))
    return out


@check("mcp-dead-server")
def dead_server(world: World, config: Config) -> list[Finding]:
    out = []
    for s in world.mcp_servers:
        if s.transport != "stdio" or not s.command:
            continue
        p = Path(s.command)
        if not p.is_absolute() and "/" in s.command:
            # A relative path resolves against a launch cwd we cannot know
            # statically (found on a real machine: a codex app-bundle
            # helper). Unverifiable is not dead.
            continue
        exists = p.exists() if p.is_absolute() else shutil.which(s.command) is not None
        if not exists:
            out.append(_finding(
                "mcp-dead-server", "error", s,
                f"server '{s.name}' command not found: {s.command}\n        {s.source}",
                fix=[f"Install {s.command} or remove the entry from {s.source}"],
            ))
    return out


@check("mcp-shadowed-server")
def shadowed_server(world: World, config: Config) -> list[Finding]:
    out = []
    per = defaultdict(list)
    for s in world.mcp_servers:
        per[(s.harness, s.name)].append(s)
    for (hid, name), entries in sorted(per.items()):
        scopes = {e.scope for e in entries}
        if scopes != {"project", "user"}:
            continue
        if len({e.config_hash for e in entries}) == 1:
            continue  # identical duplicate config: harmless
        h = world.harnesses.get(hid)
        winner = "user" if h and h.search_order == "global-first" else "project"
        srcs = "".join(
            f"\n        {e.scope}: {e.source}"
            for e in sorted(entries, key=lambda e: e.scope)
        )
        out.append(_finding(
            "mcp-shadowed-server", "warning", entries[0],
            f"'{name}' is configured in both scopes of {hid} with different "
            f"settings; the {winner} entry wins{srcs}",
            fix=[f"Keep one entry for '{name}' in {hid}"],
            fp_parts=[name, hid, *sorted(e.config_hash for e in entries)],
        ))
    return out


@check("mcp-diverged-server")
def diverged_server(world: World, config: Config) -> list[Finding]:
    out = []
    per = defaultdict(list)
    for s in world.mcp_servers:
        per[s.name].append(s)
    for name, entries in sorted(per.items()):
        variants: dict[str, list[MCPServer]] = {}
        for e in entries:
            variants.setdefault(e.config_hash, []).append(e)
        if len(variants) < 2:
            continue
        harnesses = sorted({e.harness for e in entries})
        if len(harnesses) < 2:
            continue  # same-harness drift is the shadow check's job
        fields = _differing_fields([v[0] for v in variants.values()])
        lines = "".join(
            f"\n        {e.harness} ({e.scope}): {e.source}"
            for v in variants.values() for e in v
        )
        out.append(Finding(
            check_id="mcp-diverged-server", severity="warning",
            contributors=sorted({e.source for e in entries}),
            contributor_names=[name], harnesses=harnesses,
            message=(
                f"'{name}' is configured differently across harnesses; "
                f"differing fields: {', '.join(fields)}{lines}"
            ),
            fix_commands=[
                f"Align '{name}' across harnesses, or rename intentionally different servers"
            ],
            fingerprint=_fp("mcp-diverged-server", [name, *sorted(variants)]),
        ))
    return out


def _differing_fields(variants: list[MCPServer]) -> list[str]:
    fields = []
    for attr in ("command", "args", "url", "env_names"):
        if len({repr(getattr(v, attr)) for v in variants}) > 1:
            fields.append(attr)
    return fields or ["config"]
