"""Agent Plugins 1.0.0 conformance checks for plugin-flavor mcp.json.
These run off the raw parsed file (world.plugin_mcp) because MCPServer
normalizes away the fields the spec constrains (type, cwd, env values,
headers). Parse failures are mcp-config-invalid's job, not ours."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from drskill.checks import check
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World

TRANSPORTS = {"stdio", "streamable-http", "sse"}
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_PLACEHOLDER_RE = re.compile(r"\$\{(PLUGIN_ROOT|PLUGIN_DATA)\}")
_CWD_RE = re.compile(r"^(\./|\$\{PLUGIN_ROOT\}(/|$)|\$\{PLUGIN_DATA\}(/|$))")


def _finding(check_id, severity, world, name, entry, message, reason, fix=None):
    """`reason` is a stable, path-free slug identifying which rule fired
    (e.g. "missing-schema", "env-reserved:API_KEY"). It stands in for
    `message` in the fingerprint payload: messages embed absolute paths
    (via f.path / f.root's provisional-root note), so two checkouts of the
    same mcp.json content at different paths would otherwise fingerprint
    differently and a committed ack would go red on CI."""
    text = json.dumps({name: entry}, sort_keys=True)
    payload = "|".join([check_id, hashlib.sha256(text.encode()).hexdigest(), reason])
    return Finding(
        check_id=check_id, severity=severity,
        contributors=[world.plugin_mcp.path], contributor_names=[name],
        harnesses=[], message=message, fix_commands=fix or [],
        fingerprint="sha256:" + hashlib.sha256(payload.encode()).hexdigest(),
    )


def _entries(world: World):
    f = world.plugin_mcp
    if f is None or f.data is None:
        return None, []
    servers = f.data.get("mcpServers")
    if not isinstance(servers, dict):
        return f, None
    return f, sorted((str(k), v) for k, v in servers.items() if isinstance(v, dict))


@check("mcp-spec-invalid")
def spec_invalid(world: World, config: Config) -> list[Finding]:
    f, entries = _entries(world)
    if f is None:
        return []
    out = []
    root_note = ""
    if f.provisional_root:
        root_note = f" (assuming {f.root} as the plugin root; no plugin.json found)"
    if not isinstance(f.data.get("$schema"), str):
        out.append(_finding("mcp-spec-invalid", "error", world, "mcp.json",
            f.data, f"{f.path} is missing the required $schema string",
            "missing-schema"))
    if entries is None:
        out.append(_finding("mcp-spec-invalid", "error", world, "mcp.json",
            f.data, f"{f.path} 'mcpServers' must be an object",
            "servers-not-object"))
        return out
    for name, entry in entries:
        out += _check_types(world, name, entry)
        t = entry.get("type")
        if t not in TRANSPORTS:
            out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
                f"server '{name}' has transport '{t}'; the spec allows stdio, "
                "streamable-http, or sse, and clients skip this entry",
                "bad-transport"))
            continue
        if t == "stdio":
            out += _check_stdio(world, name, entry, Path(f.root), root_note)
        else:
            out += _check_url(world, name, entry)
    return out


def _check_types(world, name, entry):
    """Field-shape checks that apply regardless of transport."""
    out = []
    args = entry.get("args")
    if args is not None and not (
        isinstance(args, list) and all(isinstance(a, str) for a in args)
    ):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' 'args' must be a list of strings",
            "args-invalid"))
    env = entry.get("env")
    if env is not None and not (
        isinstance(env, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
    ):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' 'env' must map string keys to string values",
            "env-invalid"))
    headers = entry.get("headers")
    if headers is not None and not (
        isinstance(headers, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
    ):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' 'headers' must map string keys to string values",
            "headers-invalid"))
    return out


def _check_stdio(world, name, entry, root: Path, root_note: str):
    out = []
    cmd = entry.get("command")
    if not isinstance(cmd, str) or not cmd:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"stdio server '{name}' is missing a command", "command-missing"))
        return out
    if any(ch.isspace() for ch in cmd):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' command '{cmd}' is not a single token; put "
            "arguments in 'args'", "command-multi-token"))
    elif cmd.startswith("./"):
        if not (root / cmd[2:]).exists():
            out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
                f"server '{name}' command '{cmd}' does not exist inside the "
                f"plugin{root_note}", "command-rel-missing"))
    elif "/" in cmd and not _PLACEHOLDER_RE.search(cmd):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' command '{cmd}' must be a bare executable name "
            "or a ./ plugin-relative path", "command-bare-slash"))
    return out


def _check_url(world, name, entry):
    out = []
    url = entry.get("url")
    if not isinstance(url, str) or not url:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' is missing a url", "url-missing"))
        return out
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' url must be absolute http or https",
            "url-scheme"))
        return out
    if parts.username or parts.password:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' url must not carry user info", "url-userinfo"))
    if parts.fragment:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' url must not carry a fragment", "url-fragment"))
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        out.append(_finding("mcp-spec-invalid", "error", world, name, entry,
            f"server '{name}' uses plain http to a non-loopback host",
            "url-plain-http"))
    return out


@check("mcp-spec-placeholder")
def spec_placeholder(world: World, config: Config) -> list[Finding]:
    f, entries = _entries(world)
    if f is None or entries is None:
        return []
    out = []
    for name, entry in entries:
        cmd = entry.get("command")
        if isinstance(cmd, str) and _PLACEHOLDER_RE.search(cmd):
            out.append(_finding("mcp-spec-placeholder", "error", world, name,
                entry, f"server '{name}' uses a placeholder in 'command'; "
                "placeholders expand only in args, env values, and cwd",
                "placeholder-command"))
        env = entry.get("env")
        if isinstance(env, dict):
            for k in sorted(str(k) for k in env):
                if k in ("PLUGIN_ROOT", "PLUGIN_DATA"):
                    out.append(_finding("mcp-spec-placeholder", "error", world,
                        name, entry, f"server '{name}' env defines reserved "
                        f"name '{k}'; the client provides it",
                        f"env-reserved:{k}"))
                elif _PLACEHOLDER_RE.search(k):
                    out.append(_finding("mcp-spec-placeholder", "error", world,
                        name, entry, f"server '{name}' uses a placeholder in "
                        f"env key '{k}'; placeholders never expand in keys",
                        f"env-key-placeholder:{k}"))
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and entry.get("type") == "stdio":
            if not _CWD_RE.match(cwd):
                out.append(_finding("mcp-spec-placeholder", "error", world,
                    name, entry, f"server '{name}' cwd '{cwd}' must start "
                    "with ./, ${PLUGIN_ROOT}, or ${PLUGIN_DATA}", "cwd-form"))
        headers = entry.get("headers")
        if isinstance(headers, dict):
            for k, v in sorted(headers.items()):
                if isinstance(v, str) and _PLACEHOLDER_RE.search(v):
                    out.append(_finding("mcp-spec-placeholder", "warning",
                        world, name, entry, f"server '{name}' header '{k}' "
                        "contains a placeholder; placeholders do not expand "
                        "in headers, so it is sent literally",
                        f"header-placeholder:{k}"))
    return out
