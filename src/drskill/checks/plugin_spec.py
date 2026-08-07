"""Agent Plugins 1.0.0 conformance checks for plugin.json and the plugin
layout. Every check no-ops when world.plugin is None, so scan never runs
them against harness skills."""

from __future__ import annotations

import json
import re
from pathlib import Path

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.mcp import looks_secret
from drskill.models import Finding
from drskill.resolution import World

SUPPORTED_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
KNOWN_FIELDS = {
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
}
_NAMESPACE_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
# Extension secret scan cap: skip files larger than this.
_SECRET_SCAN_CAP = 256 * 1024


def valid_plugin_name(name: str) -> bool:
    if not 1 <= len(name) <= 64:
        return False
    if not re.fullmatch(r"[a-z0-9.-]+", name):
        return False
    if name[0] in ".-" or name[-1] in ".-":
        return False
    return re.search(r"[.-]{2}", name) is None


def _pf(check_id, severity, world, message, fix=None, key=""):
    return make_finding(
        check_id, severity, [], message, fix_commands=fix or [],
        extra_key=key, fingerprint_texts=[world.plugin.raw_text or key],
    )


@check("plugin-manifest-invalid")
def manifest_invalid(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    where = f"{m.root}/plugin.json"
    if m.parse_error:
        return [_pf(
            "plugin-manifest-invalid", "error", world,
            f"plugin.json does not parse: {m.parse_error}",
            fix=[f"Fix the JSON syntax in {where}"],
        )]
    out = []
    if not isinstance(m.raw.get("$schema"), str):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json is missing the required $schema string; clients "
            "reject the whole plugin",
            fix=[f'Add "$schema": "{SUPPORTED_SCHEMA}" to {where}'], key="$schema"))
    if not isinstance(m.raw.get("name"), str):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json is missing the required name string; clients "
            "reject the whole plugin",
            fix=[f"Add a name field to {where}"], key="name"))
    for field in ("version", "description", "homepage", "repository", "license"):
        v = m.raw.get(field)
        if v is not None and not isinstance(v, str):
            out.append(_pf("plugin-manifest-invalid", "error", world,
                f"plugin.json field '{field}' must be a string",
                fix=[f"Make '{field}' a string in {where}"], key=field))
    kw = m.raw.get("keywords")
    if kw is not None and not (
        isinstance(kw, list) and all(isinstance(k, str) for k in kw)
    ):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json field 'keywords' must be a list of strings",
            fix=[f"Fix 'keywords' in {where}"], key="keywords"))
    author = m.raw.get("author")
    if author is not None and not isinstance(author, dict):
        out.append(_pf("plugin-manifest-invalid", "error", world,
            "plugin.json field 'author' must be an object",
            fix=[f"Fix 'author' in {where}"], key="author"))
    return out


@check("plugin-name-invalid")
def name_invalid(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None or m.name is None:
        return []
    if valid_plugin_name(m.name):
        return []
    return [_pf(
        "plugin-name-invalid", "error", world,
        f"plugin name '{m.name}' breaks the naming rules: 1 to 64 lowercase "
        "letters, digits, hyphens, or periods; first and last alphanumeric; "
        "no doubled separators",
        fix=[f"Rename the plugin in {m.root}/plugin.json"], key=m.name,
    )]


@check("plugin-manifest-unknown-field")
def unknown_field(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None or m.parse_error:
        return []
    out = []
    for field in sorted(set(m.raw) - KNOWN_FIELDS):
        out.append(_pf("plugin-manifest-unknown-field", "warning", world,
            f"plugin.json has an unknown top level field '{field}'; clients "
            "ignore it", key=field))
    if "extensions" in m.raw and not isinstance(m.raw["extensions"], dict):
        out.append(_pf("plugin-manifest-unknown-field", "warning", world,
            "plugin.json 'extensions' is not an object; clients ignore it",
            key="extensions"))
    return out


@check("plugin-schema-unknown")
def schema_unknown(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None or m.schema_url is None or m.schema_url == SUPPORTED_SCHEMA:
        return []
    return [_pf(
        "plugin-schema-unknown", "warning", world,
        f"plugin.json declares '{m.schema_url}'; drskill validates against "
        "1.0.0", key=m.schema_url,
    )]


@check("plugin-skill-undiscoverable")
def skill_undiscoverable(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    skills_dir = Path(m.root) / "skills"
    if not skills_dir.is_dir():
        return []
    out = []
    for child in sorted(skills_dir.iterdir()):
        if (
            child.is_dir()
            and not (child / "SKILL.md").is_file()
            and not any(child.rglob("SKILL.md"))
        ):
            rel = child.relative_to(m.root)
            out.append(_pf("plugin-skill-undiscoverable", "warning", world,
                f"'{rel}' has no SKILL.md, so clients do not load it as a "
                "skill; was that intended?", key=str(rel)))
    for f in sorted(skills_dir.rglob("SKILL.md")):
        if f.parent.parent != skills_dir:
            rel = f.relative_to(m.root)
            out.append(_pf("plugin-skill-undiscoverable", "warning", world,
                f"'{rel}' is nested too deep; clients only discover "
                "skills/<name>/SKILL.md", key=str(rel)))
    return out


@check("plugin-path-escape")
def path_escape(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    root = Path(m.root).resolve()
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_symlink():
            continue
        real = p.resolve()
        if not real.is_relative_to(root):
            rel = p.relative_to(root)
            out.append(_pf("plugin-path-escape", "error", world,
                f"'{rel}' resolves outside the plugin root to {real}; "
                "clients must reject paths that escape the root",
                fix=[f"Replace the symlink {rel} with a file inside the plugin"],
                key=str(rel)))
    return out


@check("plugin-extension-hygiene")
def extension_hygiene(world: World, config: Config) -> list[Finding]:
    m = world.plugin
    if m is None:
        return []
    root = Path(m.root)
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if d.name == "skills" or d.name.startswith("."):
            continue
        if "." not in d.name:
            continue  # a plain dir (docs, tests) is not an attempted namespace
        if not _NAMESPACE_RE.fullmatch(d.name):
            out.append(_pf("plugin-extension-hygiene", "warning", world,
                f"'{d.name}/' looks like a client extension directory but is "
                "not a valid reverse domain namespace; clients ignore it",
                key=d.name))
            continue
        if (d / "mcp.json").is_file() or any(d.glob("skills/*/SKILL.md")):
            out.append(_pf("plugin-extension-hygiene", "warning", world,
                f"'{d.name}/' contains skills/ or mcp.json; portable "
                "components load only from the plugin root, so these load "
                "for no client unless that namespace defines them",
                key=f"{d.name}:shadow"))
        for jf in sorted(d.rglob("*.json")):
            if jf.stat().st_size > _SECRET_SCAN_CAP:
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            hits = sorted(_secret_keys(data))
            if hits:
                rel = jf.relative_to(root)
                out.append(_pf("plugin-extension-hygiene", "warning", world,
                    f"'{rel}' holds credential-shaped values: {', '.join(hits)}",
                    fix=[f"Move secrets out of {rel}; plugins are published"],
                    key=str(rel)))
    return out


def _secret_keys(data, prefix=""):
    hits = set()
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str) and looks_secret(str(k), v):
                hits.add(str(k))
            else:
                hits |= _secret_keys(v, prefix)
    elif isinstance(data, list):
        for item in data:
            hits |= _secret_keys(item, prefix)
    return hits
