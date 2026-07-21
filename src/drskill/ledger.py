from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path

import tomli_w
from pydantic import BaseModel, Field, ValidationError

from drskill.models import Finding


class LedgerError(Exception):
    """Raised when a drskill.toml ledger file is malformed or schema-invalid.

    Callers (the CLI layer) catch this and print a one-line error instead of
    letting a raw tomllib/pydantic traceback reach the user."""


class Budget(BaseModel):
    catalog_tokens_max: int = 6000
    body_tokens_warn: int = 20000


class Thresholds(BaseModel):
    near_duplicate: float = 0.85
    description_overlap: float = 0.6
    generic_min_distinct_tokens: int = 2


class Deep(BaseModel):
    model: str = "anthropic/claude-haiku-4-5"


class Ack(BaseModel):
    check: str
    skills: list[str]
    fingerprint: str
    note: str | None = None
    date: dt.date | None = None


class Config(BaseModel):
    budget: Budget = Budget()
    thresholds: Thresholds = Thresholds()
    deep: Deep = Deep()
    ack: list[Ack] = Field(default_factory=list)


def ledger_path(project_root: Path, home: Path, global_mode: bool) -> Path:
    return home / ".drskill.toml" if global_mode else project_root / "drskill.toml"


def _validation_one_liner(e: ValidationError) -> str:
    first = e.errors()[0]
    loc = ".".join(str(p) for p in first["loc"])
    return f"{loc}: {first['msg']}" if loc else first["msg"]


def load_config(path: Path) -> Config:
    if not path.is_file():
        return Config()
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise LedgerError(f"{path}: invalid TOML: {e}") from e
    try:
        return Config(**data)
    except ValidationError as e:
        raise LedgerError(f"{path}: {_validation_one_liner(e)}") from e


def load_effective_config(project_root: Path, home: Path, global_mode: bool) -> Config:
    """The config that governs a scan. In project mode, decisions merge from
    both ledgers (a machine-level ack is honored in every project); budgets
    and thresholds stay with the mode's own ledger."""
    cfg = load_config(ledger_path(project_root, home, global_mode))
    if not global_mode:
        gcfg = load_config(ledger_path(project_root, home, True))
        cfg = cfg.model_copy(update={"ack": [*cfg.ack, *gcfg.ack]})
    return cfg


def ack_destination(
    world,
    finding: Finding,
    project_root: Path,
    home: Path,
    global_mode: bool,
    force_local: bool = False,
    force_global: bool = False,
) -> Path:
    """A finding that lives entirely in the machine's global loadout is a
    machine decision, so its ack goes to ~/.drskill.toml and is honored in
    every project. Anything touching a project skill stays in the project
    ledger."""
    if global_mode or force_global:
        return ledger_path(project_root, home, True)
    if force_local:
        return ledger_path(project_root, home, False)
    # For MCP sources the routing fact is where the FILE lives, not which
    # scope a server applies to: everything in a home-side file is a
    # machine decision. Parse-error paths are included so an invalid
    # user config also acks to the machine ledger.
    by_source = {s.source: s.in_project for s in getattr(world, "mcp_servers", [])}
    for _hid, path, _msg, in_project in getattr(world, "mcp_config_errors", []):
        by_source.setdefault(path, in_project)
    scopes = set()
    for cid in finding.contributors:
        if cid in world.contributors:
            scopes.add(world.contributors[cid].scope)
        elif cid in by_source:
            scopes.add("project" if by_source[cid] else "user")
    if scopes and scopes == {"user"}:
        return ledger_path(project_root, home, True)
    return ledger_path(project_root, home, False)


def append_ack(path: Path, ack: Ack) -> None:
    """Append the new [[ack]] entry as text instead of rewriting the file,
    so the user's comments and formatting are never touched. An [[ack]]
    header always starts a fresh table, so appending is valid TOML no
    matter what the file ends with."""
    entry = {k: v for k, v in ack.model_dump().items() if v is not None}
    block = tomli_w.dumps({"ack": [entry]})
    if not path.is_file():
        path.write_text(block)
        return
    existing = path.read_text()
    tomllib.loads(existing)  # malformed ledgers fail loudly, before we append
    sep = "" if existing.endswith("\n") or not existing else "\n"
    path.write_text(existing + sep + "\n" + block if existing.strip() else block)


def filter_findings(
    findings: list[Finding], config: Config
) -> tuple[list[Finding], list[Finding]]:
    acked_fps = {a.fingerprint for a in config.ack}
    active = [f for f in findings if f.fingerprint not in acked_fps]
    acked = [f for f in findings if f.fingerprint in acked_fps]
    return active, acked
