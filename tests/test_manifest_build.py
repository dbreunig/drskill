from drskill import manifest_build
from drskill.models import Contributor, Provenance, TokenCost


def contributor(name, kind="skill", prov_kind="gh-skill", source="friend/skill@v1",
                scope="project", content_hash="sha256:" + "ab" * 32):
    return Contributor(
        id=f"/tmp/{name}",
        kind=kind,
        name=name,
        source=Provenance(kind=prov_kind, source=source),
        scope=scope,
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash=content_hash,
    )


def test_envelope_shape_and_github_mapping():
    manifest, notes = manifest_build.contributors_to_manifest([contributor("citation-style")])
    assert manifest["schema_version"] == 1
    assert manifest["reproducible"] is False
    assert manifest["harness_mappings"] == []
    entry = manifest["entries"][0]
    assert entry == {
        "kind": "skill",
        "selector": "skill:citation-style",
        "name": "citation-style",
        "source_type": "github",
        "source_reference": "friend/skill@v1",
        "content_hash": "sha256:" + "ab" * 32,
        "local_only": False,
        "metadata": {"repo": "friend/skill"},
    }
    assert notes == []


def test_provenance_kinds_map_to_source_types():
    cases = {
        "gh-skill": ("github", False),
        "skills-lock": ("github", False),
        "plugin": ("plugin", False),
        "linked": ("local", True),
        "unmanaged": ("local", True),
    }
    for prov_kind, (source_type, local_only) in cases.items():
        manifest, _ = manifest_build.contributors_to_manifest(
            [contributor("x", prov_kind=prov_kind, source="somewhere")]
        )
        entry = manifest["entries"][0]
        assert entry["source_type"] == source_type, prov_kind
        assert entry["local_only"] is local_only, prov_kind


def test_missing_source_forces_local_only():
    manifest, _ = manifest_build.contributors_to_manifest(
        [contributor("x", prov_kind="gh-skill", source=None)]
    )
    entry = manifest["entries"][0]
    assert entry["local_only"] is True
    assert entry["source_type"] == "local"
    assert entry["source_reference"] == "/tmp/x"


def test_mcp_tool_kind():
    manifest, _ = manifest_build.contributors_to_manifest([contributor("papers", kind="mcp_tool")])
    assert manifest["entries"][0]["kind"] == "mcp"
    assert manifest["entries"][0]["selector"] == "mcp:papers"


def test_name_normalization_with_note():
    manifest, notes = manifest_build.contributors_to_manifest([contributor("My Skill!")])
    assert manifest["entries"][0]["name"] == "my-skill"
    assert manifest["entries"][0]["selector"] == "skill:my-skill"
    assert any("My Skill!" in note and "my-skill" in note for note in notes)


def test_duplicate_selectors_get_suffixes_with_note():
    manifest, notes = manifest_build.contributors_to_manifest(
        [contributor("dup"), contributor("dup"), contributor("dup")]
    )
    selectors = [entry["selector"] for entry in manifest["entries"]]
    assert selectors == ["skill:dup", "skill:dup-2", "skill:dup-3"]
    assert any("dup-2" in note for note in notes)


def test_normalize_name_rules():
    assert manifest_build.normalize_name("Citation Style") == "citation-style"
    assert manifest_build.normalize_name("__weird--Name__") == "weird-name"
    assert manifest_build.normalize_name("ok.name_1") == "ok.name_1"
    assert manifest_build.normalize_name("!!!") == "skill"


def test_manifest_is_canonicalizable():
    from drskill import service

    manifest, _ = manifest_build.contributors_to_manifest([contributor("a"), contributor("b")])
    canonical, runtime_hash = service.canonical_manifest(manifest)
    assert runtime_hash.startswith("sha256:")
    assert '"entries"' in canonical


def test_hosted_contributors_become_drskill_entries():
    local = contributor("mine", prov_kind="unmanaged", source=None)
    hosted_hash = "sha256:" + "cd" * 32
    manifest, notes = manifest_build.contributors_to_manifest(
        [local], hosted={local.id: {"content_hash": hosted_hash,
                                    "source_reference": "drew/mine@1"}})
    entry = manifest["entries"][0]
    assert entry["source_type"] == "drskill"
    assert entry["source_reference"] == "drew/mine@1"
    assert entry["content_hash"] == hosted_hash
    assert entry["local_only"] is False


def test_hosted_map_does_not_touch_other_contributors():
    tracked = contributor("theirs")
    manifest, _ = manifest_build.contributors_to_manifest(
        [tracked], hosted={"/tmp/other": {"content_hash": "sha256:" + "cd" * 32,
                                          "source_reference": "drew/other@1"}})
    assert manifest["entries"][0]["source_type"] == "github"


def test_is_local_matches_the_local_only_rule():
    assert manifest_build.is_local(contributor("a", prov_kind="unmanaged", source=None))
    assert manifest_build.is_local(contributor("b", prov_kind="gh-skill", source=None))
    assert not manifest_build.is_local(contributor("c"))


