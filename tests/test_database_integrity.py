import pytest
import sqlite3
import threading
from src.utils.database import get_db_connection
from src.agent_platform.conversations.store import ConversationStore


def test_database_integrity_check():
    """Verify that the primary database passes PRAGMA integrity_check."""
    conn = get_db_connection()
    cur = conn.cursor()
    result = cur.execute("PRAGMA integrity_check;").fetchall()
    conn.close()
    assert len(result) == 1 and result[0][0] == "ok", f"Integrity check failed: {result}"


def test_database_pragmas_configured():
    """Verify that get_db_connection applies safe and robust pragmas."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check busy timeout is configured (30s = 30000ms)
    busy_timeout = cur.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert busy_timeout >= 30000, f"Expected busy_timeout >= 30000, got {busy_timeout}"
    
    # Check foreign keys are enabled
    fk = cur.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert fk == 1, f"Expected foreign_keys == 1, got {fk}"
    
    # Check memory mapped I/O is not set to unsafe custom sizes
    mmap = cur.execute("PRAGMA mmap_size;").fetchone()[0]
    assert mmap == 0, f"Expected mmap_size == 0, got {mmap}"
    
    # Check journal mode is WAL
    journal = cur.execute("PRAGMA journal_mode;").fetchone()[0]
    assert journal.upper() == "WAL", f"Expected WAL mode, got {journal}"
    
    conn.close()


def test_conversation_store_upsert_assistant():
    """Verify that ConversationStore message creation and assistant upsert work without errors."""
    ConversationStore.ensure_tables()
    conv = ConversationStore.create(user_id=1, title="Test Integrity Conv")
    cid = conv["id"]
    
    # Add user message
    u_msg = ConversationStore.add_message(
        conversation_id=cid,
        role="user",
        content="Hello assistant",
        run_id=99999
    )
    assert u_msg["id"] is not None
    
    # Upsert assistant message initial
    a_msg = ConversationStore.upsert_assistant_for_run(
        conversation_id=cid,
        run_id=99999,
        content="Streaming chunk 1...",
        meta={"pending": True}
    )
    assert a_msg["content"] == "Streaming chunk 1..."
    
    # Upsert assistant message update (same run_id)
    a_msg_updated = ConversationStore.upsert_assistant_for_run(
        conversation_id=cid,
        run_id=99999,
        content="Streaming chunk 1... chunk 2 finalized.",
        meta={"pending": False}
    )
    assert a_msg_updated["id"] == a_msg["id"]
    assert a_msg_updated["content"] == "Streaming chunk 1... chunk 2 finalized."
    
    # Verify retrieval
    messages = ConversationStore.list_messages(cid)
    assert len(messages) == 2
    assert messages[1]["content"] == "Streaming chunk 1... chunk 2 finalized."


def test_conversation_store_pagination():
    """Verify list/search pagination (limit/offset) and counts behave correctly."""
    ConversationStore.ensure_tables()
    created = []
    for i in range(5):
        conv = ConversationStore.create(user_id=777, title=f"Page Conv {i}", agent_slug="agent")
        created.append(conv["id"])

    try:
        # list_for_user with limit/offset
        page1 = ConversationStore.list_for_user(777, limit=2, offset=0)
        page2 = ConversationStore.list_for_user(777, limit=2, offset=2)
        page3 = ConversationStore.list_for_user(777, limit=2, offset=4)
        assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1
        # Pages must not overlap and together cover all rows.
        ids = [c["id"] for c in page1 + page2 + page3]
        assert len(ids) == len(set(ids)) == 5
        assert ConversationStore.count_for_user(777) == 5

        # search with limit/offset
        hits = ConversationStore.search(777, "page conv", limit=3, offset=0)
        assert len(hits) == 3
        assert ConversationStore.count_search(777, "page conv") == 5

        # No-limit calls still return everything.
        assert len(ConversationStore.list_for_user(777)) == 5
        assert len(ConversationStore.search(777, "page conv")) == 5
    finally:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM agent_conversations WHERE user_id = ?", (777,))
        conn.commit()
        conn.close()


def test_concurrent_writes_integrity():
    """Verify that multiple concurrent threads writing to database maintain integrity."""
    ConversationStore.ensure_tables()
    conv = ConversationStore.create(user_id=1, title="Concurrent Test Conv")
    cid = conv["id"]
    
    errors = []
    
    def worker(worker_id):
        try:
            for i in range(5):
                run_id = 900000 + worker_id * 10 + i
                ConversationStore.add_message(
                    conversation_id=cid,
                    role="user",
                    content=f"User msg from worker {worker_id} iter {i}",
                    run_id=run_id
                )
                ConversationStore.upsert_assistant_for_run(
                    conversation_id=cid,
                    run_id=run_id,
                    content=f"Assistant response for worker {worker_id} iter {i}",
                    meta={"worker": worker_id}
                )
        except Exception as e:
            errors.append((worker_id, e))
            
    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert len(errors) == 0, f"Concurrent workers encountered errors: {errors}"
    
    # Verify DB integrity after concurrent writes
    conn = get_db_connection()
    cur = conn.cursor()
    result = cur.execute("PRAGMA integrity_check;").fetchall()
    conn.close()
    assert len(result) == 1 and result[0][0] == "ok", f"Integrity check failed after concurrency test: {result}"
