from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path

import tomli_w
from pydantic import BaseModel, Field

from drskill.models import Finding


class Budget(BaseModel):
    catalog_tokens_max: int = 6000
    body_tokens_warn: int = 20000


class Thresholds(BaseModel):
    near_duplicate: float = 0.85


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


def load_config(path: Path) -> Config:
    if not path.is_file():
        return Config()
    return Config(**tomllib.loads(path.read_text()))


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
