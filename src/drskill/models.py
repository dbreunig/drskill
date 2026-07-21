from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class RawInstance(BaseModel):
    """A skill file as one harness sees it, before resolution."""

    harness: str
    scope: Literal["project", "user"]
    skill_file: Path
    via_symlink: bool
    order: int  # index of the containing search path in the harness's list


class Deployment(BaseModel):
    harness: str
    path: Path
    scope: Literal["project", "user"]
    via_symlink: bool
    order: int
    shadowed_by: str | None = None  # contributor id of the winner, when shadowed


class Provenance(BaseModel):
    kind: Literal["skills-lock", "gh-skill", "linked", "unmanaged"] = "unmanaged"
    source: str | None = None


class TokenCost(BaseModel):
    catalog_tokens: int  # name + description, approximate
    body_tokens: int  # full body, approximate


class BundledFile(BaseModel):
    """A file a skill ships with, e.g. under scripts/ or references/."""

    relpath: str  # posix style, relative to the skill directory
    size: int
    content_hash: str  # sha256 of the raw bytes, no normalization
    is_text: bool  # no null byte in the first 8 KiB
    oversize: bool  # larger than the scan cap; recorded, never content-scanned


class Contributor(BaseModel):
    id: str  # str(realpath of the skill file)
    kind: Literal["skill"] = "skill"
    name: str
    source: Provenance = Provenance()
    scope: Literal["project", "user"]
    deployments: list[Deployment] = Field(default_factory=list)
    bundled_files: list[BundledFile] = Field(default_factory=list)
    routing_text: str = ""
    body: str = ""
    token_cost: TokenCost
    content_hash: str
    frontmatter_valid: bool = True
    frontmatter: dict = Field(default_factory=dict)
    frontmatter_text: str = ""
    system: bool = False  # lives under a `.system` dir: a harness-vendored skill


class Finding(BaseModel):
    check_id: str
    severity: Literal["error", "warning", "note"]
    contributors: list[str]  # contributor ids
    contributor_names: list[str]
    harnesses: list[str]
    message: str
    fix_commands: list[str] = Field(default_factory=list)
    fingerprint: str


class BrokenSymlink(BaseModel):
    harness: str
    path: Path
