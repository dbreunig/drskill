"""Fetch and verify github-sourced skills for loadout install.

Fetches repository tarballs from codeload over https, locates the skill
directory using recorded metadata (or a hash search), extracts it through
the same safety gate hosted downloads use, and verifies the result against
the hashes the publisher recorded.
"""

from __future__ import annotations

import io
import os
import re
import tarfile
import urllib.error
import urllib.request

from drskill import content, resolution
from drskill.manifest_build import parse_repo

MAX_TARBALL_BYTES = 100 * 1024 * 1024
_CHUNK = 1024 * 1024


class FetchError(Exception):
    pass


def coordinates(entry: dict) -> tuple[str, str] | None:
    """(repo, ref) for a github entry, or None when the source is not
    fetchable."""
    metadata = entry.get("metadata") or {}
    repo = metadata.get("repo") or parse_repo(entry.get("source_reference"))
    if not repo:
        return None
    ref = metadata.get("ref") or entry.get("source_version") or "HEAD"
    return repo, ref


def fetch_tarball(repo: str, ref: str, base_url: str | None = None) -> bytes:
    base = (base_url or os.environ.get(
        "DRSKILL_CODELOAD_URL", "https://codeload.github.com")).rstrip("/")
    request = urllib.request.Request(f"{base}/{repo}/tar.gz/{ref}")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            chunks: list[bytes] = []
            total = 0
            while chunk := response.read(_CHUNK):
                total += len(chunk)
                if total > MAX_TARBALL_BYTES:
                    raise FetchError(f"{repo}@{ref} exceeds the 100 MB download cap")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as error:
        raise FetchError(f"GitHub returned HTTP {error.code} for {repo}@{ref}") from None
    except urllib.error.URLError as error:
        raise FetchError(f"could not reach GitHub: {error.reason}") from None


def verify(files: list[dict], entry: dict) -> str:
    """"ok" (directory hash match), "legacy_ok" (SKILL.md-only match for
    entries without a directory hash), or "mismatch"."""
    metadata = entry.get("metadata") or {}
    directory_hash = metadata.get("directory_hash")
    if directory_hash:
        return "ok" if content.manifest_hash(files) == directory_hash else "mismatch"
    skill_md = next((f for f in files if f["path"] == "SKILL.md"), None)
    if skill_md is None:
        return "mismatch"
    try:
        text = skill_md["data"].decode()
    except UnicodeDecodeError:
        return "mismatch"
    if resolution.content_hash(text) == entry.get("content_hash"):
        return "legacy_ok"
    return "mismatch"


def extract_skill(tar_bytes: bytes, entry: dict) -> list[dict]:
    """The skill directory's files from a repo tarball, located by the
    recorded skill_path or by hash search. Raises FetchError on locate
    failure, unsafe members inside the skill directory, or a bad tarball."""
    metadata = entry.get("metadata") or {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            members = tar.getmembers()
            skill_path = metadata.get("skill_path")
            if skill_path is not None:
                return _apply_file_list(_extract_dir(tar, members, skill_path), entry)
            return _search(tar, members, entry)
    except FetchError:
        raise
    except (tarfile.TarError, EOFError, OSError):
        raise FetchError("the downloaded archive is not a valid tarball") from None


def _stripped(name: str) -> str | None:
    """The member path without the tarball's top-level directory, or None
    for the top-level directory itself."""
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 else None


def _extract_dir(tar, members, skill_dir: str) -> list[dict]:
    prefix = f"{skill_dir}/" if skill_dir else ""
    files: list[dict] = []
    total = 0
    for member in members:
        path = _stripped(member.name)
        if path is None or not path.startswith(prefix):
            continue
        if member.isdir():
            continue
        relpath = path[len(prefix):]
        if not member.isreg():
            raise FetchError(f"{relpath!r} in the skill directory is not a regular file")
        _check_relpath(relpath)
        if len(files) >= content.MAX_FILES:
            raise FetchError(f"the skill directory has more than {content.MAX_FILES} files")
        total += member.size
        if total > content.MAX_UNPACKED_BYTES:
            raise FetchError("the skill directory expands past 20 MB")
        files.append({
            "path": relpath,
            "data": tar.extractfile(member).read(),
            "executable": bool(member.mode & 0o111),
        })
    if not files:
        raise FetchError(f"no files found under {skill_dir!r} in the repository")
    return files


def _check_relpath(relpath: str) -> None:
    segments = relpath.split("/")
    unsafe = (
        not relpath
        or len(relpath.encode()) > content.MAX_PATH_BYTES
        or any(s in ("", ".", "..") for s in segments)
    )
    if unsafe:
        raise FetchError(f"unsafe path {relpath!r} in the skill directory")


def _apply_file_list(files: list[dict], entry: dict) -> list[dict]:
    """Keep only the files the publisher recorded as the skill; repository
    housekeeping files around a root-level skill are not the skill."""
    recorded = (entry.get("metadata") or {}).get("files")
    if not (isinstance(recorded, list) and recorded):
        return files
    wanted = set(recorded)
    kept = [f for f in files if f["path"] in wanted]
    if not kept:
        raise FetchError("none of the recorded skill files exist in the repository")
    return kept


def _search(tar, members, entry: dict) -> list[dict]:
    candidates = sorted({
        (_stripped(m.name) or "").rsplit("/", 1)[0] if "/" in (_stripped(m.name) or "") else ""
        for m in members
        if m.isreg() and (_stripped(m.name) or "").split("/")[-1] == "SKILL.md"
    })
    matches: list[list[dict]] = []
    for candidate in candidates:
        try:
            files = _apply_file_list(_extract_dir(tar, members, candidate), entry)
        except FetchError:
            continue
        if verify(files, entry) != "mismatch":
            matches.append(files)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FetchError("no directory in the repository matches this entry's hash")
    raise FetchError("several directories in the repository match this entry's hash")


_GH_URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$")
_SHORT = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([A-Za-z0-9_./-]+)$")


def parse_github_target(target: str):
    """(repo, ref, subpath) for a GitHub install target, or None when the
    string reads as a registry ref (or nothing). Refs are assumed to be a
    single path segment; blob URLs resolve to the file's directory. A
    numeric @N stays a registry pin."""
    target = target.strip()
    match = _GH_URL.match(target)
    if match:
        repo, ref, subpath = match.groups()
        subpath = (subpath or "").strip("/")
        if "/blob/" in target and subpath:
            subpath = subpath.rsplit("/", 1)[0] if "/" in subpath else ""
        return repo, ref or "HEAD", subpath
    match = _SHORT.match(target)
    if match and not match.group(2).isdigit():
        return match.group(1), match.group(2), ""
    return None


def find_skills(tar_bytes: bytes, subpath: str) -> list[tuple[str, list[dict]]]:
    """Every SKILL.md directory at or under subpath, as (repo path, files)
    pairs extracted through the safety gate. A directory whose extraction
    fails is skipped."""
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            members = tar.getmembers()
            prefix = f"{subpath}/" if subpath else ""
            skill_dirs = sorted({
                (path.rsplit("/", 1)[0] if "/" in path else "")
                for path in ((_stripped(m.name) or "") for m in members)
                if path.split("/")[-1] == "SKILL.md"
            })
            candidates = [d for d in skill_dirs
                          if not subpath or d == subpath or d.startswith(prefix)]
            found = []
            for candidate in candidates:
                try:
                    found.append((candidate, _extract_dir(tar, members, candidate)))
                except FetchError:
                    continue
            return found
    except (tarfile.TarError, EOFError, OSError):
        raise FetchError("the downloaded archive is not a valid tarball") from None
