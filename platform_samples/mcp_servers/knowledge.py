"""Knowledge Base FastMCP server (stdio).

Exposes the app's knowledge management (RAG) capabilities as MCP tools so any
agent can answer questions from the knowledge base. Backed by
``src.utils.knowledge_manager.KnowledgeManager`` (document chunking, ChromaDB
vector search, reranking, and LLM-grounded answer generation).

The server intentionally defers importing the heavy KnowledgeManager until the
first tool call so an MCP session can list this server's tools without
fast and does not require ChromaDB to be up merely to list the server.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
for _h in logging.root.handlers:
    _h.setStream(sys.stderr)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Knowledge")

_km_instance: Any = None
_km_lock: Any = None


def _lock():
    global _km_lock
    if _km_lock is None:
        import threading

        _km_lock = threading.Lock()
    return _km_lock


def _manager():
    """Lazily build a single KnowledgeManager shared across tool calls.

    KnowledgeManager is heavy (loads the embedding/reranking models, connects to
    ChromaDB, and opens a SQLite handle) so it is created on first use and kept
    alive for the lifetime of the stdio subprocess.
    """
    global _km_instance
    if _km_instance is None:
        with _lock():
            if _km_instance is None:
                from src.utils.knowledge_manager import KnowledgeManager

                _km_instance = KnowledgeManager()
    return _km_instance


def _sources_md(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "No sources."
    lines = ["# Sources\n"]
    for idx, src in enumerate(sources, 1):
        lines.append(
            f"{idx}. **{src.get('document', 'Unknown')}** "
            f"(chunk: {src.get('chunk_id', 'n/a')})"
        )
    return "\n".join(lines)


@mcp.tool()
def search_knowledge(query: str, tags: list[str] | None = None) -> str:
    """Answer a question using the knowledge base (retrieval-augmented generation).

    IMPORTANT: The ``query`` argument is REQUIRED. Pass the user's exact question
    as ``query`` so the knowledge base can be searched for relevant chunks, e.g.
    ``search_knowledge(query="what does teradata policy say")``. Never call this
    tool without providing ``query``.

    Args:
        query: REQUIRED. The user's question to search the knowledge base for.
        tags: Optional list of tags to restrict the search to matching documents.

    Returns:
        Markdown answer (or an error/explanation message), followed by the list
        of source documents used to ground the answer.
    """
    query = (query or "").strip()
    if not query:
        return (
            "Error: the 'query' argument is required. Call search_knowledge with "
            "the user's question, e.g. search_knowledge(query=\"<user question>\")."
        )

    try:
        result = _manager().get_answer(query, user_id=None, stream=False, tags=tags)
    except Exception as exc:  # pragma: no cover - defensive
        return f"Error querying knowledge base: {exc}"

    if not isinstance(result, dict):
        return f"Unexpected result: {result}"

    answer = result.get("answer") or result.get("error") or "No answer generated."
    sources = result.get("sources") or []
    return f"{answer}\n\n{_sources_md(sources)}"


@mcp.tool()
def list_knowledge_documents() -> str:
    """List every document in the knowledge base with status and chunk counts.

    Returns:
        Markdown table of documents (filename, content type, status, chunk
        count, tags, allowed roles).
    """
    try:
        docs = _manager().list_documents()
    except Exception as exc:  # pragma: no cover - defensive
        return f"Error listing documents: {exc}"

    if not docs:
        return "No documents in the knowledge base."

    lines = ["# Knowledge Base Documents\n"]
    lines.append("| Filename | Type | Status | Chunks | Tags |")
    lines.append("| --- | --- | --- | --- | --- |")
    for d in docs:
        lines.append(
            f"| {d.get('filename', '')} | {d.get('content_type', '')} | "
            f"{d.get('status', '')} | {d.get('chunk_count', 0)} | "
            f"{', '.join(d.get('tags', []) or [])} |"
        )
    return "\n".join(lines)


@mcp.tool()
def get_document_info(document_id: str) -> str:
    """Get detailed metadata for a specific knowledge document.

    Args:
        document_id: The document ID (UUID).

    Returns:
        Markdown summary of the document's metadata, tags, and allowed roles.
    """
    document_id = (document_id or "").strip()
    if not document_id:
        return "Error: document_id is required."

    try:
        from src.utils import knowledge_access

        # This stdio server has no authenticated identity, so only public
        # (owner-less) documents are in scope.  Never expose another tenant's
        # document metadata to a background tool call.
        scope = knowledge_access.retrieval_document_ids(None)
        if scope is not None and document_id not in scope:
            return f"Document not found: {document_id}"
        info = _manager().get_document_info(document_id)
    except Exception as exc:  # pragma: no cover - defensive
        return f"Error fetching document info: {exc}"

    if not info:
        return f"Document not found: {document_id}"

    return (
        f"# Document\n"
        f"- **Filename:** {info.get('original_filename', '')}\n"
        f"- **Type:** {info.get('content_type', '')}\n"
        f"- **Status:** {info.get('status', '')}\n"
        f"- **Created:** {info.get('created_at', '')}\n"
        f"- **Processed:** {info.get('processed_at', '')}\n"
        f"- **Chunks:** {info.get('chunk_count', 0)}\n"
        f"- **Tags:** {', '.join(info.get('tags', []) or [])}\n"
        f"- **Allowed roles:** {', '.join(info.get('allowed_roles', []) or [])}"
    )


@mcp.tool()
def list_knowledge_tags() -> str:
    """List all tags used across the knowledge base documents.

    Returns:
        Comma-separated list of unique tags.
    """
    try:
        tags = _manager().get_all_tags()
    except Exception as exc:  # pragma: no cover - defensive
        return f"Error listing tags: {exc}"

    if not tags:
        return "No tags in the knowledge base."
    return "Tags: " + ", ".join(tags)


def main() -> None:
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
    logging.getLogger().handlers = [logging.StreamHandler(sys.stderr)]
    mcp.run()


if __name__ == "__main__":
    main()
