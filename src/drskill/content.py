"""Pack and upload skill content to the drskill service.

Implements the CLI half of the service's content hosting contract: the
manifest hash is sha256 over a sorted sha256sum-style listing of the files,
so it is independent of archive byte layout, and the upload body is a
gzipped ustar tar the server unpacks, validates, and canonicalizes itself.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from drskill import service
from drskill.models import Contributor

# HEAD miss codes that mean "not on the server (or not visible)"; anything
# else (auth, connection) should surface to the caller unswallowed.
_MISS_CODES = {"not_found", "http_error"}


def collect_files(contributor: Contributor) -> list[dict]:
    """Read the skill file and its bundled files from disk.

    Packs exactly what the scanner saw, not a blind directory walk, so
    stray files never travel.
    """
    skill_file = Path(contributor.id)
    root = skill_file.parent
    files = [_file_entry(skill_file, skill_file.name)]
    for bundled in contributor.bundled_files:
        files.append(_file_entry(root / bundled.relpath, bundled.relpath))
    return files


def _file_entry(path: Path, relpath: str) -> dict:
    return {
        "path": relpath,
        "data": path.read_bytes(),
        "executable": bool(path.stat().st_mode & 0o111),
    }


def manifest_hash(files: list[dict]) -> str:
    lines = [
        f"{hashlib.sha256(f['data']).hexdigest()}  {f['path']}\n"
        for f in sorted(files, key=lambda f: f["path"].encode())
    ]
    return "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest()


def pack(files: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for file in sorted(files, key=lambda f: f["path"].encode()):
                info = tarfile.TarInfo(file["path"])
                info.size = len(file["data"])
                info.mode = 0o755 if file["executable"] else 0o644
                info.mtime = 0
                tar.addfile(info, io.BytesIO(file["data"]))
    return buffer.getvalue()


def upload(files: list[dict], token: str, base_url: str) -> dict:
    """Ensure the content is on the server; returns the hash and whether
    a transfer actually happened."""
    local_hash = manifest_hash(files)
    try:
        service.api_request("HEAD", f"/api/v1/content/{local_hash}",
                            token=token, base_url=base_url)
        return {"content_hash": local_hash, "uploaded": False}
    except service.ServiceError as error:
        if error.code not in _MISS_CODES:
            raise

    response = service.api_request(
        "POST", "/api/v1/content", token=token, base_url=base_url,
        raw_body=pack(files), content_type="application/gzip",
    )
    server_hash = response.get("content_hash")
    if server_hash != local_hash:
        raise service.ServiceError(
            "hash_mismatch",
            f"The server computed {server_hash}, this machine computed {local_hash}.",
        )
    return {"content_hash": local_hash, "uploaded": True}


# Mirror of the server's unpack caps: the client verifies downloads with the
# same rules the server applied at upload.
MAX_UNPACKED_BYTES = 20 * 1024 * 1024
MAX_FILES = 200
MAX_PATH_BYTES = 255


def _safe_relpath(name: str) -> str:
    path = name.removeprefix("./")
    segments = path.split("/")
    if (
        not path
        or len(path.encode()) > MAX_PATH_BYTES
        or name.startswith("/")
        or any(s in ("", ".", "..") for s in segments)
    ):
        raise service.ServiceError("content_invalid", f"unsafe path {name!r} in archive")
    return path


def unpack(body: bytes) -> list[dict]:
    """Unpack a downloaded archive with the same checks the server enforces."""
    files: list[dict] = []
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            for member in tar:
                if member.isdir():
                    continue
                if not member.isreg():
                    raise service.ServiceError(
                        "content_invalid", f"{member.name!r} is not a regular file")
                path = _safe_relpath(member.name)
                if path in seen:
                    raise service.ServiceError("content_invalid", f"duplicate path {path!r}")
                seen.add(path)
                if len(files) >= MAX_FILES:
                    raise service.ServiceError("content_invalid", "too many files")
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise service.ServiceError("content_invalid", "archive expands past 20 MB")
                data = tar.extractfile(member).read()
                files.append({
                    "path": path,
                    "data": data,
                    "executable": bool(member.mode & 0o111),
                })
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, OSError):
        raise service.ServiceError(
            "content_invalid", "is not a valid gzipped tar archive") from None
    if not files:
        raise service.ServiceError("content_invalid", "archive contains no files")
    return files


def download(content_hash: str, token: str, base_url: str) -> list[dict]:
    """Fetch an archive and verify its manifest hash before trusting it."""
    body = service.api_request(
        "GET", f"/api/v1/content/{content_hash}",
        token=token, base_url=base_url, binary=True,
    )
    files = unpack(body)
    actual = manifest_hash(files)
    if actual != content_hash:
        raise service.ServiceError(
            "hash_mismatch",
            f"Downloaded content hashes to {actual}, expected {content_hash}.",
        )
    return files


def read_dir(root: Path) -> list[dict]:
    """Collect every regular file under root, for comparing an installed
    skill against a content hash."""
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            files.append(_file_entry(path, path.relative_to(root).as_posix()))
    return files


def write_skill(files: list[dict], target: Path) -> None:
    """Write the files as target/, atomically: build a sibling temporary
    tree and swap it into place, so a failure never leaves a partial skill."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for file in files:
            dest = staging / file["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file["data"])
            if file["executable"]:
                dest.chmod(dest.stat().st_mode | 0o755)
        if target.exists():
            retired = Path(tempfile.mkdtemp(prefix=f".{target.name}.old.", dir=target.parent))
            os.replace(target, retired / "gone")
            os.replace(staging, target)
            shutil.rmtree(retired)
        else:
            os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
