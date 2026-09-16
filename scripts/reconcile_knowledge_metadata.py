"""Reconcile knowledge base metadata (SQLite) from ChromaDB vectors.

The knowledge retrieval pipeline stores document/chunk TEXT and vectors in
ChromaDB (collection ``knowledge_chunks``) and the document metadata in the
app SQLite DB (``knowledge_documents``/``knowledge_chunks``). If the SQLite
metadata is missing/empty while ChromaDB still holds the vectors (e.g. after a
DB restore), retrieval fails with "no documents" even though the vectors exist.

This script rebuilds the SQLite metadata rows from the ChromaDB collection so
``KnowledgeManager`` retrieval, ``list_documents`` and ``get_document_info``
work again. Safe to run repeatedly (idempotent).

Usage:  python scripts/reconcile_knowledge_metadata.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

CHROMA_URL = os.environ.get("CHROMADB_SERVICE_URL", "http://localhost:8001")
DB_PATH = os.environ.get("DATABASE_URI", "sqlite:///text2sql.db")
if DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH[len("sqlite:///"):]
DB_PATH = os.path.abspath(DB_PATH)

# Map document_id -> (original_filename, content_type) for known documents whose
# source files exist under uploads/. Entry can be extended if new docs appear.
DOC_META: dict[str, tuple[str, str]] = {}


def _looks_like_doc(meta: dict[str, Any], doc_id: str) -> bool:
    return bool(meta.get("chunk_id") or doc_id)


def main() -> None:
    resp = requests.get(
        f"{CHROMA_URL}/collections/knowledge_chunks/documents",
        params={"limit": 5000},
        timeout=30,
    )
    resp.raise_for_status()
    docs = resp.json().get("documents", [])
    if not docs:
        print("No chunks in ChromaDB knowledge_chunks; nothing to reconcile.")
        return

    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        cur = conn.cursor()
        now = datetime.now().isoformat()
        uploads = Path(APP_ROOT) / "uploads"

        by_doc: dict[str, list[tuple[str, str, str]]] = {}
        for doc in docs:
            meta = doc.get("metadata") or {}
            did = meta.get("document_id")
            cid = meta.get("chunk_id") or doc.get("id")
            if not did or not cid:
                continue
            by_doc.setdefault(did, []).append((cid, doc.get("document") or "", meta.get("query_text") or ""))

        for did, chunks in by_doc.items():
            fname, ctype = DOC_META.get(did, (f"document_{str(did)[:8]}.txt", "txt"))
            fpath = str(uploads / fname)
            cur.execute("SELECT id FROM knowledge_documents WHERE id=?", (did,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO knowledge_documents "
                    "(id, original_filename, file_path, content_type, status, created_at, updated_at, processed_at, error) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (did, fname, fpath, ctype, "completed", now, now, now, None),
                )
                print(f"  + doc {did} -> {fname} ({len(chunks)} chunks)")
            for idx, (cid, content, _q) in enumerate(chunks):
                cur.execute("SELECT id FROM knowledge_chunks WHERE id=?", (cid,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO knowledge_chunks "
                        "(id, document_id, chunk_index, content, embedding_id, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (cid, did, idx, content, cid, now),
                    )

        conn.commit()
        cur.execute("SELECT COUNT(*) FROM knowledge_documents")
        nd = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM knowledge_chunks")
        nc = cur.fetchone()[0]
        print(f"Done: knowledge_documents={nd} knowledge_chunks={nc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
