"""Knowledge Manager facade for Text2SQL project.

Handles document processing, chunking, embedding, and retrieval.
"""

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from markitdown import MarkItDown

from config.config import UPLOADS_DIR
from src.utils import knowledge_ingest
from src.utils.database import get_db_connection
from src.utils.knowledge import knowledge_db, knowledge_processor, knowledge_qa
from src.utils.llm_engine import LLMEngine
from src.utils.vector_store import VectorStore

# Create logger
logger = logging.getLogger("text2sql.knowledge")


class KnowledgeManager:
    """Manages knowledge base documents, chunking, embedding, and retrieval."""

    def __init__(self):
        """Initialize KnowledgeManager with vector store and database connection."""
        self.logger = logging.getLogger("text2sql.knowledge")
        self.logger.info("Initializing Knowledge Manager")

        os.makedirs(UPLOADS_DIR, exist_ok=True)

        self.md_converter = MarkItDown()

        self.vector_store = VectorStore()
        self.vector_store.connect()
        self.vector_store.init_collection("knowledge_chunks")

        self.llm_engine = LLMEngine()
        self.processing_status = {}

        # One SQLite connection per thread.
        self._local = threading.local()

        self._create_tables()

    @property
    def conn(self):
        """Return this thread's dedicated SQLite connection (created on demand)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = get_db_connection()
            self._local.conn = conn
        return conn

    def _create_tables(self):
        """Create necessary database tables if they don't exist."""
        return knowledge_db.create_tables(self)

    def process_document(
        self,
        file_path: str,
        original_filename: str,
        tags: Optional[List[str]] = None,
        allowed_roles: Optional[List[str]] = None,
        owner_id: Optional[int] = None,
        options: Optional[knowledge_ingest.IngestOptions] = None,
    ) -> str:
        """Process a document, convert to markdown, chunk it and store in vector database."""
        return knowledge_processor.process_document(
            self,
            file_path=file_path,
            original_filename=original_filename,
            tags=tags,
            allowed_roles=allowed_roles,
            owner_id=owner_id,
            options=options,
        )

    def _process_document_async(
        self,
        document_id: str,
        file_path: str,
        content_type: str,
        options: Optional[knowledge_ingest.IngestOptions] = None,
    ):
        """Process document asynchronously."""
        return knowledge_processor.process_document_async(
            self,
            document_id=document_id,
            file_path=file_path,
            content_type=content_type,
            options=options,
        )

    def _prepare_chunks(
        self,
        file_path: str,
        content_type: str,
        options: knowledge_ingest.IngestOptions,
        text: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Build the list of {"content", "metadata"} chunks for a document."""
        return knowledge_processor.prepare_chunks(
            self,
            file_path=file_path,
            content_type=content_type,
            options=options,
            text=text,
        )

    def _chunk_text(self, text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
        """Split text into overlapping chunks with default strategy."""
        return knowledge_processor.chunk_text(self, text, chunk_size, chunk_overlap)

    def process_text_content(
        self,
        content_name: str,
        content_type: str,
        content: str,
        tags: Optional[List[str]] = None,
        allowed_roles: Optional[List[str]] = None,
        owner_id: Optional[int] = None,
        options: Optional[knowledge_ingest.IngestOptions] = None,
    ) -> str:
        """Process text content, chunk it and store in vector database."""
        return knowledge_processor.process_text_content(
            self,
            content_name=content_name,
            content_type=content_type,
            content=content,
            tags=tags,
            allowed_roles=allowed_roles,
            owner_id=owner_id,
            options=options,
        )

    def _process_text_content_async(
        self,
        document_id: str,
        content: str,
        temp_file_path: str,
        options: Optional[knowledge_ingest.IngestOptions] = None,
    ):
        """Process text content asynchronously."""
        return knowledge_processor.process_text_content_async(
            self,
            document_id=document_id,
            content=content,
            temp_file_path=temp_file_path,
            options=options,
        )

    def _save_chunks(
        self,
        document_id: str,
        chunks: List[Any],
        collection_name: Optional[str] = None,
    ):
        """Save chunks to database and create embeddings."""
        return knowledge_processor.save_chunks(
            self,
            document_id=document_id,
            chunks=chunks,
            collection_name=collection_name,
        )

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for a text using the centralized LLM engine."""
        return knowledge_processor.get_embedding(self, text)

    def get_document_status(self, document_id: str) -> Dict[str, Any]:
        """Get the processing status of a document."""
        return knowledge_db.get_document_status(self, document_id)

    def list_documents(
        self,
        user_id: Optional[int] = None,
        user_roles: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """List all documents with their processing status."""
        return knowledge_db.list_documents(self, user_id=user_id, user_roles=user_roles)

    def get_document_tags(self, document_id: str) -> List[str]:
        """Get all tags for a document."""
        return knowledge_db.get_document_tags(self, document_id)

    def get_document_roles(self, document_id: str) -> List[str]:
        """Get all allowed roles for a document."""
        return knowledge_db.get_document_roles(self, document_id)

    def get_all_tags(
        self,
        user_id: Optional[int] = None,
        user_roles: Optional[List[str]] = None,
    ) -> List[str]:
        """Get all unique tags used across all documents."""
        return knowledge_db.get_all_tags(self, user_id=user_id, user_roles=user_roles)

    def _get_document_ids_by_tags(self, tags: List[str]) -> Optional[List[str]]:
        """Get document IDs that have all of the specified tags."""
        return knowledge_db.get_document_ids_by_tags(self, tags)

    def add_document_tag(self, document_id: str, tag: str) -> bool:
        """Add a tag to a document."""
        return knowledge_db.add_document_tag(self, document_id, tag)

    def remove_document_tag(self, document_id: str, tag: str) -> bool:
        """Remove a tag from a document."""
        return knowledge_db.remove_document_tag(self, document_id, tag)

    def _get_allowed_document_ids(
        self,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[int] = None,
    ) -> Optional[List[str]]:
        """Resolve document IDs readable given roles and user_id."""
        return knowledge_db.get_allowed_document_ids(self, user_roles=user_roles, user_id=user_id)

    def _retrievable_document_ids(
        self,
        user_id: int,
        user_roles: Optional[List[str]] = None,
    ) -> Optional[List[str]]:
        """Resolve document IDs retrievable in QA."""
        return knowledge_db.retrievable_document_ids(self, user_id=user_id, user_roles=user_roles)

    def _search_across_collections(
        self,
        query_embedding,
        target_doc_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Vector-search every collection that holds a target document."""
        return knowledge_qa.search_across_collections(self, query_embedding, target_doc_ids=target_doc_ids)

    def get_answer(
        self,
        query: str,
        user_id: int,
        stream: bool = False,
        tags: Optional[List[str]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        user_roles: Optional[List[str]] = None,
    ):
        """Get an answer to a query using the knowledge base."""
        return knowledge_qa.get_answer(
            self,
            query=query,
            user_id=user_id,
            stream=stream,
            tags=tags,
            conversation_history=conversation_history,
            user_roles=user_roles,
        )

    def _get_reranking_model(self):
        """Get the reranking model from the centralized LLM engine."""
        return knowledge_qa.get_reranking_model(self)

    def _rerank_chunks(self, query: str, chunk_ids: List[str]) -> List[str]:
        """Rerank chunks using LLM for better relevance."""
        return knowledge_qa.rerank_chunks(self, query, chunk_ids)

    def _get_context_chunks(self, chunk_ids: List[str]) -> List[Dict[str, Any]]:
        """Get chunks with context (predecessor and successor chunks)."""
        return knowledge_qa.get_context_chunks(self, chunk_ids)

    def _generate_answer(
        self,
        query: str,
        context_chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]] = None,
        stream: bool = False,
    ):
        """Generate an answer to the query using LLM and context chunks."""
        return knowledge_qa.generate_answer(
            self,
            query=query,
            context_chunks=context_chunks,
            conversation_history=conversation_history,
            stream=stream,
        )

    def _save_query(self, query: str, answer: str, user_id: int):
        """Save the query and answer to database."""
        return knowledge_db.save_query(self, query, answer, user_id)

    def _get_sources(self, context_chunks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Get source information for citation with numbered references."""
        return knowledge_qa.get_sources(self, context_chunks)

    def delete_document(self, document_id: str) -> bool:
        """Delete a document and its chunks from the system."""
        return knowledge_db.delete_document(self, document_id)

    def get_document_info(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed metadata for a document."""
        return knowledge_db.get_document_info(self, document_id)

    def get_document_markdown(self, document_id: str) -> Optional[str]:
        """Get the markdown content of a processed document."""
        return knowledge_qa.get_document_markdown(self, document_id)