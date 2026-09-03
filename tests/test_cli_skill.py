import json

import pytest
from typer.testing import CliRunner

from drskill import cli as cli_mod, content, service
from drskill.cli import app

runner = CliRunner()


def keys(*seq):
    it = iter(seq)
    return lambda: next(it)


def write_skill_dir(tmp_path, description="Use when demonstrating gated publishing to the registry."):
    d = tmp_path / "proj" / "my-skill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: my-skill\ndescription: {description}\n---\nBody.\n")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    service.save_credentials("http://svc.test", "drsk_x")

    state = {"publishes": [], "uploads": [], "existed": False}

    def fake_upload(files, token, base_url):
        state["uploads"].append(files)
        return {"content_hash": content.manifest_hash(files), "uploaded": True}

    monkeypatch.setattr(content, "upload", fake_upload)

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        if method == "POST" and path == "/api/v1/skills":
            state["publishes"].append(json_body)
            slug = json_body["skill"]["slug"]
            return {"skill": {"owner": "drew", "slug": slug, "visibility": "private",
                              "description": None,
                              "current_version": {"number": 1, "content_hash": json_body["skill"]["content_hash"]}},
                    "version": {"number": 1, "content_hash": json_body["skill"]["content_hash"],
                                "note": json_body["skill"].get("note")},
                    "existed": state["existed"]}
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    return home, state


def test_clean_skill_publishes(env, tmp_path):
    home, state = env
    d = write_skill_dir(tmp_path)
    result = runner.invoke(app, ["skill", "publish", str(d), "-m", "first cut"])
    assert result.exit_code == 0, result.output
    assert "Published drew/my-skill@1" in result.output
    assert len(state["uploads"]) == 1
    body = state["publishes"][0]["skill"]
    assert body["slug"] == "my-skill"
    assert body["name"] == "my-skill"
    assert body["note"] == "first cut"
    assert body["content_hash"].startswith("sha256:")


def test_idempotent_republish_reports_existing(env, tmp_path):
    home, state = env
    state["existed"] = True
    d = write_skill_dir(tmp_path)
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0, result.output
    assert "already" in result.output


def test_blocking_findings_stop_a_non_interactive_publish(env, tmp_path):
    home, state = env
    d = write_skill_dir(tmp_path, description="Helps with various tasks.")  # generic-description
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 1
    assert "generic-description" in result.output or "distinguishing" in result.output
    assert state["uploads"] == []
    assert state["publishes"] == []


def test_interactive_acks_unblock_the_publish(env, tmp_path, monkeypatch):
    home, state = env
    d = write_skill_dir(tmp_path, description="Helps with various tasks.")
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "key_source", keys("a", "a", "a"))
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0, result.output
    assert len(state["publishes"]) == 1
    assert "[[ack]]" in (home / ".drskill.toml").read_text()


def test_quitting_the_review_publishes_nothing(env, tmp_path, monkeypatch):
    home, state = env
    d = write_skill_dir(tmp_path, description="Helps with various tasks.")
    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "key_source", keys("q"))
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 1
    assert state["publishes"] == []
    assert state["uploads"] == []


def test_missing_skill_md_errors(env, tmp_path):
    d = tmp_path / "not-a-skill"
    d.mkdir()
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 1
    assert "SKILL.md" in result.output


# -- read commands --------------------------------------------------------------


@pytest.fixture
def read_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    service.save_credentials("http://svc.test", "drsk_x")

    calls = []

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        calls.append(path)
        if path == "/api/v1/skills":
            return {"skills": [{"owner": "drew", "slug": "citation-style",
                                "description": "Cites.", "visibility": "private",
                                "current_version": {"number": 3, "content_hash": "sha256:" + "ab" * 32}}]}
        if path == "/api/v1/skills/drew/citation-style/versions":
            return {"versions": [
                {"number": 2, "note": "tweak", "content_hash": "sha256:" + "cd" * 32,
                 "created_at": "2026-09-03T00:00:00Z", "byte_size": 10, "file_count": 1},
                {"number": 1, "note": "first", "content_hash": "sha256:" + "ab" * 32,
                 "created_at": "2026-09-02T00:00:00Z", "byte_size": 10, "file_count": 1}]}
        if path == "/api/v1/skills/drew/citation-style/files/SKILL.md":
            return "# Current body"
        if path == "/api/v1/skills/drew/citation-style/versions/1/files/SKILL.md":
            return "# Old body"
        if path == "/api/v1/skills/drew/citation-style/files/reference/tips.md":
            return "tips"
        if path == "/api/v1/skills/drew/citation-style/files":
            return {"version": 2, "files": [
                {"path": "SKILL.md", "size": 14, "executable": False, "text": True},
                {"path": "reference/tips.md", "size": 4, "executable": False, "text": True}]}
        if path == "/api/v1/skills/drew/citation-style/versions/2/diff?against=1":
            return {"added": ["new.md"], "removed": ["gone.md"], "changed": ["SKILL.md"],
                    "unchanged_count": 1}
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    return calls


