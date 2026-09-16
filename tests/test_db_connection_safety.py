"""SQLite connection safety: no cross-thread connection sharing.

Regression tests for the corruption root cause. A single connection shared by
many threads lets concurrent BEGIN/COMMIT/ROLLBACK interleave and corrupt the
database file, so every engine/session must hand each thread its own
connection.
"""

from __future__ import annotations

import threading

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from src.utils.database import create_db_engine


def _uri(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'safety.db'}"


def test_engine_never_shares_connections(tmp_path):
    engine = create_db_engine(_uri(tmp_path))
    try:
        assert isinstance(engine.pool, NullPool)
        assert not isinstance(engine.pool, StaticPool)
    finally:
        engine.dispose()


def test_engine_connections_get_safe_pragmas(tmp_path):
    engine = create_db_engine(_uri(tmp_path))
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 30000
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


def test_concurrent_orm_sessions_keep_integrity(tmp_path):
    engine = create_db_engine(_uri(tmp_path))
    Session = sessionmaker(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")

    errors: list[BaseException] = []

    def worker(n: int) -> None:
        session = Session()
        try:
            for i in range(25):
                session.execute(text("INSERT INTO t (v) VALUES (:v)"), {"v": f"{n}-{i}"})
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    try:
        assert not errors, errors
        with engine.connect() as conn:
            result = conn.exec_driver_sql("PRAGMA integrity_check").fetchall()
        assert result == [("ok",)], result
    finally:
        engine.dispose()


def test_knowledge_manager_connection_is_per_thread(monkeypatch):
    import src.utils.knowledge_manager as km_module
    from src.utils.knowledge_manager import KnowledgeManager

    # Never share a connection object across threads.
    monkeypatch.setattr(km_module, "get_db_connection", lambda: object())

    manager = object.__new__(KnowledgeManager)
    manager._local = threading.local()

    seen: dict[int, object] = {}
    barrier = threading.Barrier(4)

    def grab(n: int) -> None:
        first = manager.conn
        barrier.wait()
        seen[n] = first

    threads = [threading.Thread(target=grab, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 4
    assert len({id(conn) for conn in seen.values()}) == 4
