"""Plugin/extension install stores: which suites a harness has installed
and where their ACTIVE skills live.

Each adapter reads only its harness's own state files and never globs for
active versions -- stores retain stale version dirs (observed live on
claude-code) and record enabled/disabled state that determines whether
skills load at all. Store facts are dated per adapter; see
docs/superpowers/specs/2026-08-14-plugin-stores-design.md.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field


class InstalledPlugin(BaseModel):
    harness: str
    name: str
    marketplace: str | None = None
    version: str | None = None
    scope: Literal["user", "project"] = "user"
    project_path: Path | None = None
    skills_roots: list[Path] = Field(default_factory=list)
    enabled: bool = True
    recursive: bool = True
    evidence: Path

    @property
    def provenance_source(self) -> str:
        s = self.name
        if self.marketplace:
            s += f"@{self.marketplace}"
        if self.version:
            s += f"=={self.version}"
        return s


def _read_json(path: Path, unreadable: list[str]):
    """Parse a JSON file; None when missing, and None + an unreadable
    entry when present but unparseable."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        unreadable.append(str(path))
        return None


# --- claude-code -----------------------------------------------------------
# Observed live 2026-08-14: cache/<marketplace>/<plugin>/<version>/skills
# with STALE version dirs retained; ~/.claude/plugins/installed_plugins.json
# maps "name@marketplace" -> a list of installs, each {scope: "user" |
# "local"+projectPath, installPath, version}; ~/.claude/settings.json
# enabledPlugins maps the same key -> bool. A missing enabledPlugins key
# counts as enabled (installed implies usable; for a scanner, over-scanning
# is safer than missing surface). Claude Code namespaces plugin skills
# ("plugin:skill"), which resolution accounts for separately.
def _claude_code(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    state_path = home / ".claude" / "plugins" / "installed_plugins.json"
    data = _read_json(state_path, unreadable)
    if not isinstance(data, dict):
        if data is not None and str(state_path) not in unreadable:
            unreadable.append(str(state_path))
        return [], unreadable
    enabled_map: dict = {}
    settings = _read_json(home / ".claude" / "settings.json", unreadable)
    if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
        enabled_map = settings["enabledPlugins"]
    out: list[InstalledPlugin] = []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        unreadable.append(str(state_path))
        return [], unreadable
    for key, installs in sorted(plugins.items()):
        if not isinstance(installs, list):
            continue
        name, _, marketplace = key.partition("@")
        for inst in installs:
            if not isinstance(inst, dict):
                continue
            install_path = inst.get("installPath")
            if not install_path:
                continue
            scope = inst.get("scope")
            if scope == "user":
                pscope, ppath = "user", None
            elif scope == "local":
                pp = inst.get("projectPath")
                if not pp or Path(pp).resolve() != project_root.resolve():
                    continue
                pscope, ppath = "project", Path(pp)
            else:
                continue
            out.append(InstalledPlugin(
                harness="claude-code",
                name=name,
                marketplace=marketplace or None,
                version=inst.get("version"),
                scope=pscope,
                project_path=ppath,
                skills_roots=[Path(install_path) / "skills"],
                enabled=enabled_map.get(key) is not False,
                recursive=True,
                evidence=state_path,
            ))
    return out, unreadable


# --- codex ------------------------------------------------------------------
# Source-verified 2026-08-14 (openai/codex commit 5bc8da6, current main):
# cache at ~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/
# (core-plugins/src/store.rs:26-27,131-139); enablement in config.toml
# [plugins."name@marketplace"] enabled (config/src/plugin_edit.rs:21-34),
# disabled plugins' skills never load (core-plugins/src/loader.rs:843-845);
# active version prefers the "local" dir, else highest (store.rs:168-189);
# skills roots from the manifest's paths.skills, default skills/, plus
# .codex-plugin/migrated-command-skills (loader.rs:1059-1087). Legacy
# manifests scan recursively (depth <= 6), agent-plugins root plugin.json
# scans direct children only -- approximated here as recursive True/False.
# The $CODEX_HOME env override is ignored, matching how drskill already
# reads ~/.codex/config.toml for MCP. drskill's no-shadowing handling for
# codex (search_order "none") extends to plugin skills, matching source
# (ext/skills/src/loader/host_merge.rs:232-233).
_CODEX_LEGACY_MANIFESTS = (
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
)


def _codex_version_key(v: str):
    # Numeric-aware ordering approximating codex's compare; "1.10.0" beats
    # "1.9.0". Non-numeric parts rank as 0 with the raw string as tiebreak.
    return [int(p) if p.isdigit() else 0 for p in v.split(".")], v


def _codex(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    config_path = home / ".codex" / "config.toml"
    if not config_path.is_file():
        return [], unreadable
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [str(config_path)]
    plugins_tbl = config.get("plugins")
    if not isinstance(plugins_tbl, dict):
        unreadable.append(str(config_path))
        return [], unreadable
    out: list[InstalledPlugin] = []
    cache = home / ".codex" / "plugins" / "cache"
    for key, entry in sorted(plugins_tbl.items()):
        name, _, marketplace = key.partition("@")
        base = cache / (marketplace or "") / name
        if not base.is_dir():
            continue  # stale config entry, nothing installed
        versions = sorted(d.name for d in base.iterdir() if d.is_dir())
        if not versions:
            continue
        version = ("local" if "local" in versions
                   else max(versions, key=_codex_version_key))
        root = base / version
        manifest_path = root / "plugin.json"
        recursive = False  # agent-plugins root manifest: shallow scan
        if not manifest_path.is_file():
            recursive = True  # legacy manifest flavors scan recursively
            for rel in _CODEX_LEGACY_MANIFESTS:
                if (root / rel).is_file():
                    manifest_path = root / rel
                    break
            else:
                manifest_path = None
        skills_rel = ["skills"]
        if manifest_path is not None:
            manifest = _read_json(manifest_path, unreadable)
            if isinstance(manifest, dict):
                declared = (manifest.get("paths") or {}).get("skills")
                if isinstance(declared, list) and declared:
                    skills_rel = [s for s in declared if isinstance(s, str)]
        roots = [root / rel for rel in skills_rel]
        migrated = root / ".codex-plugin" / "migrated-command-skills"
        if migrated.is_dir():
            roots.append(migrated)
        enabled = entry.get("enabled") is not False if isinstance(entry, dict) else True
        out.append(InstalledPlugin(
            harness="codex",
            name=name,
            marketplace=marketplace or None,
            version=version,
            scope="user",
            skills_roots=roots,
            enabled=enabled,
            recursive=recursive,
            evidence=config_path,
        ))
    return out, unreadable


ADAPTERS: dict[str, Callable[[Path, Path], tuple[list[InstalledPlugin], list[str]]]] = {
    "claude-code": _claude_code,
    "codex": _codex,
}


def discover_plugins(
    harness_id: str, home: Path, project_root: Path
) -> tuple[list[InstalledPlugin], list[str]]:
    adapter = ADAPTERS.get(harness_id)
    if adapter is None:
        return [], []
    return adapter(home, project_root)
