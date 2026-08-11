import json
import shutil

from threadkeeper import collect, db


def test_sweep_skips_claude_subagent_jsonl(conn, tmp_path, fixtures_dir):
    """Subagent transcripts share the parent sessionId; ingesting them would
    wipe the main session via replace_session. Sweep must only take top-level
    session JSONL (Chronicle's non-recursive scan)."""
    root = tmp_path / "claude_root"
    proj = root / "my-project"
    sub = proj / "cc-session-1" / "subagents"
    sub.mkdir(parents=True)
    shutil.copyfile(fixtures_dir / "claude_code" / "cc-session-1.jsonl", proj / "cc-session-1.jsonl")
    # sidechain-only agent file that claims the same sessionId
    (sub / "agent-deadbeef.jsonl").write_text(
        json.dumps(
            {
                "sessionId": "cc-session-1",
                "isSidechain": True,
                "type": "user",
                "message": {"content": "agent prompt"},
                "uuid": "agent-u1",
                "timestamp": "2026-07-01T10:00:00Z",
            }
        )
        + "\n"
    )

    results = collect.collect_sweep(
        conn,
        sources=("claude-code",),
        claude_root=str(root),
    )
    assert results["claude-code"].ingested == 1
    assert results["claude-code"].skipped_unchanged == 0
    row = conn.execute("SELECT message_count FROM sessions WHERE id = ?", ("cc-session-1",)).fetchone()
    assert row is not None
    assert row["message_count"] > 0  # main session kept, not wiped by empty sidechain


def test_sweep_cold_start_backfills_claude_and_codex(conn, fixtures_dir):
    results = collect.collect_sweep(
        conn,
        claude_root=str(fixtures_dir / "claude_code"),
        codex_root=str(fixtures_dir / "codex"),
        cursor_root=str(fixtures_dir / "does-not-exist"),
    )
    assert results["claude-code"].ingested == 1
    assert results["codex"].ingested == 1
    assert results["claude-code"].errored == 0
    assert results["codex"].errored == 0

    sessions = conn.execute("SELECT id, source FROM sessions ORDER BY id").fetchall()
    ids = {row["id"] for row in sessions}
    assert "cc-session-1" in ids
    assert "codex-0198-xyz" in ids


def test_sweep_twice_back_to_back_writes_zero_net_new_rows(conn, fixtures_dir):
    sources = ("claude-code", "codex")
    collect.collect_sweep(conn, sources=sources, claude_root=str(fixtures_dir / "claude_code"), codex_root=str(fixtures_dir / "codex"))
    session_count_1 = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    message_count_1 = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]

    results = collect.collect_sweep(conn, sources=sources, claude_root=str(fixtures_dir / "claude_code"), codex_root=str(fixtures_dir / "codex"))
    assert results["claude-code"].ingested == 0
    assert results["claude-code"].skipped_unchanged == 1
    assert results["codex"].ingested == 0
    assert results["codex"].skipped_unchanged == 1

    session_count_2 = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    message_count_2 = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert session_count_1 == session_count_2
    assert message_count_1 == message_count_2


def test_sweep_reingests_a_grown_file_via_whole_session_replace(conn, tmp_path, fixtures_dir):
    claude_root = tmp_path / "claude_root"
    claude_root.mkdir()
    target = claude_root / "cc-session-1.jsonl"
    shutil.copyfile(fixtures_dir / "claude_code" / "cc-session-1.jsonl", target)

    sources = ("claude-code", "codex")
    collect.collect_sweep(conn, sources=sources, claude_root=str(claude_root), codex_root=str(tmp_path / "no-codex"))
    before = conn.execute("SELECT message_count FROM sessions WHERE id = ?", ("cc-session-1",)).fetchone()

    # simulate the file growing with one more turn (appended, then whole file re-read)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "sessionId": "cc-session-1",
                    "type": "user",
                    "message": {"content": "One more question"},
                    "uuid": "u6",
                    "timestamp": "2026-07-01T10:00:30Z",
                }
            )
            + "\n"
        )

    result = collect.collect_sweep(conn, sources=sources, claude_root=str(claude_root), codex_root=str(tmp_path / "no-codex"))
    assert result["claude-code"].ingested == 1

    after = conn.execute("SELECT message_count FROM sessions WHERE id = ?", ("cc-session-1",)).fetchone()
    assert after["message_count"] == before["message_count"] + 1
    # no duplicate rows: exactly one session row, messages == new total
    assert conn.execute("SELECT COUNT(*) AS c FROM sessions WHERE id = ?", ("cc-session-1",)).fetchone()["c"] == 1
    msg_count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", ("cc-session-1",)).fetchone()["c"]
    assert msg_count == after["message_count"]


