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


class Ack(BaseModel):
    check: str
    skills: list[str]
    fingerprint: str
    note: str | None = None
    date: dt.date | None = None


class Config(BaseModel):
    budget: Budget = Budget()
    thresholds: Thresholds = Thresholds()
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


def append_ack(path: Path, ack: Ack) -> None:
    data = tomllib.loads(path.read_text()) if path.is_file() else {}
    entry = {k: v for k, v in ack.model_dump().items() if v is not None}
    data.setdefault("ack", []).append(entry)
    path.write_text(tomli_w.dumps(data))


def filter_findings(
    findings: list[Finding], config: Config
) -> tuple[list[Finding], list[Finding]]:
    acked_fps = {a.fingerprint for a in config.ack}
    active = [f for f in findings if f.fingerprint not in acked_fps]
    acked = [f for f in findings if f.fingerprint in acked_fps]
    return active, acked
