"""Document processing, chunking, and embedding creation for knowledge subsystem."""

from datetime import datetime
import logging
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional
import uuid

import numpy as np

from src.utils import chunking, collection_access, knowledge_access, knowledge_ingest, tabular_ingest

logger = logging.getLogger("text2sql.knowledge_processor")


def process_document(
    manager,
    file_path: str,
    original_filename: str,
    tags: Optional[List[str]] = None,
    allowed_roles: Optional[List[str]] = None,
    owner_id: Optional[int] = None,
    options: Optional[knowledge_ingest.IngestOptions] = None,
) -> str:
    """Process a document, convert to markdown, chunk it and store in vector database."""
    document_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    options = options or knowledge_ingest.normalize_options({})

    _, ext = os.path.splitext(original_filename)
    content_type = ext.lower().strip('.')

    cursor = manager.conn.cursor()
    ingest_columns = options.to_columns()
    cursor.execute(
        'INSERT INTO knowledge_documents '
        '(id, original_filename, file_path, content_type, status, created_at, updated_at, owner_id, '
        'chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (document_id, original_filename, file_path, content_type, 'processing', now, now, owner_id,
         ingest_columns['chunking_method'], ingest_columns['chunk_size'], ingest_columns['chunk_overlap'],
         ingest_columns['metadata_columns'], ingest_columns['data_columns'], ingest_columns['collection_name'])
    )

    if tags and isinstance(tags, list) and len(tags) > 0:
        for tag in tags:
            tag = tag.strip().lower()
            if tag:
                tag_id = str(uuid.uuid4())
                cursor.execute(
                    'INSERT INTO knowledge_document_tags (id, document_id, tag, created_at) VALUES (?, ?, ?, ?)',
                    (tag_id, document_id, tag, now)
                )

    manager.conn.commit()
    knowledge_access.apply_role_names(document_id, allowed_roles, granted_by=owner_id)

    manager.processing_status[document_id] = {
        'status': 'processing',
        'message': 'Document upload complete, starting conversion'
    }

    threading.Thread(
        target=manager._process_document_async,
        args=(document_id, file_path, content_type, options)
    ).start()

    return document_id


def process_document_async(
    manager,
    document_id: str,
    file_path: str,
    content_type: str,
    options: Optional[knowledge_ingest.IngestOptions] = None,
):
    """Process document asynchronously."""
    options = options or knowledge_ingest.normalize_options({})
    try:
        manager.logger.info("Starting document processing for %s", document_id)

        manager.processing_status[document_id] = {
            'status': 'processing',
            'message': 'Preparing document for indexing'
        }

        chunks = manager._prepare_chunks(file_path, content_type, options)
        manager.logger.info("Created %d chunks for document %s", len(chunks), document_id)

        manager.logger.info("Saving chunks and creating embeddings for document %s", document_id)
        manager._save_chunks(document_id, chunks, options.collection_name)

        now = datetime.now().isoformat()
        cursor = manager.conn.cursor()
        cursor.execute(
            'UPDATE knowledge_documents SET status = ?, updated_at = ?, processed_at = ? WHERE id = ?',
            ('completed', now, now, document_id)
        )
        manager.conn.commit()

        manager.processing_status[document_id] = {
            'status': 'completed',
            'message': 'Document processing completed successfully'
        }
        manager.logger.info("Document %s processing completed successfully", document_id)

    except Exception as e:
        manager.logger.info("Error processing document %s: %s", document_id, str(e), exc_info=True)
        now = datetime.now().isoformat()
        cursor = manager.conn.cursor()
        cursor.execute(
            'UPDATE knowledge_documents SET status = ?, updated_at = ?, error = ? WHERE id = ?',
            ('error', now, str(e), document_id)
        )
        manager.conn.commit()
        manager.processing_status[document_id] = {
            'status': 'error',
            'message': f'Error processing document: {str(e)}'
        }