def test_skill_list_renders_mine(read_env):
    result = runner.invoke(app, ["skill", "list"])
    assert result.exit_code == 0, result.output
    assert "drew/citation-style" in result.output
    assert "@3" in result.output


def test_skill_log_renders_versions_and_notes(read_env):
    result = runner.invoke(app, ["skill", "log", "drew/citation-style"])
    assert result.exit_code == 0, result.output
    assert "@2" in result.output and "tweak" in result.output
    assert "@1" in result.output and "first" in result.output


def test_skill_show_prints_the_current_skill_md(read_env):
    result = runner.invoke(app, ["skill", "show", "drew/citation-style"])
    assert result.exit_code == 0, result.output
    assert "# Current body" in result.output


def test_skill_show_pinned_and_flags(read_env, tmp_path):
    result = runner.invoke(app, ["skill", "show", "drew/citation-style@1"])
    assert "# Old body" in result.output

    result = runner.invoke(app, ["skill", "show", "drew/citation-style", "--files"])
    assert "reference/tips.md" in result.output

    result = runner.invoke(app, ["skill", "show", "drew/citation-style",
                                 "--file", "reference/tips.md"])
    assert "tips" in result.output


def test_skill_diff_renders_path_changes(read_env):
    result = runner.invoke(app, ["skill", "diff", "drew/citation-style", "@2", "@1"])
    assert result.exit_code == 0, result.output
    assert "+ new.md" in result.output
    assert "- gone.md" in result.output or "− gone.md" in result.output
    assert "~ SKILL.md" in result.output


def test_skill_show_bad_ref_errors(read_env):
    result = runner.invoke(app, ["skill", "show", "junk"])
    assert result.exit_code == 1
    assert "owner/slug" in result.output


# -- skill install ----------------------------------------------------------------

SKILL_FILES = [
    {"path": "SKILL.md", "data": b"# Current\n", "executable": False},
    {"path": "reference/tips.md", "data": b"tips\n", "executable": False},
]
OLD_SKILL_FILES = [{"path": "SKILL.md", "data": b"# Old\n", "executable": False}]
SKILL_HASH = content.manifest_hash(SKILL_FILES)
OLD_SKILL_HASH = content.manifest_hash(OLD_SKILL_FILES)


@pytest.fixture
def install_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    monkeypatch.chdir(project)
    service.save_credentials("http://svc.test", "drsk_x")

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        if path == "/api/v1/skills/drew/citation-style":
            return {"skill": {"owner": "drew", "slug": "citation-style",
                              "visibility": "unlisted", "description": None,
                              "current_version": {"number": 2, "content_hash": SKILL_HASH},
                              "recent_versions": []}}
        if path == "/api/v1/skills/drew/citation-style/versions":
            return {"versions": [
                {"number": 2, "content_hash": SKILL_HASH, "note": None},
                {"number": 1, "content_hash": OLD_SKILL_HASH, "note": None}]}
        if path == f"/api/v1/content/{SKILL_HASH}":
            return content.pack(SKILL_FILES)
        if path == f"/api/v1/content/{OLD_SKILL_HASH}":
            return content.pack(OLD_SKILL_FILES)
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    return home, project


def installed(home):
    return home / ".agents" / "skills" / "citation-style"


