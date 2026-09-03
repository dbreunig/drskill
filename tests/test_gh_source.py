import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from drskill import content, gh_source, resolution

SKILL_MD = b"---\nname: citation\ndescription: d\n---\nbody\n"
REPO_FILES = {
    "README.md": b"# repo\n",
    "skills/citation/SKILL.md": SKILL_MD,
    "skills/citation/reference/tips.md": b"tips\n",
    "skills/other/SKILL.md": b"---\nname: other\ndescription: o\n---\nother\n",
}
CITATION_FILES = [
    {"path": "SKILL.md", "data": SKILL_MD, "executable": False},
    {"path": "reference/tips.md", "data": b"tips\n", "executable": False},
]
CITATION_HASH = content.manifest_hash(CITATION_FILES)


def repo_tarball(files=REPO_FILES, top="repo-abc123", symlink=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"{top}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(f"{top}/{symlink}")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    return buf.getvalue()


def entry(**overrides):
    base = {
        "kind": "skill", "selector": "skill:citation", "name": "citation",
        "source_type": "github", "source_reference": "friend/pack@v1",
        "source_version": "v1", "content_hash": "sha256:" + "ab" * 32,
        "local_only": False,
        "metadata": {"repo": "friend/pack", "skill_path": "skills/citation",
                     "ref": "v1.2.0", "directory_hash": CITATION_HASH},
    }
    base.update(overrides)
    return base


# -- coordinates --------------------------------------------------------------


def test_coordinates_prefers_metadata():
    assert gh_source.coordinates(entry()) == ("friend/pack", "v1.2.0")


def test_coordinates_falls_back_to_source_fields():
    e = entry(metadata={})
    assert gh_source.coordinates(e) == ("friend/pack", "v1")
    e = entry(metadata={}, source_version=None)
    assert gh_source.coordinates(e) == ("friend/pack", "HEAD")


def test_coordinates_returns_none_for_junk():
    assert gh_source.coordinates(entry(metadata={}, source_reference="???")) is None


# -- extract_skill ------------------------------------------------------------


def test_extract_by_skill_path():
    files = gh_source.extract_skill(repo_tarball(), entry())
    assert content.manifest_hash(files) == CITATION_HASH
    assert sorted(f["path"] for f in files) == ["SKILL.md", "reference/tips.md"]


def test_extract_root_level_skill():
    tarball = repo_tarball(files={"SKILL.md": SKILL_MD, "reference/tips.md": b"tips\n"})
    e = entry(metadata={"repo": "friend/pack", "skill_path": "",
                        "directory_hash": CITATION_HASH})
    files = gh_source.extract_skill(tarball, e)
    assert content.manifest_hash(files) == CITATION_HASH


def test_extract_locates_by_hash_when_path_is_missing():
    e = entry(metadata={"repo": "friend/pack", "directory_hash": CITATION_HASH})
    files = gh_source.extract_skill(repo_tarball(), e)
    assert content.manifest_hash(files) == CITATION_HASH


def test_extract_locates_legacy_entries_by_skill_md_hash():
    e = entry(metadata={"repo": "friend/pack"},
              content_hash=resolution.content_hash(SKILL_MD.decode()))
    files = gh_source.extract_skill(repo_tarball(), e)
    assert content.manifest_hash(files) == CITATION_HASH


def test_extract_fails_when_nothing_matches():
    e = entry(metadata={"repo": "friend/pack",
                        "directory_hash": "sha256:" + "00" * 32})
    with pytest.raises(gh_source.FetchError):
        gh_source.extract_skill(repo_tarball(), e)


def test_extract_fails_on_ambiguity():
    files = dict(REPO_FILES)
    files["copy/citation/SKILL.md"] = SKILL_MD
    files["copy/citation/reference/tips.md"] = b"tips\n"
    e = entry(metadata={"repo": "friend/pack", "directory_hash": CITATION_HASH})
    with pytest.raises(gh_source.FetchError) as excinfo:
        gh_source.extract_skill(repo_tarball(files=files), e)
    assert "several" in str(excinfo.value)


def test_extract_rejects_symlinks_inside_the_skill():
    tarball = repo_tarball(symlink="skills/citation/evil")
    with pytest.raises(gh_source.FetchError):
        gh_source.extract_skill(tarball, entry())


def test_extract_ignores_files_outside_the_skill_dir():
    tarball = repo_tarball(symlink="unrelated/evil")
    files = gh_source.extract_skill(tarball, entry())
    assert content.manifest_hash(files) == CITATION_HASH


def test_extract_fails_on_garbage():
    with pytest.raises(gh_source.FetchError):
        gh_source.extract_skill(b"not a tarball", entry())


# -- verify -------------------------------------------------------------------


def test_verify_directory_hash():
    assert gh_source.verify(CITATION_FILES, entry()) == "ok"
    bad = entry(metadata={"repo": "friend/pack", "skill_path": "skills/citation",
                          "directory_hash": "sha256:" + "00" * 32})
    assert gh_source.verify(CITATION_FILES, bad) == "mismatch"


def test_verify_legacy_skill_md():
    good = entry(metadata={"repo": "friend/pack"},
                 content_hash=resolution.content_hash(SKILL_MD.decode()))
    assert gh_source.verify(CITATION_FILES, good) == "legacy_ok"
    bad = entry(metadata={"repo": "friend/pack"})
    assert gh_source.verify(CITATION_FILES, bad) == "mismatch"


# -- fetch_tarball ------------------------------------------------------------


class _CodeloadStub(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/friend/pack/tar.gz/v1.2.0":
            body = repo_tarball()
            self.send_response(200)
        else:
            body = b"not found"
            self.send_response(404)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def codeload():
    server = HTTPServer(("127.0.0.1", 0), _CodeloadStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_fetch_tarball_downloads(codeload):
    body = gh_source.fetch_tarball("friend/pack", "v1.2.0", base_url=codeload)
    assert gh_source.extract_skill(body, entry())


def test_fetch_tarball_reports_http_errors(codeload):
    with pytest.raises(gh_source.FetchError) as excinfo:
        gh_source.fetch_tarball("friend/pack", "missing", base_url=codeload)
    assert "404" in str(excinfo.value)


def test_fetch_tarball_enforces_the_size_cap(codeload, monkeypatch):
    monkeypatch.setattr(gh_source, "MAX_TARBALL_BYTES", 10)
    with pytest.raises(gh_source.FetchError) as excinfo:
        gh_source.fetch_tarball("friend/pack", "v1.2.0", base_url=codeload)
    assert "cap" in str(excinfo.value)


# -- recorded file list filtering ---------------------------------------------


def test_extract_filters_to_the_recorded_file_list():
    # A root-level skill: the repo carries housekeeping files the installer
    # never materialized. Only the recorded files are the skill.
    repo = {
        "SKILL.md": SKILL_MD,
        "reference/tips.md": b"tips\n",
        "README.md": b"# repo\n",
        ".gitignore": b"*.pyc\n",
        "docs/dev-notes.md": b"internal\n",
    }
    recorded = [
        {"path": "SKILL.md", "data": SKILL_MD, "executable": False},
        {"path": "reference/tips.md", "data": b"tips\n", "executable": False},
    ]
    e = entry(metadata={
        "repo": "friend/pack", "skill_path": "",
        "files": ["SKILL.md", "reference/tips.md"],
        "directory_hash": content.manifest_hash(recorded),
    })
    files = gh_source.extract_skill(repo_tarball(files=repo), e)
    assert sorted(f["path"] for f in files) == ["SKILL.md", "reference/tips.md"]
    assert gh_source.verify(files, e) == "ok"


def test_extract_with_file_list_detects_true_drift():
    repo = {"SKILL.md": b"changed body\n", "README.md": b"# repo\n"}
    recorded = [{"path": "SKILL.md", "data": SKILL_MD, "executable": False}]
    e = entry(metadata={
        "repo": "friend/pack", "skill_path": "",
        "files": ["SKILL.md"],
        "directory_hash": content.manifest_hash(recorded),
    })
    files = gh_source.extract_skill(repo_tarball(files=repo), e)
    assert gh_source.verify(files, e) == "mismatch"


def test_extract_fails_when_every_recorded_file_is_gone():
    repo = {"README.md": b"# repo\n"}
    e = entry(metadata={"repo": "friend/pack", "skill_path": "",
                        "files": ["SKILL.md"], "directory_hash": CITATION_HASH})
    with pytest.raises(gh_source.FetchError):
        gh_source.extract_skill(repo_tarball(files=repo), e)


# -- github install targets ---------------------------------------------------


def test_parse_github_target_url_forms():
    f = gh_source.parse_github_target
    assert f("https://github.com/humanlayer/skills") == ("humanlayer/skills", "HEAD", "")
    assert f("https://github.com/humanlayer/skills.git") == ("humanlayer/skills", "HEAD", "")
    assert f("https://github.com/humanlayer/skills/tree/main/plugins/show-me") == \
        ("humanlayer/skills", "main", "plugins/show-me")
    assert f("https://github.com/humanlayer/skills/tree/main") == ("humanlayer/skills", "main", "")
    assert f("https://github.com/o/r/blob/v2/skills/x/SKILL.md") == ("o/r", "v2", "skills/x")
    assert f("owner/repo@my-branch") == ("owner/repo", "my-branch", "")
    assert f("owner/repo@3") is None          # numeric = registry pin
    assert f("owner/repo") is None            # bare = registry unless --github
    assert f("not a target") is None


def test_find_skills_under_a_plugin_path():
    files = {
        "plugins/show-me/README.md": b"about\n",
        "plugins/show-me/skills/render/SKILL.md": b"---\nname: render\ndescription: Renders.\n---\nB\n",
        "plugins/show-me/skills/capture/SKILL.md": b"---\nname: capture\ndescription: Captures.\n---\nB\n",
        "plugins/other/skills/x/SKILL.md": b"---\nname: x\ndescription: X.\n---\nB\n",
        "SKILL.md": b"---\nname: root\ndescription: Root.\n---\nB\n",
    }
    found = gh_source.find_skills(repo_tarball(files=files), "plugins/show-me")
    assert sorted(path for path, _ in found) == \
        ["plugins/show-me/skills/capture", "plugins/show-me/skills/render"]

    direct = gh_source.find_skills(repo_tarball(files=files), "plugins/show-me/skills/render")
    assert [path for path, _ in direct] == ["plugins/show-me/skills/render"]

    everything = gh_source.find_skills(repo_tarball(files=files), "")
    assert len(everything) == 4
