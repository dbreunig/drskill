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
        return [], unreadable
    enabled_map: dict = {}
    settings = _read_json(home / ".claude" / "settings.json", unreadable)
    if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
        enabled_map = settings["enabledPlugins"]
    out: list[InstalledPlugin] = []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
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


ADAPTERS: dict[str, Callable[[Path, Path], tuple[list[InstalledPlugin], list[str]]]] = {
    "claude-code": _claude_code,
}


def discover_plugins(
    harness_id: str, home: Path, project_root: Path
) -> tuple[list[InstalledPlugin], list[str]]:
    adapter = ADAPTERS.get(harness_id)
    if adapter is None:
        return [], []
    return adapter(home, project_root)