def test_skill_install_current_into_the_shared_store(install_env):
    home, _ = install_env
    result = runner.invoke(app, ["skill", "install", "drew/citation-style"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (installed(home) / "reference" / "tips.md").read_bytes() == b"tips\n"
    assert "@2" in result.output


def test_skill_install_pinned_version(install_env):
    home, _ = install_env
    result = runner.invoke(app, ["skill", "install", "drew/citation-style@1", "--yes"])
    assert result.exit_code == 0, result.output
    assert (installed(home) / "SKILL.md").read_bytes() == b"# Old\n"


def test_skill_install_reinstall_and_force(install_env):
    home, _ = install_env
    runner.invoke(app, ["skill", "install", "drew/citation-style", "--yes"])
    again = runner.invoke(app, ["skill", "install", "drew/citation-style", "--yes"])
    assert "already installed" in again.output

    (installed(home) / "SKILL.md").write_bytes(b"edited")
    held = runner.invoke(app, ["skill", "install", "drew/citation-style", "--yes"])
    assert "--force" in held.output
    forced = runner.invoke(app, ["skill", "install", "drew/citation-style", "--yes", "--force"])
    assert forced.exit_code == 0, forced.output
    assert (installed(home) / "SKILL.md").read_bytes() == b"# Current\n"


def test_skill_install_decline_and_missing(install_env):
    home, _ = install_env
    result = runner.invoke(app, ["skill", "install", "drew/citation-style"], input="n\n")
    assert result.exit_code == 0
    assert not installed(home).exists()

    result = runner.invoke(app, ["skill", "install", "drew/missing", "--yes"])
    assert result.exit_code == 1


# -- review fixes -----------------------------------------------------------------


def test_non_slug_frontmatter_name_publishes_clean(env, tmp_path):
    # The gate must lint under the skill's real folder name; naming the tmp
    # copy after the normalized slug used to fire spec-name-mismatch falsely.
    home, state = env
    d = tmp_path / "proj" / "My Skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: My Skill\ndescription: Use when demonstrating slug normalization on publish.\n---\nBody.\n")
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0, result.output
    assert state["publishes"][0]["skill"]["slug"] == "my-skill"


def test_underscored_names_slugify_for_the_server(env, tmp_path):
    home, state = env
    d = tmp_path / "proj" / "my_skill.v2"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: my_skill.v2\ndescription: Use when demonstrating slug rules for odd names.\n---\nBody.\n")
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0, result.output
    assert state["publishes"][0]["skill"]["slug"] == "my-skill-v2"


def test_acking_an_error_finding_unblocks(env, tmp_path, monkeypatch):
    # A real folder/frontmatter mismatch fires spec-name-mismatch (error).
    home, state = env
    d = tmp_path / "proj" / "actual-folder"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: different-name\ndescription: Use when demonstrating error acks on publish.\n---\nBody.\n")
    blocked = runner.invoke(app, ["skill", "publish", str(d)])
    assert blocked.exit_code == 1
    assert state["publishes"] == []

    monkeypatch.setattr(cli_mod.interactive, "can_interact", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod, "key_source", keys("a"))
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0, result.output
    assert len(state["publishes"]) == 1


def test_symlinks_are_rejected_loudly(env, tmp_path):
    home, state = env
    d = write_skill_dir(tmp_path)
    (d / "link.md").symlink_to(d / "SKILL.md")
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 1
    assert "symlink" in result.output.lower()
    assert state["uploads"] == []


def test_publish_prints_an_upload_summary(env, tmp_path):
    home, state = env
    d = write_skill_dir(tmp_path)
    result = runner.invoke(app, ["skill", "publish", str(d)])
    assert result.exit_code == 0
    assert "1 file" in result.output


def test_log_and_diff_reject_pinned_refs(read_env):
    result = runner.invoke(app, ["skill", "log", "drew/citation-style@2"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["skill", "diff", "drew/citation-style@2", "@2", "@1"])
    assert result.exit_code == 1


def test_show_file_is_binary_safe_and_url_encodes(read_env, monkeypatch):
    from drskill import service as service_module

    def fake_binary(method, path, token=None, json_body=None, base_url=None,
                    raw=False, raw_body=None, content_type=None, binary=False):
        assert binary
        assert path == "/api/v1/skills/drew/citation-style/files/data%20file.bin"
        return b"\xff\xfebytes"

    monkeypatch.setattr(service_module, "api_request", fake_binary)
    result = runner.invoke(app, ["skill", "show", "drew/citation-style",
                                 "--file", "data file.bin"])
    assert result.exit_code == 0
    assert b"\xff\xfebytes" in result.stdout_bytes
