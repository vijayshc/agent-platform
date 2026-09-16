#!/usr/bin/env python3
"""Rebuild a corrupted SQLite database by copying readable rows.

The SQLite build shipped on this host lacks the ``sqlite_dbpage`` extension,
so ``.recover`` is unavailable and ``VACUUM INTO`` aborts on a malformed page.
This script recreates the schema in a fresh database and copies rows table by
table, salvaging around malformed pages by skipping only the affected rows.

Legacy agent trace records (``agent_run_events``) are intentionally dropped:
traces now live in Arize Phoenix and are never persisted to SQLite.

Usage:
    python scripts/recover_database.py text2sql.db            # dry run -> text2sql.recovered
    python scripts/recover_database.py text2sql.db --apply    # backup + replace in place
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sqlite3
import sys
from pathlib import Path

DROP_TABLES = {"agent_run_events"}


def _schema_objects(src: sqlite3.Connection) -> list[tuple[str, str, str]]:
    rows = src.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
        ORDER BY CASE type
            WHEN 'table' THEN 0 WHEN 'index' THEN 1
            WHEN 'trigger' THEN 2 WHEN 'view' THEN 3 ELSE 4 END
        """
    ).fetchall()
    out: list[tuple[str, str, str]] = []
    for obj_type, name, tbl_name, sql in ((r[0], r[1], r[2], r[3]) for r in rows):
        if tbl_name in DROP_TABLES or name in DROP_TABLES:
            continue
        if name.startswith("sqlite_"):
            continue
        out.append((obj_type, name, sql))
    return out


def _table_columns(src: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in src.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _insert_rows(dst: sqlite3.Connection, table: str, rows: list[sqlite3.Row]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    quoted = ",".join(f'"{c}"' for c in cols)
    placeholders = ",".join("?" for _ in cols)
    dst.executemany(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
        [tuple(r) for r in rows],
    )
    return len(rows)


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> tuple[int, list[int]]:
    """Copy a table. Returns (rows_copied, skipped_rowids)."""
    skipped: list[int] = []
    try:
        cur = src.execute(f'SELECT * FROM "{table}"')
        total = 0
        while True:
            batch = cur.fetchmany(1000)
            if not batch:
                break
            total += _insert_rows(dst, table, batch)
        dst.commit()
        return total, skipped
    except sqlite3.Error:
        dst.rollback()

    # Fallback: single-row lookups so only malformed rows are skipped.
    max_rowid = 0
    try:
        max_rowid = src.execute(f'SELECT MAX(rowid) FROM "{table}"').fetchone()[0] or 0
    except sqlite3.Error:
        try:
            max_rowid = src.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)
            ).fetchone()[0]
        except sqlite3.Error:
            max_rowid = 0

    total = 0
    for rowid in range(1, int(max_rowid) + 1):
        try:
            row = src.execute(f'SELECT * FROM "{table}" WHERE rowid = ?', (rowid,)).fetchone()
        except sqlite3.Error:
            skipped.append(rowid)
            continue
        if row is None:
            continue
        try:
            _insert_rows(dst, table, [row])
            total += 1
        except sqlite3.Error:
            skipped.append(rowid)
    dst.commit()
    return total, skipped


def recover(source: Path, out: Path) -> dict:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(str(out), timeout=60)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")
    dst.execute("PRAGMA foreign_keys=OFF")

    report: dict = {"tables": {}, "skipped": {}, "dropped": sorted(DROP_TABLES)}
    objects = _schema_objects(src)

    for obj_type, _name, sql in objects:
        if obj_type == "table":
            try:
                dst.execute(sql)
            except sqlite3.Error as exc:
                report.setdefault("schema_errors", []).append(f"{sql[:60]}...: {exc}")

    for obj_type, name, _sql in objects:
        if obj_type != "table":
            continue
        if not _table_columns(src, name):
            continue
        copied, skipped = _copy_table(src, dst, name)
        report["tables"][name] = copied
        if skipped:
            report["skipped"][name] = skipped

    for obj_type, _name, sql in objects:
        if obj_type in ("index", "trigger", "view"):
            try:
                dst.execute(sql)
            except sqlite3.Error as exc:
                report.setdefault("schema_errors", []).append(f"{obj_type}: {exc}")

    dst.commit()
    report["integrity"] = [r[0] for r in dst.execute("PRAGMA integrity_check").fetchall()]
    dst.close()
    src.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="back up and replace the database in place")
    args = parser.parse_args()

    source: Path = args.database.resolve()
    if not source.exists():
        print(f"error: {source} not found", file=sys.stderr)
        return 2

    out = source.with_suffix(source.suffix + ".recovered")
    if out.exists():
        out.unlink()
    for sidecar in (out.with_name(out.name + "-wal"), out.with_name(out.name + "-shm")):
        if sidecar.exists():
            sidecar.unlink()

    print(f"recovering {source} -> {out}")
    report = recover(source, out)
    print(f"  tables recovered: {len(report['tables'])}")
    for name, count in sorted(report["tables"].items()):
        print(f"    {name:35s} {count}")
    if report["skipped"]:
        print("  skipped (unreadable rowids):")
        for name, rowids in report["skipped"].items():
            shown = rowids[:30]
            more = " ..." if len(rowids) > len(shown) else ""
            print(f"    {name}: {shown}{more} (total {len(rowids)})")
    if report.get("schema_errors"):
        print("  schema errors:")
        for err in report["schema_errors"]:
            print(f"    {err}")
    print(f"  integrity_check: {report['integrity'][:3]}")

    if report["integrity"] != ["ok"]:
        print("error: recovered database did not pass integrity_check; not applying", file=sys.stderr)
        return 1

    if not args.apply:
        print("dry run complete; re-run with --apply to replace the database")
        return 0

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = source.with_suffix(source.suffix + f".corrupt.{stamp}")
    shutil.copy2(source, backup)
    for sidecar in (source.with_name(source.name + "-wal"), source.with_name(source.name + "-shm")):
        if sidecar.exists():
            sidecar.unlink()
    shutil.move(str(out), str(source))
    print(f"applied. backup at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
