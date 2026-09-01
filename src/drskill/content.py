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
import tarfile
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
