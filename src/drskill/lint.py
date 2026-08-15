"""drskill lint: check one authorable unit (an Agent Plugins plugin, a
skill, or an MCP config file) against its standard and drskill's checks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from drskill.checks import run_checks
from drskill.discovery import _find_broken_symlinks
from drskill.ledger import Config
from drskill.mcp import _servers_from_map
from drskill.models import BrokenSymlink, Finding, PluginManifest, PluginMcpFile
from drskill.resolution import World, make_contributor

_ACCEPTS = (
    "drskill lint takes a plugin directory (with plugin.json or "
    ".claude-plugin/plugin.json), a skill directory or SKILL.md file, a "
    "marketplace directory or marketplace.json file, or an MCP config "
    "JSON file"
)
_MCP_SCHEMA_RE = re.compile(r"agent-plugins\.org/schemas/[^/]+/mcp\.schema\.json$")


class LintUsageError(Exception):
    pass


def _plugin_target(p: Path) -> LintTarget:
    return LintTarget(
        kind="plugin", path=p, plugin_flavor="agent-plugins",
        dual_manifest=(p / ".claude-plugin" / "plugin.json").is_file(),
    )


class LintTarget(BaseModel):
    kind: Literal["plugin", "skill", "mcp", "marketplace"]
    path: Path
    mcp_flavor: Literal["agent-plugins", "harness"] | None = None
    plugin_flavor: Literal["agent-plugins", "claude-code"] | None = None
    dual_manifest: bool = False


def classify(path: Path, forced: str | None = None) -> LintTarget:
    p = path.expanduser()
    if not p.exists():
        raise LintUsageError(f"{path} does not exist; {_ACCEPTS}")
    if forced == "plugin":
        if p.is_dir() and (p / "plugin.json").is_file():
            return _plugin_target(p)
        if p.is_dir() and (p / ".claude-plugin" / "plugin.json").is_file():
            return LintTarget(kind="plugin", path=p, plugin_flavor="claude-code")
        raise LintUsageError(
            f"{path} is not a plugin directory (no plugin.json or "
            ".claude-plugin/plugin.json)"
        )
    if forced == "marketplace":
        if p.is_file() and p.name == "marketplace.json":
            return LintTarget(kind="marketplace", path=p)
        if p.is_dir() and (p / ".claude-plugin" / "marketplace.json").is_file():
            return LintTarget(kind="marketplace", path=p)
        raise LintUsageError(
            f"{path} is not a marketplace (no marketplace.json or "
            ".claude-plugin/marketplace.json)"
        )
    if forced == "skill":
        if p.is_file():
            if p.name != "SKILL.md":
                raise LintUsageError(
                    f"{path} is not a skill (a skill file target must be "
                    "named SKILL.md)"
                )
            return LintTarget(kind="skill", path=p)
        if not (p / "SKILL.md").is_file():
            raise LintUsageError(f"{path} is not a skill (no SKILL.md)")
        return LintTarget(kind="skill", path=p)
    if forced == "mcp":
        if not p.is_file():
            raise LintUsageError(f"{path} is not an MCP config file")
        return _classify_json(p)
    if p.is_dir():
        if (p / "plugin.json").is_file():
            return _plugin_target(p)
        if (p / ".claude-plugin" / "plugin.json").is_file():
            return LintTarget(kind="plugin", path=p, plugin_flavor="claude-code")
        if (p / "SKILL.md").is_file():
            return LintTarget(kind="skill", path=p)
        if (p / ".claude-plugin" / "marketplace.json").is_file():
            return LintTarget(kind="marketplace", path=p)
        raise LintUsageError(
            f"{path} has no plugin.json, .claude-plugin/, or SKILL.md; {_ACCEPTS}"
        )
    if p.name == "SKILL.md":
        return LintTarget(kind="skill", path=p)
    if p.name == "marketplace.json":
        return LintTarget(kind="marketplace", path=p)
    if p.suffix == ".json" or p.name.startswith("."):
        return _classify_json(p)
    raise LintUsageError(f"{path} is not a lintable file; {_ACCEPTS}")


def _classify_json(p: Path) -> LintTarget:
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        data = None
    sibling_manifest = (p.parent / "plugin.json").is_file()
    schema = data.get("$schema") if isinstance(data, dict) else None
    if isinstance(schema, str) and _MCP_SCHEMA_RE.search(schema):
        return LintTarget(kind="mcp", path=p, mcp_flavor="agent-plugins")
    if sibling_manifest and p.name == "mcp.json":
        return LintTarget(kind="mcp", path=p, mcp_flavor="agent-plugins")
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        return LintTarget(kind="mcp", path=p, mcp_flavor="harness")
    if data is None and p.name in ("mcp.json", ".mcp.json"):
        flavor = "agent-plugins" if sibling_manifest else "harness"
        return LintTarget(kind="mcp", path=p, mcp_flavor=flavor)
    raise LintUsageError(f"{p} is not a recognized MCP config; {_ACCEPTS}")


def build_lint_world(target: LintTarget) -> World:
    world = World()
    if target.kind == "skill":
        f = target.path if target.path.is_file() else target.path / "SKILL.md"
        _add_skill(world, f)
    elif target.kind == "plugin":
        root = target.path.resolve()
        world.plugin = _parse_manifest(root)
        skills_dir = root / "skills"
        if skills_dir.is_dir():
            for child in sorted(skills_dir.iterdir()):
                f = child / "SKILL.md"
                if child.is_dir() and f.is_file():
                    _add_skill(world, f)
        mcp_path = root / "mcp.json"
        if mcp_path.is_file():
            _add_mcp_file(world, mcp_path, root, provisional=False)
    else:
        p = target.path.resolve()
        if target.mcp_flavor == "agent-plugins":
            root = p.parent
            provisional = not (root / "plugin.json").is_file()
            _add_mcp_file(world, p, root, provisional)
        else:
            _add_harness_mcp(world, p)
    return world


def _add_skill(world: World, skill_file: Path) -> None:
    c, unreadable = make_contributor(skill_file)
    if c is None:
        world.unreadable.append(("", str(skill_file)))
        return
    world.unreadable += [("", p) for p in unreadable]
    world.contributors[c.id] = c
    for b in _find_broken_symlinks(skill_file.parent, recursive=True):
        world.broken_symlinks.append(BrokenSymlink(harness="", path=b))


def _parse_manifest(root: Path) -> PluginManifest:
    path = root / "plugin.json"
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return PluginManifest(root=str(root), raw_text=text, parse_error=str(e))
    if not isinstance(raw, dict):
        return PluginManifest(
            root=str(root), raw_text=text, parse_error="top level is not an object"
        )
    return PluginManifest(
        root=str(root),
        raw=raw,
        raw_text=text,
        name=raw.get("name") if isinstance(raw.get("name"), str) else None,
        version=raw.get("version") if isinstance(raw.get("version"), str) else None,
        schema_url=raw.get("$schema") if isinstance(raw.get("$schema"), str) else None,
    )


def _add_mcp_file(world: World, path: Path, root: Path, provisional: bool) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        world.mcp_config_errors.append(("", str(path), str(e), True))
        world.plugin_mcp = PluginMcpFile(
            path=str(path), text=text, root=str(root), provisional_root=provisional
        )
        return
    world.plugin_mcp = PluginMcpFile(
        path=str(path), text=text, data=data if isinstance(data, dict) else None,
        root=str(root), provisional_root=provisional,
    )
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        world.mcp_servers = _servers_from_map(
            servers, harness="", scope="project", source=path, in_project=True
        )


def _add_harness_mcp(world: World, path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        world.mcp_config_errors.append(("", str(path), str(e), True))
        return
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        world.mcp_servers = _servers_from_map(
            servers, harness="", scope="project", source=path, in_project=True
        )


SKILL_CONTENT_CHECKS = [
    "spec-invalid-frontmatter", "spec-name-mismatch", "spec-missing-description",
    "spec-description-too-long", "frontmatter-angle-brackets",
    "missing-activation", "generic-description", "opposing-imperatives",
    "description-overlap",
    "budget-catalog-tokens", "budget-body-tokens",
    "injection-unicode", "injection-encoded-blob", "injection-override",
    "injection-remote-fetch", "injection-egress", "injection-credential-read",
    "injection-mandatory-script",
    "injection-shell-unreviewed", "injection-shell-dangerous",
    "broken-symlink", "unreadable-skill",
]
PLUGIN_SPEC_CHECKS = [
    "plugin-manifest-invalid", "plugin-name-invalid",
    "plugin-manifest-unknown-field", "plugin-schema-unknown",
    "plugin-skill-undiscoverable", "plugin-path-escape",
    "plugin-extension-hygiene",
]
MCP_SPEC_CHECKS = ["mcp-spec-invalid", "mcp-spec-placeholder"]
MCP_STATIC_CHECKS = ["mcp-config-invalid", "mcp-secret-in-config", "mcp-unpinned-server"]
MCP_CONNECT_CHECKS = [
    "mcp-connect-failed", "mcp-tool-collision", "mcp-tools-unreviewed",
    "mcp-tool-poisoning",
]


def checks_for(target: LintTarget, mcp_connect: bool) -> list[str]:
    if target.kind == "skill":
        return list(SKILL_CONTENT_CHECKS)
    if target.kind == "plugin":
        ids = (SKILL_CONTENT_CHECKS + ["exact-duplicate", "near-duplicate"]
               + PLUGIN_SPEC_CHECKS + MCP_SPEC_CHECKS + MCP_STATIC_CHECKS)
    elif target.mcp_flavor == "agent-plugins":
        ids = MCP_SPEC_CHECKS + MCP_STATIC_CHECKS
    else:
        # No spec to enforce; the generic URL and dead-command checks stand in.
        ids = MCP_STATIC_CHECKS + ["mcp-insecure-url", "mcp-dead-server"]
    if mcp_connect:
        ids = ids + MCP_CONNECT_CHECKS
    return ids


def find_config_root(start: Path) -> Path:
    """The nearest directory at or above `start` holding a drskill.toml.
    Falls back to `start` (or its parent for a file) so acks and budgets
    default sanely when the author has no ledger yet."""
    cur = (start if start.is_dir() else start.parent).resolve()
    for d in [cur, *cur.parents]:
        if (d / "drskill.toml").is_file():
            return d
    return cur


def run_lint(
    target: LintTarget,
    config: Config,
    config_root: Path,
    home: Path,
    mcp_connect: bool = False,
    judge=None,
    rewriter=None,
    max_calls: int | None = 25,
    progress=None,
) -> tuple[World, list[Finding]]:
    if progress:
        progress(f"reading {target.kind}")
    world = build_lint_world(target)
    if mcp_connect and world.mcp_servers:
        from drskill import mcp_connect as mcpc
        from drskill.pipeline import _add_tool_contributors

        sdir = mcpc.snapshot_dir(config_root, home, False)
        _, world.mcp_connect_failures = mcpc.run_handshakes(
            world.mcp_servers, sdir, progress=progress
        )
        _add_tool_contributors(world, mcpc.load_snapshots(sdir))
        world.mcp_approved = mcpc.load_snapshots(mcpc.approved_dir(sdir))
    findings = run_checks(
        world, config, checks_for(target, mcp_connect), progress=progress
    )
    from drskill import deep

    cdir = deep.cache_dir(config_root, home, False)
    cache = deep.load_cache(cdir)
    acked_fps = {a.fingerprint for a in config.ack}
    if judge is not None:
        # Acked clusters never spend the call budget; the user already ruled.
        active = [f for f in findings if f.fingerprint not in acked_fps]
        deep.judge_pairs(
            world, active, cache, cdir, judge, config.deep.model, max_calls,
            rewriter=rewriter, progress=progress,
        )
    findings = deep.apply_verdicts(world, findings, cache, acked_fps)
    # Lint has no harness context; strip any attribution a shared check set.
    findings = [
        f.model_copy(update={"harnesses": []}) if f.harnesses else f
        for f in findings
    ]
    return world, findings