def test_github_entries_record_fetch_metadata(monkeypatch):
    from drskill import content
    monkeypatch.setattr(content, "collect_files",
        lambda c: [{"path": "SKILL.md", "data": b"x", "executable": False}])
    c = contributor("citation")
    c.source.path = "skills/citation"
    c.source.ref = "v1.2.0"
    c.frontmatter["tree_sha"] = "abc123"
    manifest, _ = manifest_build.contributors_to_manifest([c])
    md = manifest["entries"][0]["metadata"]
    assert md["repo"] == "friend/skill"
    assert md["skill_path"] == "skills/citation"
    assert md["ref"] == "v1.2.0"
    assert md["tree_sha"] == "abc123"
    assert md["directory_hash"].startswith("sha256:")


def test_unreadable_directory_omits_the_hash(monkeypatch):
    from drskill import content

    def boom(c):
        raise OSError("gone")

    monkeypatch.setattr(content, "collect_files", boom)
    manifest, _ = manifest_build.contributors_to_manifest([contributor("citation")])
    md = manifest["entries"][0]["metadata"]
    assert "directory_hash" not in md
    assert md["repo"] == "friend/skill"


def test_parse_repo():
    assert manifest_build.parse_repo("friend/skill@v1") == "friend/skill"
    assert manifest_build.parse_repo("friend/skill") == "friend/skill"
    assert manifest_build.parse_repo("https://github.com/friend/skill.git") == "friend/skill"
    assert manifest_build.parse_repo("github:friend/skill") == "friend/skill"
    assert manifest_build.parse_repo("not a repo") is None
    assert manifest_build.parse_repo(None) is None


def test_local_and_hosted_entries_get_no_fetch_metadata(monkeypatch):
    from drskill import content
    monkeypatch.setattr(content, "collect_files",
        lambda c: [{"path": "SKILL.md", "data": b"x", "executable": False}])
    local = contributor("mine", prov_kind="unmanaged", source=None)
    hosted = contributor("uploaded", prov_kind="unmanaged", source=None)
    manifest, _ = manifest_build.contributors_to_manifest(
        [local, hosted], hosted={hosted.id: {"content_hash": "sha256:" + "cd" * 32,
                                             "source_reference": "drew/uploaded@1"}})
    assert manifest["entries"][0]["metadata"] == {}
    assert manifest["entries"][1]["metadata"] == {}


def test_github_metadata_records_the_file_list(monkeypatch):
    from drskill import content
    monkeypatch.setattr(content, "collect_files",
        lambda c: [{"path": "b.md", "data": b"2", "executable": False},
                   {"path": "a.md", "data": b"1", "executable": False}])
    manifest, _ = manifest_build.contributors_to_manifest([contributor("citation")])
    md = manifest["entries"][0]["metadata"]
    assert md["files"] == ["a.md", "b.md"]


def make_server(**overrides):
    from drskill.mcp import MCPServer

    fields = dict(
        name="Notion", harness="claude-code", scope="project",
        source="/tmp/.mcp.json", transport="stdio",
        command="npx", args=["-y", "notion-mcp"],
        env_names=["NOTION_TOKEN"], config_hash="cc" * 32,
    )
    fields.update(overrides)
    return MCPServer(**fields)


def test_server_to_entry_stdio():
    entry = manifest_build.server_to_entry(make_server(), ["search", "create-page"])
    assert entry["kind"] == "mcp"
    assert entry["selector"] == "mcp:notion"
    assert entry["name"] == "notion"
    assert entry["source_type"] == "mcp"
    assert entry["source_reference"] == "npx -y notion-mcp"
    assert entry["content_hash"] == "sha256:" + "cc" * 32
    assert entry["local_only"] is False
    md = entry["metadata"]
    assert md["server_name"] == "Notion"
    assert md["transport"] == "stdio"
    assert md["command"] == "npx"
    assert md["args"] == ["-y", "notion-mcp"]
    assert md["url"] is None
    assert md["env_names"] == ["NOTION_TOKEN"]
    assert md["tools"] == ["create-page", "search"]


def test_server_to_entry_http():
    server = make_server(transport="http", command=None, args=[],
                         url="https://mcp.example.com/sse", env_names=[])
    entry = manifest_build.server_to_entry(server, [])
    assert entry["source_reference"] == "https://mcp.example.com/sse"
    assert entry["metadata"]["transport"] == "http"
    assert entry["metadata"]["url"] == "https://mcp.example.com/sse"


def test_portability_notes_flag_machine_local_paths():
    notes = manifest_build.server_portability_notes(
        make_server(command="/Users/drew/bin/server", args=["--db", "~/data.db"]))
    assert len(notes) == 2
    assert "/Users/drew/bin/server" in notes[0]
    assert "~/data.db" in notes[1]


def test_portability_notes_accept_resolvable_commands():
    assert manifest_build.server_portability_notes(make_server()) == []


def test_portability_notes_flag_windows_paths():
    notes = manifest_build.server_portability_notes(
        make_server(command="C:\\tools\\server.exe", args=["\\\\share\\data"]))
    assert len(notes) == 2
    assert "C:\\tools\\server.exe" in notes[0]


def test_portability_notes_ignore_bare_relative_values():
    assert manifest_build.server_portability_notes(
        make_server(command="npx", args=["owner/repo", "bin/server"])) == []
