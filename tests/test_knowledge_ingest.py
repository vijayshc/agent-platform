"""Focused tests for upload-time ingestion properties.

Covers the pure chunking strategies, the CSV/Excel column-role model, option
validation, and the schema/round-trip of structured chunk metadata.  The live
end-to-end path (upload -> embed -> retrieve) is exercised against the running
app, not here.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid

import pandas as pd
import pytest

from src.utils import chunking, knowledge_ingest, tabular_ingest
from src.utils.knowledge_manager import KnowledgeManager

_PROSE = " ".join(f"Sentence number {i} explains policy {i}." for i in range(80))
_PROSE += "\n\n# Section Two\n\n" + " ".join(f"Rule {i} requires annual review {i}." for i in range(80))


# --------------------------------------------------------------------------
# chunking strategies
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method", sorted(chunking.METHODS))
def test_every_method_produces_chunks(method):
    chunks = chunking.chunk_text(_PROSE, 300, 50, method)
    assert chunks, f"{method} produced no chunks"
    assert all(c.strip() for c in chunks)
    assert len(set(chunks)) == len(chunks)


def test_recursive_respects_size_budget_for_normal_text():
    chunks = chunking.chunk_text(_PROSE, 300, 40, "recursive")
    assert chunks
    # Overlap is prepended after packing, so a chunk is at most size + overlap.
    assert max(len(c) for c in chunks) <= 300 + 40


def test_fixed_windows_are_size_bounded():
    chunks = chunking.chunk_text("x" * 1000, 200, 0, "fixed")
    assert all(len(c) <= 200 for c in chunks)


def test_sentence_never_ends_mid_sentence():
    chunks = chunking.chunk_text("One two three. Four five six. Seven eight nine.", 20, 0, "sentence")
    assert chunks == ["One two three.", "Four five six.", "Seven eight nine."]


def test_markdown_repeats_heading_in_each_part():
    chunks = chunking.chunk_text("# Title\nalpha beta gamma\n\n# Other\ndelta epsilon", 16, 0, "markdown")
    title_parts = [c for c in chunks if c.startswith("# Title")]
    assert title_parts and all("Title" in c for c in title_parts)


def test_overlap_makes_consecutive_chunks_share_text():
    chunks = chunking.chunk_text(_PROSE, 300, 60, "recursive")
    assert len(chunks) >= 2
    assert chunks[0].split()[-1] in chunks[1].split()[:20]


def test_unknown_method_and_bad_size_are_rejected():
    with pytest.raises(ValueError):
        chunking.normalize_method("mystery")
    with pytest.raises(ValueError):
        chunking.validate_size(10, 1000)
    with pytest.raises(ValueError):
        chunking.validate_size("abc", 1000)
    with pytest.raises(ValueError):
        chunking.validate_overlap(-5, 500, 200)
    assert chunking.validate_overlap(9999, 500, 200) <= 250


def test_empty_text_yields_no_chunks():
    assert chunking.chunk_text("   ", 500, 50, "recursive") == []


# --------------------------------------------------------------------------
# option normalisation
# --------------------------------------------------------------------------

def test_normalize_options_accepts_json_and_csv_lists():
    options = knowledge_ingest.normalize_options({
        "chunking_method": "Sentence",
        "chunk_size": "800",
        "chunk_overlap": "100",
        "data_columns": '["name", "notes"]',
        "metadata_columns": "dept, year",
        "collection_name": "hr_records",
    })
    assert options.chunking_method == "sentence"
    assert (options.chunk_size, options.chunk_overlap) == (800, 100)
    assert options.data_columns == ["name", "notes"]
    assert options.metadata_columns == ["dept", "year"]
    assert options.collection_name == "hr_records"


def test_normalize_options_defaults_to_knowledge_collection():
    assert knowledge_ingest.normalize_options({}).collection_name == "knowledge_chunks"


def test_normalize_options_rejects_unknown_method():
    with pytest.raises(ValueError, match="Unknown chunking method"):
        knowledge_ingest.normalize_options({"chunking_method": "nope"})


def test_columns_json_round_trip():
    assert knowledge_ingest.decode_columns(knowledge_ingest.IngestOptions(
        metadata_columns=["a"], data_columns=["b"]
    ).to_columns()["data_columns"]) == ["b"]
    assert knowledge_ingest.decode_columns(None) == []


# --------------------------------------------------------------------------
# tabular column roles
# --------------------------------------------------------------------------

@pytest.fixture()
def csv_file(tmp_path):
    path = tmp_path / "people.csv"
    pd.DataFrame({
        "id": ["001", "002", "003"],
        "name": ["Ada", "Grace", "Alan"],
        "dept": ["Eng", "Eng", "Sales"],
        "notes": ["", "pioneer", "maths"],
    }).to_csv(path, index=False)
    return path


def test_inspect_table_reports_columns_and_preview(csv_file):
    info = tabular_ingest.inspect_table(str(csv_file), "csv")
    assert info["columns"] == ["id", "name", "dept", "notes"]
    assert info["row_count"] == 3
    assert info["sample_rows"][0]["id"] == "001"  # leading zeros preserved


def test_build_row_chunks_embeds_data_and_keeps_metadata_out(csv_file):
    chunks = tabular_ingest.build_row_chunks(
        str(csv_file), "csv", data_columns=["id", "name", "notes"], metadata_columns=["dept"]
    )
    assert len(chunks) == 3
    assert chunks[0]["content"] == "id: 001\nname: Ada"
    assert "dept" not in chunks[0]["content"]
    assert chunks[0]["metadata"] == {"dept": "Eng", "row_number": 2}
    # A fully blank record is skipped rather than embedded as noise.
    assert all(c["content"].strip() for c in chunks)


def test_build_row_chunks_rejects_unknown_data_columns(csv_file):
    with pytest.raises(ValueError, match="data columns"):
        tabular_ingest.build_row_chunks(str(csv_file), "csv", ["missing"], [])


def test_is_tabular_only_for_spreadsheets():
    assert tabular_ingest.is_tabular("csv") and tabular_ingest.is_tabular("XLSX")
    assert not tabular_ingest.is_tabular("pdf")


# --------------------------------------------------------------------------
# schema + metadata persistence
# --------------------------------------------------------------------------

def _bare_manager(temp_db):
    manager = object.__new__(KnowledgeManager)
    manager._local = threading.local()
    manager.processing_status = {}
    manager.logger = logging.getLogger("text2sql.knowledge.ingest-test")
    manager.vector_store = None
    manager.llm_engine = None
    manager.md_converter = None
    manager._create_tables()
    return manager


def test_ingest_columns_and_chunk_metadata_round_trip(temp_db):
    manager = _bare_manager(temp_db)
    cursor = manager.conn.cursor()
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(knowledge_documents)")}
    assert {"chunking_method", "chunk_size", "chunk_overlap", "metadata_columns", "data_columns", "collection_name"} <= columns

    now = "2024-01-01T00:00:00"
    doc_id, chunk_id = str(uuid.uuid4()), str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO knowledge_documents (id, original_filename, file_path, content_type, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, "people.csv", "/tmp/people.csv", "csv", "completed", now, now),
    )
    knowledge_ingest.insert_chunk_metadata(cursor, doc_id, chunk_id, {"dept": "Eng", "blank": ""})
    manager.conn.commit()

    fetched = knowledge_ingest.fetch_chunk_metadata(cursor, [chunk_id])
    assert fetched[chunk_id] == {"dept": "Eng"}  # empty values are dropped


def test_prepare_chunks_uses_row_path_for_mapped_tabular(temp_db, csv_file):
    manager = _bare_manager(temp_db)
    options = knowledge_ingest.normalize_options({
        "data_columns": ["name", "notes"], "metadata_columns": ["dept"],
    })
    chunks = manager._prepare_chunks(str(csv_file), "csv", options)
    assert [c["content"] for c in chunks] == ["name: Ada", "name: Grace\nnotes: pioneer", "name: Alan\nnotes: maths"]
    assert all("dept" in c["metadata"] for c in chunks)


def test_process_document_persists_ingest_config(temp_db, csv_file):
    manager = _bare_manager(temp_db)
    options = knowledge_ingest.normalize_options({
        "chunking_method": "markdown", "chunk_size": 640, "chunk_overlap": 80,
        "data_columns": ["name"], "metadata_columns": ["dept"],
        "collection_name": "hr_records",
    })
    document_id = manager.process_document(str(csv_file), "people.csv", options=options)
    row = manager.conn.execute(
        "SELECT chunking_method, chunk_size, chunk_overlap, data_columns, metadata_columns, collection_name "
        "FROM knowledge_documents WHERE id = ?", (document_id,)
    ).fetchone()
    assert row[0] == "markdown" and row[1] == 640 and row[2] == 80
    assert knowledge_ingest.decode_columns(row[3]) == ["name"]
    assert knowledge_ingest.decode_columns(row[4]) == ["dept"]
    assert row[5] == "hr_records"