def test_sweep_host_tagging_for_copied_in_other_laptop_logs(conn, fixtures_dir):
    collect.collect_sweep(
        conn,
        sources=("claude-code",),
        claude_root=str(fixtures_dir / "claude_code"),
        host="laptopB",
    )
    row = conn.execute("SELECT host FROM sessions WHERE id = ?", ("cc-session-1",)).fetchone()
    assert row["host"] == "laptopB"

    # a later local sweep with no --host preserves the origin host
    collect.collect_sweep(
        conn,
        sources=("claude-code",),
        claude_root=str(fixtures_dir / "claude_code"),
    )
    row = conn.execute("SELECT host FROM sessions WHERE id = ?", ("cc-session-1",)).fetchone()
    assert row["host"] == "laptopB"


def test_fast_path_claude_code_ingests_named_session(conn, fixtures_dir):
    ingested = collect.collect_fast_path(
        conn,
        source="claude-code",
        session_id="cc-session-1",
        transcript=str(fixtures_dir / "claude_code" / "cc-session-1.jsonl"),
    )
    assert ingested is True
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", ("cc-session-1",)).fetchone()
    assert row is not None


def test_fast_path_claude_code_without_transcript_searches_root(conn, fixtures_dir):
    ingested = collect.collect_fast_path(
        conn,
        source="claude-code",
        session_id="cc-session-1",
        claude_root=str(fixtures_dir / "claude_code"),
    )
    assert ingested is True


def test_fast_path_unknown_session_raises(conn, fixtures_dir):
    import pytest

    with pytest.raises(FileNotFoundError):
        collect.collect_fast_path(
            conn,
            source="claude-code",
            session_id="does-not-exist",
            claude_root=str(fixtures_dir / "claude_code"),
        )


def test_fast_path_codex_finds_file_by_id_without_prefix(conn, fixtures_dir):
    ingested = collect.collect_fast_path(
        conn,
        source="codex",
        session_id="0198-xyz",
        codex_root=str(fixtures_dir / "codex"),
    )
    assert ingested is True
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", ("codex-0198-xyz",)).fetchone()
    assert row is not None


def test_collect_codex_notify_applies_idle_heuristic_on_newest_file(conn, fixtures_dir):
    ingested = collect.collect_codex_notify(conn, codex_root=str(fixtures_dir / "codex"))
    assert ingested is True
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", ("codex-0198-xyz",)).fetchone()
    assert row is not None


def test_fast_path_cursor_finds_workspace_by_session_id(conn, cursor_fixture):
    user_dir, _ws_dir = cursor_fixture
    ingested = collect.collect_fast_path(
        conn,
        source="cursor",
        session_id="cursor-chat-tab1",
        cursor_root=str(user_dir),
    )
    assert ingested is True
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", ("cursor-chat-tab1",)).fetchone()
    assert row is not None
    # ingesting one session in a workspace ingests its siblings too (whole-file replace)
    other = conn.execute("SELECT id FROM sessions WHERE id = ?", ("cursor-composer-comp1",)).fetchone()
    assert other is not None


