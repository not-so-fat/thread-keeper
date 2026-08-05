from threadkeeper import db


def _sample_session(**overrides):
    session = {
        "id": "s1",
        "project_id": None,
        "source": "claude-code",
        "file_path": "/tmp/s1.jsonl",
        "started_at": "2026-07-01T10:00:00Z",
        "ended_at": "2026-07-01T10:05:00Z",
        "first_prompt": "hello",
        "context_tokens": 100,
        "summary": "a summary",
        "usage": None,
    }
    session.update(overrides)
    return session


def test_schema_creates_all_tables(conn):
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "sessions", "messages", "collection_state"} <= tables


def test_upsert_project_is_idempotent(conn):
    id1 = db.upsert_project(conn, "/repo/one")
    id2 = db.upsert_project(conn, "/repo/one")
    assert id1 == id2
    count = conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
    assert count == 1


def test_upsert_project_name_is_basename(conn):
    pid = db.upsert_project(conn, "/repo/my-project")
    row = conn.execute("SELECT name FROM projects WHERE id = ?", (pid,)).fetchone()
    assert row["name"] == "my-project"


def test_replace_session_inserts_session_and_messages(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = _sample_session(project_id=pid)
    events = [
        {"kind": "user", "text": "hi"},
        {"kind": "assistant", "text": "hello", "model": "claude-opus-4-8"},
    ]
    db.replace_session(conn, session, events)

    row = conn.execute("SELECT * FROM sessions WHERE id = ?", ("s1",)).fetchone()
    assert row["message_count"] == 2
    assert row["first_prompt"] == "hello"
    msgs = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY seq", ("s1",)).fetchall()
    assert [m["seq"] for m in msgs] == [0, 1]
    assert [m["kind"] for m in msgs] == ["user", "assistant"]


def test_replace_session_is_idempotent_net_zero_rows(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = _sample_session(project_id=pid)
    events = [{"kind": "user", "text": "hi"}]
    db.replace_session(conn, session, events)
    db.replace_session(conn, session, events)

    sessions_count = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    messages_count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert sessions_count == 1
    assert messages_count == 1


def test_replace_session_preserves_user_set_name_across_reimport(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = _sample_session(project_id=pid)
    db.replace_session(conn, session, [{"kind": "user", "text": "hi"}])

    conn.execute("UPDATE sessions SET name = ? WHERE id = ?", ("My Renamed Session", "s1"))
    conn.commit()

    # re-import with a freshly re-derived summary/usage; name must survive
    session2 = _sample_session(project_id=pid, summary="a new re-derived summary")
    db.replace_session(conn, session2, [{"kind": "user", "text": "hi"}, {"kind": "assistant", "text": "hey"}])

    row = conn.execute("SELECT name, summary, message_count FROM sessions WHERE id = ?", ("s1",)).fetchone()
    assert row["name"] == "My Renamed Session"
    assert row["summary"] == "a new re-derived summary"
    assert row["message_count"] == 2


def test_replace_session_preserves_host_unless_explicit(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = _sample_session(project_id=pid)

    db.replace_session(conn, session, [{"kind": "user", "text": "hi"}], host="laptopB", host_explicit=True)
    row = conn.execute("SELECT host FROM sessions WHERE id = ?", ("s1",)).fetchone()
    assert row["host"] == "laptopB"

    # a later local sweep (no --host) must preserve the origin host
    db.replace_session(conn, session, [{"kind": "user", "text": "hi"}], host="local-machine", host_explicit=False)
    row = conn.execute("SELECT host FROM sessions WHERE id = ?", ("s1",)).fetchone()
    assert row["host"] == "laptopB"

    # an explicit --host overrides it
    db.replace_session(conn, session, [{"kind": "user", "text": "hi"}], host="laptopC", host_explicit=True)
    row = conn.execute("SELECT host FROM sessions WHERE id = ?", ("s1",)).fetchone()
    assert row["host"] == "laptopC"


def test_new_session_defaults_to_given_host_when_no_prior_row(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = _sample_session(project_id=pid)
    db.replace_session(conn, session, [{"kind": "user", "text": "hi"}], host="my-mac", host_explicit=False)
    row = conn.execute("SELECT host FROM sessions WHERE id = ?", ("s1",)).fetchone()
    assert row["host"] == "my-mac"


def test_collection_state_change_detection(conn):
    assert db.has_changed(conn, "/tmp/a.jsonl", size=10, mtime="2026-01-01T00:00:00") is True
    db.set_collection_state(conn, "/tmp/a.jsonl", "claude-code", last_size=10, last_mtime="2026-01-01T00:00:00")
    assert db.has_changed(conn, "/tmp/a.jsonl", size=10, mtime="2026-01-01T00:00:00") is False
    assert db.has_changed(conn, "/tmp/a.jsonl", size=11, mtime="2026-01-01T00:00:00") is True
    assert db.has_changed(conn, "/tmp/a.jsonl", size=10, mtime="2026-01-01T00:00:01") is True


def test_collection_state_hash_based_change_detection(conn):
    db.set_collection_state(conn, "/tmp/state.vscdb", "cursor", last_hash="abc")
    assert db.has_changed(conn, "/tmp/state.vscdb", content_hash="abc") is False
    assert db.has_changed(conn, "/tmp/state.vscdb", content_hash="def") is True


def test_replace_session_persists_injected_flag(conn):
    pid = db.upsert_project(conn, "/repo/one")
    session = {"id": "s-inj", "project_id": pid, "source": "claude-code",
               "file_path": "/tmp/s-inj.jsonl", "started_at": "2026-07-01T10:00:00Z"}
    events = [
        {"kind": "user", "text": "real human", "ts": "2026-07-01T10:00:00Z"},
        {"kind": "user", "text": "<task-notification>", "ts": "2026-07-01T10:00:01Z", "injected": True},
    ]
    db.replace_session(conn, session, events)
    rows = conn.execute(
        "SELECT text, injected FROM messages WHERE session_id='s-inj' ORDER BY seq"
    ).fetchall()
    assert rows[0]["injected"] == 0
    assert rows[1]["injected"] == 1
