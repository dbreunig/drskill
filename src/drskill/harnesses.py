from __future__ import annotations

import tomllib
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class HarnessDef(BaseModel):
    id: str
    display_name: str
    verified: bool = False
    detect: list[str] = Field(default_factory=list)
    project_paths: list[str] = Field(default_factory=list)
    global_paths: list[str] = Field(default_factory=list)
    search_order: Literal["project-first", "global-first"] = "project-first"
    recursive: bool = True
    root_md_paths: list[str] = Field(default_factory=list)

    def search_paths(
        self, project_root: Path, home: Path, global_only: bool = False
    ) -> list[tuple[Path, str, str]]:
        """(directory, scope, spec_str) triples in precedence order."""
        proj = [(project_root / s, "project", s) for s in self.project_paths]
        glob = [(home / s.removeprefix("~/"), "user", s) for s in self.global_paths]
        if global_only:
            return glob
        if self.search_order == "global-first":
            return glob + proj
        return proj + glob


@cache
def load_harnesses() -> tuple[HarnessDef, ...]:
    text = resources.files("drskill.data").joinpath("harnesses.toml").read_text()
    data = tomllib.loads(text)
    return tuple(HarnessDef(**h) for h in data["harness"])


def detect_harnesses(
    project_root: Path, home: Path, global_only: bool = False
) -> list[HarnessDef]:
    found = []
    for h in load_harnesses():
        for marker in h.detect:
            if marker.startswith("~/"):
                p = home / marker.removeprefix("~/")
            elif global_only:
                continue
            else:
                p = project_root / marker
            if p.exists():
                found.append(h)
                break
    return found