def test_status_reports_counts_and_watermarks(conn, fixtures_dir):
    collect.collect_sweep(
        conn,
        sources=("claude-code", "codex"),
        claude_root=str(fixtures_dir / "claude_code"),
        codex_root=str(fixtures_dir / "codex"),
    )
    info = collect.status(
        conn,
        claude_root=str(fixtures_dir / "claude_code"),
        codex_root=str(fixtures_dir / "codex"),
        cursor_root=str(fixtures_dir / "does-not-exist"),
    )
    assert info["counts"]["sessions"] == 2
    assert info["sessions_by_source"]["claude-code"] == 1
    assert info["sessions_by_source"]["codex"] == 1
    assert len(info["watermarks"]) == 2


def _roots(fixtures_dir):
    return dict(
        claude_root=str(fixtures_dir / "claude_code"),
        codex_root=str(fixtures_dir / "codex"),
        cursor_root=str(fixtures_dir / "does-not-exist"),
    )


def test_freshness_flags_pending_before_sweep_and_clears_after(conn, fixtures_dir):
    roots = _roots(fixtures_dir)

    # Cold start: every on-disk file is pending (nothing collected yet).
    before = collect.source_freshness(conn, **roots)
    assert before["claude-code"] == {
        "disk_files": 1,
        "newest_disk": before["claude-code"]["newest_disk"],
        "newest_collected": None,
        "pending": 1,
        "stale": True,
    }
    assert before["claude-code"]["newest_disk"] is not None
    assert before["codex"]["pending"] == 1 and before["codex"]["stale"] is True
    # A missing source dir is not stale — zero files, nothing to collect.
    assert before["cursor"] == {
        "disk_files": 0,
        "newest_disk": None,
        "newest_collected": None,
        "pending": 0,
        "stale": False,
    }

    # After a sweep, the JSONL sources are fully caught up.
    collect.collect_sweep(conn, sources=("claude-code", "codex"), **roots)
    after = collect.source_freshness(conn, **roots)
    for src in ("claude-code", "codex"):
        assert after[src]["pending"] == 0
        assert after[src]["stale"] is False
        assert after[src]["newest_collected"] is not None


def test_freshness_repending_when_a_file_changes(conn, fixtures_dir, tmp_path):
    # Copy the codex fixture into a writable root so we can mutate it post-sweep.
    codex_root = tmp_path / "codex"
    (codex_root).mkdir()
    src_file = next((fixtures_dir / "codex").rglob("*.jsonl"))
    target = codex_root / src_file.name
    target.write_bytes(src_file.read_bytes())

    roots = dict(
        claude_root=str(fixtures_dir / "does-not-exist"),
        codex_root=str(codex_root),
        cursor_root=str(fixtures_dir / "does-not-exist"),
    )
    collect.collect_sweep(conn, sources=("codex",), **roots)
    assert collect.source_freshness(conn, **roots)["codex"]["pending"] == 0

    # Appending a line changes size+mtime → the change-detector re-flags it.
    with target.open("a") as fh:
        fh.write("\n")
    fresh = collect.source_freshness(conn, **roots)["codex"]
    assert fresh["pending"] == 1
    assert fresh["stale"] is True


def test_sweep_force_reingests_unchanged_files(conn, fixtures_dir):
    # force=True bypasses the change-detection watermark and re-parses every file
    # (used to backfill a parser/schema change into an existing store).
    sources = ("claude-code", "codex")
    kw = dict(claude_root=str(fixtures_dir / "claude_code"), codex_root=str(fixtures_dir / "codex"))
    collect.collect_sweep(conn, sources=sources, **kw)

    plain = collect.collect_sweep(conn, sources=sources, **kw)
    assert sum(r.ingested for r in plain.values()) == 0
    assert sum(r.skipped_unchanged for r in plain.values()) > 0

    forced = collect.collect_sweep(conn, sources=sources, force=True, **kw)
    assert sum(r.ingested for r in forced.values()) > 0
    assert sum(r.skipped_unchanged for r in forced.values()) == 0
