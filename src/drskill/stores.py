"""Plugin/extension install stores: which suites a harness has installed
and where their ACTIVE skills live.

Each adapter reads only its harness's own state files and never globs for
active versions -- stores retain stale version dirs (observed live on
claude-code) and record enabled/disabled state that determines whether
skills load at all. Store facts are dated per adapter; see
docs/superpowers/specs/2026-08-14-plugin-stores-design.md.

Trust decision: installPath/cache_path values in state files are followed
verbatim after a string type check. A hostile state file could point one
at a huge tree and slow discovery to a crawl; drskill treats the user's
own harness state as trusted for WHERE to read (it validates shapes, not
intent), because the state files live beside the very configs every
harness already executes. Bounding roots to each store directory would
also break legitimate out-of-store installs (codex "local" version dirs).
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


def _read_jsonc(path: Path, unreadable: list[str]):
    """JSON preceded by // comment lines (copilot's config.json style)."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        body = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("//")
        )
        return json.loads(body)
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
        if str(state_path) not in unreadable:
            unreadable.append(str(state_path))
        return [], unreadable
    for key, installs in sorted(plugins.items()):
        if not isinstance(installs, list):
            continue
        name, _, marketplace = key.partition("@")
        if not name:
            continue  # e.g. key "@mkt": empty name would defeat suite attribution
        for inst in installs:
            if not isinstance(inst, dict):
                continue
            install_path = inst.get("installPath")
            if install_path is not None and not isinstance(install_path, str):
                if str(state_path) not in unreadable:
                    unreadable.append(str(state_path))
                continue
            if not install_path:
                continue
            scope = inst.get("scope")
            if scope == "user":
                pscope, ppath = "user", None
            elif scope == "local":
                pp = inst.get("projectPath")
                if pp is not None and not isinstance(pp, str):
                    if str(state_path) not in unreadable:
                        unreadable.append(str(state_path))
                    continue
                if not pp:
                    continue
                try:
                    if Path(pp).resolve() != project_root.resolve():
                        continue
                except (ValueError, OSError):
                    if str(state_path) not in unreadable:
                        unreadable.append(str(state_path))
                    continue
                pscope, ppath = "project", Path(pp)
            else:
                continue
            version = inst.get("version")
            out.append(InstalledPlugin(
                harness="claude-code",
                name=name,
                marketplace=marketplace or None,
                version=str(version) if version is not None else None,
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
    # isascii() guards against non-ASCII digits (e.g. "²" superscript
    # two) that satisfy str.isdigit() but make int() raise ValueError.
    return [int(p) if p.isascii() and p.isdigit() else 0 for p in v.split(".")], v


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
    if plugins_tbl is None:
        return [], unreadable  # no [plugins] table is normal (config.toml
        # is also used for MCP/model settings); only present-but-wrong-shape
        # counts as unreadable.
    if not isinstance(plugins_tbl, dict):
        if str(config_path) not in unreadable:
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
                paths_val = manifest.get("paths")
                declared = paths_val.get("skills") if isinstance(paths_val, dict) else None
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


# --- gemini-cli -------------------------------------------------------------
# Source-verified 2026-08-14 (google-gemini/gemini-cli commit c0d1924):
# store at ~/.gemini/extensions/<name>/ with identity in
# gemini-extension.json (required name, version; extensions/storage.ts:23-39,
# extension.ts:24-26); skills in a FIXED skills/ subdir loaded
# NON-recursively -- glob exactly SKILL.md + */SKILL.md
# (extension-manager.ts:921-922, skillLoader.ts:127). Enablement in
# extension-enablement.json: {name: {overrides: [rule, ...]}}; default
# enabled, rules iterate in file order and each MATCHING rule sets
# enabled = not-disable; "!" prefix = disable, trailing "*" = include
# subdirs, base rule is slash-wrapped; exact rules match only that dir
# (extensionEnablement.ts:146-178, 35-43, 102-108). Missing or corrupt
# file = everything enabled (:189-202). Extension skills rank below user
# and workspace skills (skillManager.ts:54-99), which discovery encodes
# by appending plugin roots after native paths.
def _gemini_enabled(name: str, config: dict, project_root: Path) -> bool:
    entry = config.get(name)
    overrides = entry.get("overrides") if isinstance(entry, dict) else None
    if not isinstance(overrides, list):
        return True
    cwd = "/" + str(project_root.resolve()).replace("\\", "/").strip("/") + "/"
    enabled = True
    for rule in overrides:
        if not isinstance(rule, str) or not rule:
            continue
        disable = rule.startswith("!")
        r = rule[1:] if disable else rule
        subdirs = r.endswith("*")
        base = r[:-1] if subdirs else r
        if cwd == base or (subdirs and cwd.startswith(base)):
            enabled = not disable
    return enabled


def _gemini_cli(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    ext_root = home / ".gemini" / "extensions"
    if not ext_root.is_dir():
        return [], unreadable
    enablement_path = ext_root / "extension-enablement.json"
    enablement = _read_json(enablement_path, unreadable)
    if not isinstance(enablement, dict):
        if enablement is not None and str(enablement_path) not in unreadable:
            unreadable.append(str(enablement_path))
        enablement = {}
    out: list[InstalledPlugin] = []
    for ext_dir in sorted(p for p in ext_root.iterdir() if p.is_dir()):
        manifest_path = ext_dir / "gemini-extension.json"
        manifest = _read_json(manifest_path, unreadable)
        if not isinstance(manifest, dict) or not manifest.get("name"):
            if manifest is not None and str(manifest_path) not in unreadable:
                unreadable.append(str(manifest_path))
            continue
        name = str(manifest["name"])
        out.append(InstalledPlugin(
            harness="gemini-cli",
            name=name,
            marketplace=None,
            version=str(manifest["version"]) if manifest.get("version") else None,
            scope="user",
            skills_roots=[ext_dir / "skills"],
            enabled=_gemini_enabled(name, enablement, project_root),
            recursive=False,
            evidence=manifest_path,
        ))
    return out, unreadable


# --- copilot ----------------------------------------------------------------
# Empirical 2026-08-14 (GitHub Copilot CLI 1.0.80, sandboxed install probe):
# store at ~/.copilot/installed-plugins/<marketplace>/<plugin>/ (whole
# plugin copied once, unversioned dirs) with skills under skills/; state in
# ~/.copilot/config.json (JSONC: comment lines precede the JSON) --
# installedPlugins[] with name/marketplace/version/cache_path -- and
# ~/.copilot/settings.json enabledPlugins keyed "name@marketplace".
# Collision rank probed stable: project > personal > plugin, which
# discovery encodes by appending plugin roots after native paths.
# Recursion inside a plugin's skills dir was not probed; harness default
# (recursive) assumed.
def _copilot(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    config_path = home / ".copilot" / "config.json"
    config = _read_jsonc(config_path, unreadable)
    if not isinstance(config, dict):
        if config is not None and str(config_path) not in unreadable:
            unreadable.append(str(config_path))
        return [], unreadable
    installed = config.get("installedPlugins")
    if not isinstance(installed, list):
        if installed is not None:
            if str(config_path) not in unreadable:
                unreadable.append(str(config_path))
        return [], unreadable
    enabled_map: dict = {}
    settings = _read_json(home / ".copilot" / "settings.json", unreadable)
    if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
        enabled_map = settings["enabledPlugins"]
    out: list[InstalledPlugin] = []
    for entry in installed:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"])
        marketplace = entry.get("marketplace")
        cache_path = entry.get("cache_path")
        if cache_path is not None and not isinstance(cache_path, str):
            if str(config_path) not in unreadable:
                unreadable.append(str(config_path))
            continue
        root = (Path(cache_path) if cache_path
                else home / ".copilot" / "installed-plugins" / str(marketplace or "") / name)
        key = f"{name}@{marketplace}" if marketplace else name
        out.append(InstalledPlugin(
            harness="copilot",
            name=name,
            marketplace=str(marketplace) if marketplace else None,
            version=str(entry["version"]) if entry.get("version") else None,
            scope="user",
            skills_roots=[root / "skills"],
            enabled=enabled_map.get(key) is not False,
            recursive=True,
            evidence=config_path,
        ))
    return out, unreadable


# --- droid ------------------------------------------------------------------
# Empirical 2026-08-14 (@factory/cli 0.196.0, sandboxed install probe):
# store at ~/.factory/plugins/cache/<mkt>-<hash>/<plugin>-<hash>/<installId>/
# with skills under skills/; one JSON per install under
# ~/.factory/plugins/installed_plugins/ ({schemaVersion, pluginId:
# "name@marketplace", entry: {scope, installPath, version}}). droid has no
# skill-enumeration verb, so THAT these cached skills load (and at what
# rank) is BEST-EFFORT, resting on prime-radiant-inc/everyharness's
# container install checks. Scopes other than "user" are skipped (only
# "user" observed).
def _droid(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    if not state_dir.is_dir():
        return [], unreadable
    out: list[InstalledPlugin] = []
    for state_path in sorted(state_dir.glob("*.json")):
        data = _read_json(state_path, unreadable)
        if not isinstance(data, dict):
            if data is not None and str(state_path) not in unreadable:
                unreadable.append(str(state_path))
            continue
        plugin_id = data.get("pluginId")
        entry = data.get("entry")
        if not isinstance(plugin_id, str) or not isinstance(entry, dict):
            if str(state_path) not in unreadable:
                unreadable.append(str(state_path))
            continue
        if entry.get("scope") != "user":
            continue
        install_path = entry.get("installPath")
        if install_path is not None and not isinstance(install_path, str):
            if str(state_path) not in unreadable:
                unreadable.append(str(state_path))
            continue
        if not install_path:
            continue
        name, _, marketplace = plugin_id.partition("@")
        out.append(InstalledPlugin(
            harness="droid",
            name=name,
            marketplace=marketplace or None,
            version=str(entry["version"]) if entry.get("version") else None,
            scope="user",
            skills_roots=[Path(install_path) / "skills"],
            enabled=entry.get("enabled") is not False,
            recursive=True,
            evidence=state_path,
        ))
    return out, unreadable


ADAPTERS: dict[str, Callable[[Path, Path], tuple[list[InstalledPlugin], list[str]]]] = {
    "claude-code": _claude_code,
    "codex": _codex,
    "gemini-cli": _gemini_cli,
    "copilot": _copilot,
    "droid": _droid,
}

# Best-known primary state path per harness, used only by the belt-and-braces
# fallback below when an adapter raises something its own guards missed.
_PRIMARY_STATE_PATH: dict[str, Callable[[Path], Path]] = {
    "claude-code": lambda home: home / ".claude" / "plugins" / "installed_plugins.json",
    "codex": lambda home: home / ".codex" / "config.toml",
    "gemini-cli": lambda home: home / ".gemini" / "extensions" / "extension-enablement.json",
    "copilot": lambda home: home / ".copilot" / "config.json",
    "droid": lambda home: home / ".factory" / "plugins" / "installed_plugins",
}


def discover_plugins(
    harness_id: str, home: Path, project_root: Path
) -> tuple[list[InstalledPlugin], list[str]]:
    adapter = ADAPTERS.get(harness_id)
    if adapter is None:
        return [], []
    try:
        return adapter(home, project_root)
    except Exception:
        # Never let an adapter exception escape scanning ("never a crash,
        # never a guess"): degrade to unreadable rather than propagating.
        path_fn = _PRIMARY_STATE_PATH.get(harness_id)
        if path_fn is not None:
            try:
                return [], [str(path_fn(home))]
            except Exception:
                return [], []
        return [], []