def prepare_chunks(
    manager,
    file_path: str,
    content_type: str,
    options: knowledge_ingest.IngestOptions,
    text: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build the list of {"content", "metadata"} chunks for a document."""
    if options.data_columns and tabular_ingest.is_tabular(content_type):
        return tabular_ingest.build_row_chunks(
            file_path, content_type, options.data_columns, options.metadata_columns
        )

    if text is None:
        manager.logger.info("Converting %s to markdown", file_path)
        text = manager.md_converter.convert(file_path).text_content

    texts = chunking.chunk_text(
        text, options.chunk_size, options.chunk_overlap, options.chunking_method
    )
    return [{'content': chunk, 'metadata': {}} for chunk in texts]


def chunk_text(manager, text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """Split text into overlapping chunks with default strategy."""
    return chunking.chunk_text(text, chunk_size, chunk_overlap, chunking.DEFAULT_METHOD)


def process_text_content(
    manager,
    content_name: str,
    content_type: str,
    content: str,
    tags: Optional[List[str]] = None,
    allowed_roles: Optional[List[str]] = None,
    owner_id: Optional[int] = None,
    options: Optional[knowledge_ingest.IngestOptions] = None,
) -> str:
    """Process text content, chunk it and store in vector database."""
    document_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    options = options or knowledge_ingest.normalize_options({})

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as temp_file:
        temp_file.write(content)
        temp_file_path = temp_file.name

    cursor = manager.conn.cursor()
    ingest_columns = options.to_columns()
    cursor.execute(
        'INSERT INTO knowledge_documents '
        '(id, original_filename, file_path, content_type, status, created_at, updated_at, owner_id, '
        'chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (document_id, content_name, temp_file_path, content_type, 'processing', now, now, owner_id,
         ingest_columns['chunking_method'], ingest_columns['chunk_size'], ingest_columns['chunk_overlap'],
         ingest_columns['metadata_columns'], ingest_columns['data_columns'], ingest_columns['collection_name'])
    )

    if tags and isinstance(tags, list) and len(tags) > 0:
        for tag in tags:
            tag = tag.strip().lower()
            if tag:
                tag_id = str(uuid.uuid4())
                cursor.execute(
                    'INSERT INTO knowledge_document_tags (id, document_id, tag, created_at) VALUES (?, ?, ?, ?)',
                    (tag_id, document_id, tag, now)
                )

    manager.conn.commit()
    knowledge_access.apply_role_names(document_id, allowed_roles, granted_by=owner_id)

    manager.processing_status[document_id] = {
        'status': 'processing',
        'message': 'Text content received, starting processing'
    }

    threading.Thread(
        target=manager._process_text_content_async,
        args=(document_id, content, temp_file_path, options)
    ).start()

    return document_id


def process_text_content_async(
    manager,
    document_id: str,
    content: str,
    temp_file_path: str,
    options: Optional[knowledge_ingest.IngestOptions] = None,
):
    """Process text content asynchronously."""
    options = options or knowledge_ingest.normalize_options({})
    try:
        manager.logger.info("Starting text content processing for %s", document_id)

        manager.processing_status[document_id] = {
            'status': 'processing',
            'message': 'Chunking text content'
        }

        texts = chunking.chunk_text(
            content, options.chunk_size, options.chunk_overlap, options.chunking_method
        )
        chunks = [{'content': chunk, 'metadata': {}} for chunk in texts]
        manager.logger.info("Created %d chunks for document %s", len(chunks), document_id)

        manager.logger.info("Saving chunks and creating embeddings for document %s", document_id)
        manager._save_chunks(document_id, chunks, options.collection_name)

        now = datetime.now().isoformat()
        cursor = manager.conn.cursor()
        cursor.execute(
            'UPDATE knowledge_documents SET status = ?, updated_at = ?, processed_at = ? WHERE id = ?',
            ('completed', now, now, document_id)
        )
        manager.conn.commit()

        manager.processing_status[document_id] = {
            'status': 'completed',
            'message': 'Text content processing completed successfully'
        }

        try:
            os.unlink(temp_file_path)
            manager.logger.info("Deleted temporary file %s", temp_file_path)
        except Exception as e:
            manager.logger.warning("Failed to delete temporary file %s: %s", temp_file_path, str(e))

        manager.logger.info("Document %s processing completed successfully", document_id)

    except Exception as e:
        manager.logger.info("Error processing text content %s: %s", document_id, str(e), exc_info=True)
        now = datetime.now().isoformat()
        cursor = manager.conn.cursor()
        cursor.execute(
            'UPDATE knowledge_documents SET status = ?, updated_at = ?, error = ? WHERE id = ?',
            ('error', now, str(e), document_id)
        )
        manager.conn.commit()

        manager.processing_status[document_id] = {
            'status': 'error',
            'message': f'Error processing text content: {str(e)}'
        }
        try:
            os.unlink(temp_file_path)
        except Exception:
            pass


def save_chunks(manager, document_id: str, chunks: List[Any], collection_name: Optional[str] = None):
    """Save chunks to database and create embeddings."""
    collection = collection_name or collection_access.DEFAULT_KNOWLEDGE_COLLECTION
    cursor = manager.conn.cursor()

    for i, chunk in enumerate(chunks):
        if isinstance(chunk, dict):
            content = chunk.get('content') or ''
            metadata = chunk.get('metadata') or {}
        else:
            content, metadata = chunk, {}
        if not str(content).strip():
            continue

        chunk_id = str(uuid.uuid4())

        try:
            manager.processing_status[document_id] = {
                'status': 'processing',
                'message': f'Creating embedding for chunk {i+1} of {len(chunks)}'
            }

            embedding = manager._get_embedding(content)

            vector_metadata = {'document_id': document_id, 'chunk_id': chunk_id}
            for key, value in metadata.items():
                vector_metadata[f'meta_{key}'] = value
            manager.vector_store.insert_embedding(
                collection,
                chunk_id,
                embedding,
                content,
                vector_metadata
            )

            now = datetime.now().isoformat()
            cursor.execute(
                'INSERT INTO knowledge_chunks (id, document_id, chunk_index, content, embedding_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (chunk_id, document_id, i, content, chunk_id, now)
            )
            knowledge_ingest.insert_chunk_metadata(cursor, document_id, chunk_id, metadata)

        except Exception as e:
            manager.logger.info("Error creating embedding for chunk %d of document %s: %s", i, document_id, str(e), exc_info=True)

    manager.conn.commit()


def get_embedding(manager, text: str) -> List[float]:
    """Get embedding for text using the centralized LLM engine."""
    embedding = manager.llm_engine.generate_embedding(text)
    if isinstance(embedding, np.ndarray):
        return embedding.tolist()
    return embedding
