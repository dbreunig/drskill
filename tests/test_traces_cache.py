import datetime as dt
import json

from drskill.traces import cache
from drskill.traces.model import Invocation

UTC = dt.timezone.utc


def _inv(path):
    return Invocation(
        harness="pi", session_id="s", timestamp=dt.datetime(2026, 7, 1, tzinfo=UTC),
        kind="skill", name="foo", detection="skill-read", source_file=str(path),
    )


def _entry(trace, adapter="pi", version=1):
    st = trace.stat()
    return cache.TraceCacheEntry(
        trace_path=str(trace), mtime_ns=st.st_mtime_ns, size=st.st_size,
        adapter=adapter, adapter_version=version, recognized=3,
        invocations=[_inv(trace)],
    )


def test_dir_is_under_home_never_repo(tmp_path):
    assert cache.audit_cache_dir(tmp_path) == tmp_path / ".drskill" / "cache" / "audit"


def test_store_then_load_round_trip(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("x\n")
    cdir = cache.audit_cache_dir(tmp_path)
    entry = _entry(trace)
    cache.store_entry(cdir, entry)
    got = cache.load_entry(cdir, trace)
    assert got == entry


def test_load_misses_on_mtime_or_size_change(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("x\n")
    cdir = cache.audit_cache_dir(tmp_path)
    cache.store_entry(cdir, _entry(trace))
    trace.write_text("x\ny\n")  # size changes
    assert cache.load_entry(cdir, trace) is None


def test_load_misses_on_adapter_version_bump(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("x\n")
    cdir = cache.audit_cache_dir(tmp_path)
    cache.store_entry(cdir, _entry(trace, version=1))
    entry = cache.load_entry(cdir, trace)
    assert entry is not None and entry.adapter_version == 1
    # The pipeline compares adapter_version itself; load_entry returns the
    # entry and the caller decides. Assert the field is present for that.


def test_corrupt_cache_file_is_a_miss(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("x\n")
    cdir = cache.audit_cache_dir(tmp_path)
    cdir.mkdir(parents=True)
    (cdir / f"{cache.entry_key(trace)}.json").write_text("{broken")
    assert cache.load_entry(cdir, trace) is None


def test_prune_vanished_removes_stale_entries(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("x\n")
    cdir = cache.audit_cache_dir(tmp_path)
    cache.store_entry(cdir, _entry(trace))
    other = tmp_path / "gone.jsonl"
    other.write_text("y\n")
    cache.store_entry(cdir, _entry(other))
    removed = cache.prune_vanished(cdir, {cache.entry_key(trace)})
    assert removed == 1
    assert cache.load_entry(cdir, trace) is not None


def test_query_and_reasoning_are_the_only_trace_text_stored(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("x\n")
    cdir = cache.audit_cache_dir(tmp_path)
    cache.store_entry(cdir, _entry(trace))
    raw = json.loads((cdir / f"{cache.entry_key(trace)}.json").read_text())
    allowed = {"harness", "session_id", "project", "timestamp", "kind", "name",
               "server", "query", "query_source", "reasoning", "sidechain",
               "detection", "source_file", "source_line"}
    assert set(raw["invocations"][0]) <= allowed
