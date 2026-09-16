"""
Knowledge Manager for Text2SQL project.
Handles document processing, chunking, embedding, and retrieval.
"""

import os
import logging
import uuid
import json
import tempfile
import shutil
import time
from typing import List, Dict, Any, Tuple, Optional
import threading
from datetime import datetime
import numpy as np
import sqlite3
from markitdown import MarkItDown
from src.utils.database import get_db_session, get_db_connection
from src.utils import chunking, collection_access, knowledge_access, knowledge_ingest, tabular_ingest
from src.auth import resource_access
from src.utils.vector_store import VectorStore
from src.utils.browser_llm_proxy import is_browser_llm_latest_message_only
from src.utils.llm_engine import LLMEngine
from config.config import UPLOADS_DIR

# Create logger
logger = logging.getLogger('text2sql.knowledge')

class KnowledgeManager:
    """Manages knowledge base documents, chunking, embedding, and retrieval"""
    
    def __init__(self):
        """Initialize KnowledgeManager with vector store and database connection"""
        self.logger = logging.getLogger('text2sql.knowledge')
        self.logger.info("Initializing Knowledge Manager")
        
        # Ensure uploads directory exists
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        
        # Initialize markitdown converter
        self.md_converter = MarkItDown()
        
        # Initialize vector store
        self.vector_store = VectorStore()
        self.vector_store.connect()
        self.vector_store.init_collection('knowledge_chunks')
        
        # Initialize LLM engine
        self.llm_engine = LLMEngine()
        
        # Dictionary to track processing status of documents
        self.processing_status = {}
        
        # One SQLite connection per thread. KnowledgeManager is a process-wide
        # singleton used by Flask request threads and its own background
        # workers; sharing a single connection across threads would interleave
        # transactions and corrupt the database.
        self._local = threading.local()
        
        # Create necessary tables if they don't exist
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
        """Create necessary database tables if they don't exist"""
        cursor = self.conn.cursor()
        
        # Create documents table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content_type TEXT,
                status TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                processed_at TIMESTAMP,
                error TEXT
            )
        ''')
        
        # Create document tags table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_document_tags (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
            )
        ''')
        
        # Create document roles table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_document_roles (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
            )
        ''')
        
        # Create chunks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding_id TEXT,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
            )
        ''')
        
        # Create queries table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_queries (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        self.conn.commit()

        # Ingest configuration (chunking method/size + column roles) and the
        # per-chunk structured metadata table.  Idempotent ALTERs so existing
        # deployments gain the columns in place.
        knowledge_ingest.ensure_ingest_schema(self.conn)

        # Name -> integer identity registry for vector collections, plus the
        # generic access-store registration that makes grants manageable.
        collection_access.ensure_schema()

        # Add/backfill the tenancy columns (owner_id, stable access_id) and
        # migrate any legacy role-NAME grants into the generic store.  Runs on
        # its own connection, so the base tables must be committed first.
        knowledge_access.ensure_schema()
    
    def process_document(self, file_path: str, original_filename: str, tags: List[str] = None, allowed_roles: List[str] = None, owner_id: int = None, options: knowledge_ingest.IngestOptions = None) -> str:
        """Process a document, convert to markdown, chunk it and store in vector database
        
        Args:
            file_path: Path to the uploaded file
            original_filename: Original filename
            tags: List of tags to associate with the document
            allowed_roles: List of role names allowed to access the document
            owner_id: Authenticated uploader's user id (stamped on the row)
            options: Validated ingestion properties (chunking method/size and,
                     for CSV/Excel, the data/metadata column roles)
            
        Returns:
            str: Document ID
        """
        document_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        options = options or knowledge_ingest.normalize_options({})
        
        # Get file extension
        _, ext = os.path.splitext(original_filename)
        content_type = ext.lower().strip('.')
        
        # Save document info to database
        cursor = self.conn.cursor()
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
        
        # Save tags if provided
        if tags and isinstance(tags, list) and len(tags) > 0:
            for tag in tags:
                tag = tag.strip().lower()  # Normalize tags
                if tag:
                    tag_id = str(uuid.uuid4())
                    cursor.execute(
                        'INSERT INTO knowledge_document_tags (id, document_id, tag, created_at) VALUES (?, ?, ?, ?)',
                        (tag_id, document_id, tag, now)
                    )
        
        self.conn.commit()

        # Role-NAME grants are written to the generic store (access_id keyed);
        # the legacy knowledge_document_roles table is left readable only.
        knowledge_access.apply_role_names(document_id, allowed_roles, granted_by=owner_id)
        
        # Update processing status
        self.processing_status[document_id] = {
            'status': 'processing',
            'message': 'Document upload complete, starting conversion'
        }
        
        # Start processing in a separate thread
        threading.Thread(
            target=self._process_document_async,
            args=(document_id, file_path, content_type, options)
        ).start()
        
        return document_id
        
    def _process_document_async(self, document_id: str, file_path: str, content_type: str, options: knowledge_ingest.IngestOptions = None):
        """Process document asynchronously
        
        Args:
            document_id: Document ID
            file_path: Path to the document file
            content_type: File type/extension
            options: Ingestion properties captured at upload time
        """
        options = options or knowledge_ingest.normalize_options({})
        try:
            self.logger.info(f"Starting document processing for {document_id}")
            
            self.processing_status[document_id] = {
                'status': 'processing',
                'message': 'Preparing document for indexing'
            }
            
            # Tabular files with mapped data columns become one chunk per row;
            # everything else is converted to text and chunked.
            chunks = self._prepare_chunks(file_path, content_type, options)
            self.logger.info(f"Created {len(chunks)} chunks for document {document_id}")
            
            # Save chunks to database and create embeddings
            self.logger.info(f"Saving chunks and creating embeddings for document {document_id}")
            self._save_chunks(document_id, chunks, options.collection_name)
            
            # Update document status to completed
            now = datetime.now().isoformat()
            cursor = self.conn.cursor()
            cursor.execute(
                'UPDATE knowledge_documents SET status = ?, updated_at = ?, processed_at = ? WHERE id = ?',
                ('completed', now, now, document_id)
            )
            self.conn.commit()
            
            # Update processing status
            self.processing_status[document_id] = {
                'status': 'completed',
                'message': 'Document processing completed successfully'
            }
            
            self.logger.info(f"Document {document_id} processing completed successfully")
            
        except Exception as e:
            self.logger.info(f"Error processing document {document_id}: {str(e)}", exc_info=True)
            
            # Update document status to error
            now = datetime.now().isoformat()
            cursor = self.conn.cursor()
            cursor.execute(
                'UPDATE knowledge_documents SET status = ?, updated_at = ?, error = ? WHERE id = ?',
                ('error', now, str(e), document_id)
            )
            self.conn.commit()
            
            # Update processing status
            self.processing_status[document_id] = {
                'status': 'error',
                'message': f'Error processing document: {str(e)}'
            }
    
    def _prepare_chunks(self, file_path: str, content_type: str, options: knowledge_ingest.IngestOptions, text: str = None) -> List[Dict[str, Any]]:
        """Build the list of ``{"content", "metadata"}`` chunks for a document.

        A CSV/Excel upload with mapped data columns yields one chunk per record;
        every other document is converted to text (when ``text`` is not already
        supplied) and split with the chosen chunking method.
        """
        if options.data_columns and tabular_ingest.is_tabular(content_type):
            return tabular_ingest.build_row_chunks(
                file_path, content_type, options.data_columns, options.metadata_columns
            )

        if text is None:
            self.logger.info(f"Converting {file_path} to markdown")
            text = self.md_converter.convert(file_path).text_content

        texts = chunking.chunk_text(
            text, options.chunk_size, options.chunk_overlap, options.chunking_method
        )
        return [{'content': chunk, 'metadata': {}} for chunk in texts]

    def _chunk_text(self, text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
        """Split text into overlapping chunks with the default strategy.

        Kept as a thin, stable wrapper; callers that need a specific strategy
        use :func:`src.utils.chunking.chunk_text` directly.
        """
        return chunking.chunk_text(text, chunk_size, chunk_overlap, chunking.DEFAULT_METHOD)
    
    def process_text_content(self, content_name: str, content_type: str, content: str, tags: List[str] = None, allowed_roles: List[str] = None, owner_id: int = None, options: knowledge_ingest.IngestOptions = None) -> str:
        """Process text content, chunk it and store in vector database
        
        Args:
            content_name: Name of the content
            content_type: Type of content
            content: The actual text content
            tags: List of tags to associate with the document
            allowed_roles: List of role names allowed to access the document
            owner_id: Authenticated uploader's user id (stamped on the row)
            options: Validated ingestion properties (chunking method/size)
            
        Returns:
            str: Document ID
        """
        document_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        options = options or knowledge_ingest.normalize_options({})
        
        # Temporary file for consistency with file-based implementation
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # Save document info to database
        cursor = self.conn.cursor()
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
        
        # Save tags if provided
        if tags and isinstance(tags, list) and len(tags) > 0:
            for tag in tags:
                tag = tag.strip().lower()  # Normalize tags
                if tag:
                    tag_id = str(uuid.uuid4())
                    cursor.execute(
                        'INSERT INTO knowledge_document_tags (id, document_id, tag, created_at) VALUES (?, ?, ?, ?)',
                        (tag_id, document_id, tag, now)
                    )
        
        self.conn.commit()

        # Role-NAME grants go to the generic store (access_id keyed).
        knowledge_access.apply_role_names(document_id, allowed_roles, granted_by=owner_id)
        
        # Update processing status
        self.processing_status[document_id] = {
            'status': 'processing',
            'message': 'Text content received, starting processing'
        }
        
        # Start processing in a separate thread
        threading.Thread(
            target=self._process_text_content_async,
            args=(document_id, content, temp_file_path, options)
        ).start()
        
        return document_id
        
    def _process_text_content_async(self, document_id: str, content: str, temp_file_path: str, options: knowledge_ingest.IngestOptions = None):
        """Process text content asynchronously
        
        Args:
            document_id: Document ID
            content: Text content
            temp_file_path: Path to temporary file
            options: Ingestion properties captured at submit time
        """
        options = options or knowledge_ingest.normalize_options({})
        try:
            self.logger.info(f"Starting text content processing for {document_id}")
            
            # Update status
            self.processing_status[document_id] = {
                'status': 'processing',
                'message': 'Chunking text content'
            }
            
            # Chunk the content with the chosen strategy
            texts = chunking.chunk_text(
                content, options.chunk_size, options.chunk_overlap, options.chunking_method
            )
            chunks = [{'content': chunk, 'metadata': {}} for chunk in texts]
            self.logger.info(f"Created {len(chunks)} chunks for document {document_id}")
            
            # Save chunks to database and create embeddings
            self.logger.info(f"Saving chunks and creating embeddings for document {document_id}")
            self._save_chunks(document_id, chunks, options.collection_name)
            
            # Update document status to completed
            now = datetime.now().isoformat()
            cursor = self.conn.cursor()
            cursor.execute(
                'UPDATE knowledge_documents SET status = ?, updated_at = ?, processed_at = ? WHERE id = ?',
                ('completed', now, now, document_id)
            )
            self.conn.commit()
            
            # Update processing status
            self.processing_status[document_id] = {
                'status': 'completed',
                'message': 'Text content processing completed successfully'
            }
            
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
                self.logger.info(f"Deleted temporary file {temp_file_path}")
            except Exception as e:
                self.logger.warning(f"Failed to delete temporary file {temp_file_path}: {str(e)}")
            
            self.logger.info(f"Document {document_id} processing completed successfully")
            
        except Exception as e:
            self.logger.info(f"Error processing text content {document_id}: {str(e)}", exc_info=True)
            
            # Update document status to error
            now = datetime.now().isoformat()
            cursor = self.conn.cursor()
            cursor.execute(
                'UPDATE knowledge_documents SET status = ?, updated_at = ?, error = ? WHERE id = ?',
                ('error', now, str(e), document_id)
            )
            self.conn.commit()
            
            # Update processing status
            self.processing_status[document_id] = {
                'status': 'error',
                'message': f'Error processing text content: {str(e)}'
            }
            
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except:
                pass
    
    def _save_chunks(self, document_id: str, chunks: List[Any], collection_name: str = None):
        """Save chunks to database and create embeddings.

        ``chunks`` is a list of ``{"content", "metadata"}`` records (a bare
        string is accepted for backward compatibility).  Metadata values are
        stored both in the vector store (flattened, ``meta_``-prefixed, so a
        user column can never collide with ``document_id``) and in the
        ``knowledge_chunk_metadata`` table for exact round-tripping.  Vectors
        are written to ``collection_name`` (defaulting to the shared knowledge
        collection).
        """
        collection = collection_name or collection_access.DEFAULT_KNOWLEDGE_COLLECTION
        cursor = self.conn.cursor()
        
        for i, chunk in enumerate(chunks):
            if isinstance(chunk, dict):
                content = chunk.get('content') or ''
                metadata = chunk.get('metadata') or {}
            else:
                content, metadata = chunk, {}
            if not str(content).strip():
                continue

            # Generate unique chunk ID
            chunk_id = str(uuid.uuid4())
            
            # Create embedding for the chunk
            try:
                # Update status
                self.processing_status[document_id] = {
                    'status': 'processing',
                    'message': f'Creating embedding for chunk {i+1} of {len(chunks)}'
                }
                
                # Create embedding using LLM engine
                embedding = self._get_embedding(content)
                
                # Save embedding to vector store using chunk_id as unique ChromaDB ID
                # (Using chunk_id instead of chunk index to avoid ID collisions across documents)
                vector_metadata = {'document_id': document_id, 'chunk_id': chunk_id}
                for key, value in metadata.items():
                    vector_metadata[f'meta_{key}'] = value
                self.vector_store.insert_embedding(
                    collection, 
                    chunk_id,  # Use unique chunk UUID as the ID to prevent cross-document collisions
                    embedding,
                    content,
                    vector_metadata
                )
                
                # Save chunk to database
                now = datetime.now().isoformat()
                cursor.execute(
                    'INSERT INTO knowledge_chunks (id, document_id, chunk_index, content, embedding_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                    (chunk_id, document_id, i, content, chunk_id, now)
                )
                knowledge_ingest.insert_chunk_metadata(cursor, document_id, chunk_id, metadata)
                
            except Exception as e:
                self.logger.info(f"Error creating embedding for chunk {i} of document {document_id}: {str(e)}", exc_info=True)
        
        self.conn.commit()
    
    # _get_embedding_model method has been moved to LLMEngine class
        
    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for a text using the centralized LLM engine
        
        Args:
            text: Text to embed
            
        Returns:
            Vector embedding
        """
        # Use the centralized LLM engine to generate embeddings
        embedding = self.llm_engine.generate_embedding(text)
        
        # Convert to list if it's a numpy array
        if isinstance(embedding, np.ndarray):
            return embedding.tolist()
        return embedding
    
    def get_document_status(self, document_id: str) -> Dict[str, Any]:
        """Get the processing status of a document
        
        Args:
            document_id: Document ID
            
        Returns:
            Status information
        """
        # Check in-memory status first
        if document_id in self.processing_status:
            return self.processing_status[document_id]
        
        # If not in memory, check database
        cursor = self.conn.cursor()
        cursor.execute('SELECT status, error, processed_at FROM knowledge_documents WHERE id = ?', (document_id,))
        row = cursor.fetchone()
        
        if not row:
            return {'status': 'not_found', 'message': 'Document not found'}
        
        status, error, processed_at = row
        
        if status == 'completed':
            return {'status': status, 'message': 'Document processing completed successfully', 'processed_at': processed_at}
        elif status == 'error':
            return {'status': status, 'message': f'Error processing document: {error}'}
        else:
            return {'status': status, 'message': 'Document status unknown'}
    
    def list_documents(self, user_id: int = None, user_roles: List[str] = None) -> List[Dict[str, Any]]:
        """List the documents the caller may see.
        
        Args:
            user_id: Requesting user id; drives ownership/role-grant tenancy.
            user_roles: Legacy role names (mapped to ids) for callers with no
                        user id.  With no identity at all only owner-less
                        (public/legacy) documents are returned.
        
        Returns:
            List of document information (``access_id``, ``owner_id`` and
            ``can_manage`` drive the generic API and the admin UI)
        """
        visible = knowledge_access.retrieval_document_ids(user_id, user_roles)
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT id, original_filename, content_type, status, created_at, processed_at, owner_id, '
            'chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name '
            'FROM knowledge_documents ORDER BY created_at DESC'
        )
        
        documents = []
        for row in cursor.fetchall():
            (doc_id, filename, content_type, status, created_at, processed_at, owner_id,
             chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name) = row
            if visible is not None and doc_id not in visible:
                continue
            
            # Get chunk count for this document
            cursor.execute('SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?', (doc_id,))
            chunk_count = cursor.fetchone()[0]
            
            # Get tags for this document
            tags = self.get_document_tags(doc_id)
            
            # Get allowed roles for this document
            allowed_roles = self.get_document_roles(doc_id)
            
            documents.append({
                'id': doc_id,
                'access_id': knowledge_access.access_id_for(doc_id),
                'owner_id': None if owner_id is None else int(owner_id),
                'can_manage': knowledge_access.can_manage_document(doc_id, user_id),
                'filename': filename,
                'content_type': content_type,
                'status': status,
                'created_at': created_at,
                'processed_at': processed_at,
                'chunk_count': chunk_count,
                'tags': tags,
                'allowed_roles': allowed_roles,
                'chunking_method': chunking_method,
                'chunk_size': chunk_size,
                'chunk_overlap': chunk_overlap,
                'metadata_columns': knowledge_ingest.decode_columns(metadata_columns),
                'data_columns': knowledge_ingest.decode_columns(data_columns),
                'collection_name': collection_name or collection_access.DEFAULT_KNOWLEDGE_COLLECTION,
            })
        
        return documents
        
    def get_document_tags(self, document_id: str) -> List[str]:
        """Get tags for a specific document
        
        Args:
            document_id: Document ID
            
        Returns:
            List of tags
        """
        cursor = self.conn.cursor()
        cursor.execute('SELECT tag FROM knowledge_document_tags WHERE document_id = ?', (document_id,))
        return [row[0] for row in cursor.fetchall()]
        
    def get_document_roles(self, document_id: str) -> List[str]:
        """Get the granted role names for a document from the generic store.
        
        Args:
            document_id: Document ID
            
        Returns:
            List of role names currently granted on the document
        """
        access_id = knowledge_access.access_id_for(document_id)
        if access_id is None:
            return []
        return [
            entry['role_name']
            for entry in resource_access.list_access("knowledge_document", access_id)
        ]
        
    def get_all_tags(self, user_id: int = None, user_roles: List[str] = None) -> List[str]:
        """Get unique tags from active (completed) documents the caller can access.
        
        Args:
            user_id: Requesting user id (ownership + role-grant tenancy).
            user_roles: Legacy role names (mapped to ids) for callers with no
                        user id.  With no identity at all only owner-less
                        (public/legacy) documents contribute tags.
        
        Returns:
            List of unique tags from accessible active documents only
        """
        cursor = self.conn.cursor()
        allowed_doc_ids = knowledge_access.retrieval_document_ids(user_id, user_roles)
        
        if allowed_doc_ids is not None:
            if len(allowed_doc_ids) == 0:
                return []  # No accessible documents
            placeholders = ','.join(['?' for _ in allowed_doc_ids])
            cursor.execute(f'''
                SELECT DISTINCT kdt.tag 
                FROM knowledge_document_tags kdt
                INNER JOIN knowledge_documents kd ON kdt.document_id = kd.id
                WHERE kd.status = 'completed' AND kdt.document_id IN ({placeholders})
                ORDER BY kdt.tag
            ''', list(allowed_doc_ids))
        else:
            cursor.execute('''
                SELECT DISTINCT kdt.tag 
                FROM knowledge_document_tags kdt
                INNER JOIN knowledge_documents kd ON kdt.document_id = kd.id
                WHERE kd.status = 'completed'
                ORDER BY kdt.tag
            ''')
        
        return [row[0] for row in cursor.fetchall()]
        
    def _get_document_ids_by_tags(self, tags: List[str]) -> List[str]:
        """Get document IDs that have all the specified tags
        
        Args:
            tags: List of tags to filter by
            
        Returns:
            List of document IDs
        """
        if not tags:
            return None
            
        # Normalize tags
        normalized_tags = [tag.strip().lower() for tag in tags if tag.strip()]
        if not normalized_tags:
            return None
            
        # Construct SQL query to find documents that have ALL the specified tags
        # This uses a GROUP BY and HAVING COUNT to ensure documents have all tags
        cursor = self.conn.cursor()
        placeholders = ','.join(['?' for _ in normalized_tags])
        
        query = f"""
            SELECT document_id FROM knowledge_document_tags
            WHERE tag IN ({placeholders})
            GROUP BY document_id
            HAVING COUNT(DISTINCT tag) = ?
        """
        
        # The params include all tags plus the count of tags
        params = normalized_tags + [len(normalized_tags)]
        
        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall()]
        
    def add_document_tag(self, document_id: str, tag: str) -> bool:
        """Add a tag to a document
        
        Args:
            document_id: Document ID
            tag: Tag to add
            
        Returns:
            True if successful, False otherwise
        """
        try:
            tag = tag.strip().lower()  # Normalize tag
            if not tag:
                return False
                
            now = datetime.now().isoformat()
            tag_id = str(uuid.uuid4())
            
            # Check if this tag already exists for this document
            cursor = self.conn.cursor()
            cursor.execute('SELECT id FROM knowledge_document_tags WHERE document_id = ? AND tag = ?', 
                        (document_id, tag))
            if cursor.fetchone():
                return True  # Tag already exists
                
            # Add the new tag
            cursor.execute(
                'INSERT INTO knowledge_document_tags (id, document_id, tag, created_at) VALUES (?, ?, ?, ?)',
                (tag_id, document_id, tag, now)
            )
            self.conn.commit()
            return True
        except Exception as e:
            self.logger.info(f"Error adding tag to document {document_id}: {str(e)}", exc_info=True)
            return False
            
    def remove_document_tag(self, document_id: str, tag: str) -> bool:
        """Remove a tag from a document
        
        Args:
            document_id: Document ID
            tag: Tag to remove
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM knowledge_document_tags WHERE document_id = ? AND tag = ?', 
                        (document_id, tag.strip().lower()))
            self.conn.commit()
            return True
        except Exception as e:
            self.logger.info(f"Error removing tag from document {document_id}: {str(e)}", exc_info=True)
            return False
    
    def _get_allowed_document_ids(self, user_roles: List[str] = None, user_id: int = None) -> List[str]:
        """Document IDs the caller may retrieve (generic-store tenancy).

        Delegates to :mod:`src.utils.knowledge_access`; ``user_roles`` keeps
        legacy callers working by mapping role names to role ids.  Returns
        ``None`` when everything is allowed (admin).
        """
        allowed = knowledge_access.retrieval_document_ids(user_id, user_roles)
        return None if allowed is None else sorted(allowed)

    def _retrievable_document_ids(self, user_id: int, user_roles: List[str] = None) -> List[str]:
        """Visible document ids for retrieval; ``None`` means all (admin)."""
        allowed = knowledge_access.retrieval_document_ids(user_id, user_roles)
        return None if allowed is None else sorted(allowed)

    def _search_across_collections(self, query_embedding, target_doc_ids: List[str] = None) -> List[Dict[str, Any]]:
        """Vector-search every collection that holds a target document.

        Documents may be indexed into different collections, so the search is
        grouped by each document's ``collection_name`` and each collection is
        queried with a filter restricted to that group.  The per-collection hits
        are merged into one similarity-ranked list so reranking is unchanged.

        ``target_doc_ids is None`` means "every document" (admin with no filter).
        """
        cursor = self.conn.cursor()
        cursor.execute('SELECT id, collection_name FROM knowledge_documents')
        collection_by_doc = {
            str(row[0]): (row[1] or collection_access.DEFAULT_KNOWLEDGE_COLLECTION)
            for row in cursor.fetchall()
        }
        if target_doc_ids is None:
            targets = list(collection_by_doc)
        else:
            targets = [str(doc_id) for doc_id in target_doc_ids if str(doc_id) in collection_by_doc]

        grouped: Dict[str, List[str]] = {}
        for doc_id in targets:
            grouped.setdefault(collection_by_doc[doc_id], []).append(doc_id)

        hits: List[Dict[str, Any]] = []
        # Take a bounded number from *each* collection before merging, so a
        # large collection cannot crowd a small one out of the shared candidate
        # pool that reranking sees.
        per_collection = 20
        for collection, doc_ids in grouped.items():
            filter_expr = (
                {"document_id": doc_ids[0]} if len(doc_ids) == 1
                else {"document_id": {"$in": doc_ids}}
            )
            try:
                hits.extend(self.vector_store.search_similar(
                    collection,
                    query_embedding,
                    limit=per_collection,
                    output_fields=['document_id', 'chunk_id', 'query_text'],
                    filter_expr=filter_expr
                ))
            except Exception as e:
                self.logger.info(f"Search failed in collection {collection}: {str(e)}", exc_info=True)

        hits.sort(key=lambda hit: hit.get('similarity') or 0, reverse=True)
        return hits[:60]

    def get_answer(self, query: str, user_id: int, stream: bool = False, tags: List[str] = None, conversation_history: List[Dict[str, str]] = None, user_roles: List[str] = None):
        """Get an answer to a query using the knowledge base
        
        Args:
            query: The user's question
            user_id: User ID
            stream: Whether to stream the response
            tags: Optional list of tags to filter documents by
            conversation_history: Previous conversation messages for context
            user_roles: List of roles the user has (for RBAC)
            
        Returns:
            If stream=False: Dictionary with answer and supporting information
            If stream=True: A generator yielding text chunks
        """
        try:
            # Create embedding for the query
            query_embedding = self._get_embedding(query)
            
            # Tenancy scope: ownership + generic role grants.  With no user id
            # (background/MCP) only owner-less public documents are retrievable.
            allowed_doc_ids = self._retrievable_document_ids(user_id, user_roles)
            self.logger.info(f"Allowed document IDs for retrieval: {len(allowed_doc_ids) if allowed_doc_ids is not None else 'ALL'}")
            
            # If tags are provided, get the document IDs that have these tags
            tag_filtered_doc_ids = None
            if tags and isinstance(tags, list) and len(tags) > 0:
                self.logger.info(f"Filtering documents by tags: {tags}")
                tag_filtered_doc_ids = self._get_document_ids_by_tags(tags)
                self.logger.info(f"Filtered document IDs by tags: {tag_filtered_doc_ids}")
                
                if not tag_filtered_doc_ids:
                    self.logger.info(f"No documents found with tags: {tags}")
                    if stream:
                        # For streaming requests, we need to return a tuple
                        def empty_generator():
                            yield "No documents found with the selected tags."
                        return empty_generator(), []
                    else:
                        return {
                            'success': False,
                            'answer': 'No documents found with the selected tags.',
                            'sources': []
                        }
            
            # Combine filters
            final_filtered_ids = None
            
            # Case 1: Both filters active
            if allowed_doc_ids is not None and tag_filtered_doc_ids is not None:
                # Intersect the lists
                final_filtered_ids = list(set(allowed_doc_ids) & set(tag_filtered_doc_ids))
                if not final_filtered_ids:
                    msg = "No documents found matching both tags and your access permissions."
                    if stream:
                        def empty_generator(): yield msg
                        return empty_generator(), []
                    else:
                        return {'success': False, 'answer': msg, 'sources': []}
                        
            # Case 2: Only role filter active
            elif allowed_doc_ids is not None:
                final_filtered_ids = allowed_doc_ids
                if not final_filtered_ids:
                    msg = "You don't have access to any documents in the knowledge base."
                    if stream:
                        def empty_generator(): yield msg
                        return empty_generator(), []
                    else:
                        return {'success': False, 'answer': msg, 'sources': []}
                        
            # Case 3: Only tag filter active
            elif tag_filtered_doc_ids is not None:
                final_filtered_ids = tag_filtered_doc_ids
                
            # Case 4: No filters (Admin with no tags) -> final_filtered_ids is None
            
            # Search for similar chunks across the collections the documents
            # actually live in, restricted to the tenancy-visible document ids.
            top_chunks = self._search_across_collections(query_embedding, final_filtered_ids)


            if not top_chunks:
                msg = 'No relevant information found in the knowledge base.'
                if stream:
                    def empty_generator():
                        yield msg
                    return empty_generator(), []
                return {
                    'success': False,
                    'answer': msg,
                    'sources': []
                }
            
            # Get chunk content from database
            chunk_ids = [chunk.get('chunk_id') for chunk in top_chunks]
            
            # Rerank chunks using LLM
            reranked_chunks = self._rerank_chunks(query, chunk_ids)
            
            # Get top 3 chunks after reranking
            top_3_chunk_ids = reranked_chunks[:3]
            
            self.logger.info(f"Top 3 chunks for query '{query}': {top_3_chunk_ids}")
            # Get context chunks (predecessor and successor for each top chunk)
            context_chunks = self._get_context_chunks(top_3_chunk_ids)
            
            # Get source information for citation
            sources = self._get_sources(context_chunks)
            self.logger.info(f"Sources for query '{query}': {sources}")
            # Handle streaming case
            if stream:
                # Generate streaming answer
                stream_generator = self._generate_answer(query, context_chunks, conversation_history, stream=True)
                
                # Create a new generator that saves the complete answer when done
                def save_and_stream():
                    collected_answer = []
                    try:
                        for chunk in stream_generator:
                            collected_answer.append(chunk)
                            yield chunk
                        
                        # After streaming is complete, save the full answer
                        full_answer = "".join(collected_answer)
                        self._save_query(query, full_answer, user_id)
                    except Exception as e:
                        self.logger.info(f"Error in streaming answer: {str(e)}", exc_info=True)
                        yield "\nError occurred during streaming."
                
                # Return exactly two values as a tuple
                return save_and_stream(), sources
            
            # Handle non-streaming case (original behavior)
            else:
                # Generate answer using LLM
                answer = self._generate_answer(query, context_chunks, conversation_history)
                
                # Save the query and answer to database
                self._save_query(query, answer, user_id)
                
                return {
                    'success': True,
                    'answer': answer,
                    'sources': sources
                }
            
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            self.logger.info(f"Error answering query '{query}': {str(e)}")
            self.logger.info(f"Full traceback:\n{error_traceback}")
            print(f"KNOWLEDGE MANAGER ERROR: {str(e)}")
            print(f"TRACEBACK:\n{error_traceback}")
            error_msg = 'Sorry, an error occurred while processing your question.'
            if stream:
                def error_generator():
                    yield error_msg
                return error_generator(), []
            return {
                'success': False,
                'error': str(e),
                'answer': error_msg
            }
    
    # _get_reranking_model method has been moved to LLMEngine class
    # _get_reranking_model method has been moved to LLMEngine class
    def _get_reranking_model(self):
        """Get the reranking model from the centralized LLM engine
        
        Returns:
            CrossEncoder: The initialized reranking model (cross-encoder)
        """
        try:
            from src.utils.llm_engine import LLMEngine
            llm_engine = LLMEngine()
            return llm_engine.get_reranking_model()
        except Exception as e:
            self.logger.info(f"Failed to get reranking model from LLMEngine: {str(e)}", exc_info=True)
            return None
            
    def _rerank_chunks(self, query: str, chunk_ids: List[str]) -> List[str]:
        """Rerank chunks using LLM for better relevance
        
        Args:
            query: User query
            chunk_ids: List of chunk IDs to rerank
            
        Returns:
            Reranked list of chunk IDs (most relevant first)
        """
        # If no chunks to rerank, return empty list
        if not chunk_ids:
            return []
        
        try:
            # Get the content for each chunk from the database
            cursor = self.conn.cursor()
            chunk_contents = {}
            for chunk_id in chunk_ids:
                cursor.execute('SELECT content FROM knowledge_chunks WHERE id = ?', (chunk_id,))
                row = cursor.fetchone()
                if row:
                    chunk_contents[chunk_id] = row[0]
            
            # If we couldn't retrieve any chunk contents, return original order
            if not chunk_contents:
                self.logger.warning("No chunk contents found for reranking, returning original order")
                return chunk_ids[:3]
            
            # Prepare candidate pairs for reranking
            reranker = self._get_reranking_model()
            if not reranker:
                self.logger.warning("Reranker not available, returning original chunk order")
                return chunk_ids[:3]
            
            # Create pairs of (query, chunk_content) for each chunk
            candidate_pairs = []
            chunk_id_list = []
            for chunk_id, content in chunk_contents.items():
                candidate_pairs.append((query, content))
                chunk_id_list.append(chunk_id)
            
            # Get reranking scores
            self.logger.info(f"Reranking {len(candidate_pairs)} chunks using cross-encoder")
            rerank_scores = reranker.predict(candidate_pairs)
            
            # Create list of (chunk_id, score) tuples
            ranked_chunks = list(zip(chunk_id_list, rerank_scores))
            
            # Sort by score in descending order
            ranked_chunks.sort(key=lambda x: x[1], reverse=True)
            self.logger.info(f"Reranking complete, top score: {ranked_chunks[0][1] if ranked_chunks else 'N/A'}")
            
            # Extract ordered chunk IDs (most relevant first)
            reranked_ids = [chunk_id for chunk_id, _ in ranked_chunks]
            
            return reranked_ids
            
        except Exception as e:
            self.logger.info(f"Error during chunk reranking: {str(e)}", exc_info=True)
            # If reranking fails, return original order (top 3)
            return chunk_ids[:3]
    
    def _get_context_chunks(self, chunk_ids: List[str]) -> List[Dict[str, Any]]:
        """Get chunks with context (predecessor and successor chunks)
        
        Args:
            chunk_ids: List of primary chunk IDs
            
        Returns:
            List of chunks with their content and metadata
        """
        chunks = []
        cursor = self.conn.cursor()
        # Structured (CSV/Excel) column metadata for every requested chunk, so a
        # retrieval hit can show "department = Sales" without embedding it.
        metadata_by_chunk = knowledge_ingest.fetch_chunk_metadata(cursor, chunk_ids)
        
        for chunk_id in chunk_ids:
            # Get the main chunk
            cursor.execute(
                '''
                SELECT c.id, c.document_id, c.chunk_index, c.content, d.original_filename 
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON c.document_id = d.id
                WHERE c.id = ?
                ''',
                (chunk_id,)
            )
            main_chunk = cursor.fetchone()
            
            if not main_chunk:
                continue
                
            chunk_id, doc_id, chunk_index, content, filename = main_chunk
            
            # Get predecessor chunk
            cursor.execute(
                'SELECT id, content FROM knowledge_chunks WHERE document_id = ? AND chunk_index = ?',
                (doc_id, chunk_index - 1)
            )
            predecessor = cursor.fetchone()
            
            # Get successor chunk
            cursor.execute(
                'SELECT id, content FROM knowledge_chunks WHERE document_id = ? AND chunk_index = ?',
                (doc_id, chunk_index + 1)
            )
            successor = cursor.fetchone()
            
            chunk_data = {
                'id': chunk_id,
                'document_id': doc_id,
                'chunk_index': chunk_index,
                'content': content,
                'filename': filename,
                'metadata': metadata_by_chunk.get(str(chunk_id), {})
            }
            
            if predecessor:
                chunk_data['predecessor'] = {
                    'id': predecessor[0],
                    'content': predecessor[1]
                }
                
            if successor:
                chunk_data['successor'] = {
                    'id': successor[0],
                    'content': successor[1]
                }
                
            chunks.append(chunk_data)
        
        return chunks
    
    def _generate_answer(self, query: str, context_chunks: List[Dict[str, Any]], conversation_history: List[Dict[str, str]] = None, stream: bool = False):
        """Generate an answer to the query using LLM and context chunks
        
        Args:
            query: User query
            context_chunks: Context chunks with their content
            conversation_history: Previous conversation messages for context
            stream: Whether to stream the response
            
        Returns:
            If stream=False: Generated answer as a string
            If stream=True: A generator yielding text chunks
        """
        # Create a mapping of unique documents to numbered references
        document_index = {}
        document_counter = 1
        
        # Build the context string from the chunks with numbered references
        context = ""
        
        for i, chunk in enumerate(context_chunks):
            # Get or assign a document number
            doc_filename = chunk['filename']
            if doc_filename not in document_index:
                document_index[doc_filename] = document_counter
                document_counter += 1
            
            doc_num = document_index[doc_filename]
            context += f"\n\nDocument [{doc_num}]:\n"
            
            # Metadata columns were deliberately not embedded; surface them as
            # a labelled line so the LLM can still filter/attribute records.
            metadata = chunk.get('metadata') or {}
            if metadata:
                context += "Metadata: " + ", ".join(f"{k}={v}" for k, v in metadata.items()) + "\n\n"
            
            if 'predecessor' in chunk:
                context += chunk['predecessor']['content'] + "\n\n"
                
            context += chunk['content'] + "\n\n"
            
            if 'successor' in chunk:
                context += chunk['successor']['content']
        
        # Build conversation context if history is provided
        conversation_context = ""
        if conversation_history and not is_browser_llm_latest_message_only():
            conversation_context = "\n\nPrevious conversation context:\n"
            # Include only the last few messages to avoid token limits
            recent_history = conversation_history[-6:]  # Last 6 messages (3 exchanges)
            for msg in recent_history:
                role = msg.get('role', '')
                content = msg.get('content', '')
                if role == 'user':
                    conversation_context += f"User: {content}\n"
                elif role == 'assistant':
                    conversation_context += f"Assistant: {content}\n"
        
        # Create a document reference list for the LLM
        doc_reference_list = ""
        if document_index:
            doc_reference_list = "\n\nDocument References:\n"
            for doc_name, doc_num in sorted(document_index.items(), key=lambda x: x[1]):
                doc_reference_list += f"[{doc_num}] {doc_name}\n"
        
        # Create prompt for LLM with conversation context and numbered citation instructions
        system_content = f"""You are a helpful AI assistant that provides accurate answers based on the given context. 
        If the answer cannot be found in the context, acknowledge that you don't know instead of making up information.
        Provide clear, concise answers and use markdown formatting in your response to improve readability.
        
        IMPORTANT CITATION RULES:
        - When referencing information from the context, use numbered citations like [1], [2], etc.
        - Do NOT repeat the full document names in your response
        - Use citations sparingly - only at the end of sentences or paragraphs where you reference specific information
        - The document references will be provided separately at the end, so you don't need to mention document names{conversation_context}"""
        
        prompt = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Context information:\n{context}{doc_reference_list}\n\nQuestion: {query}\n\nProvide a detailed answer to the question based only on the context provided. Use markdown formatting for better readability and numbered citations [1], [2], etc. when referencing specific information."}
        ]
        
        try:
            # Generate answer using LLM engine
            return self.llm_engine.generate_completion(prompt, log_prefix="Knowledge QA", stream=stream)
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            self.logger.info(f"Error generating answer: {str(e)}")
            self.logger.info(f"Full traceback:\n{error_traceback}")
            print(f"GENERATE ANSWER ERROR: {str(e)}")
            print(f"TRACEBACK:\n{error_traceback}")
            if stream:
                def error_generator():
                    yield "I'm sorry, I couldn't generate an answer based on the available information."
                return error_generator()
            return "I'm sorry, I couldn't generate an answer based on the available information."
    
    def _save_query(self, query: str, answer: str, user_id: int):
        """Save the query and answer to database

        Args:
            query: User query
            answer: Generated answer
            user_id: User ID
        """
        query_id = str(uuid.uuid4())
        now = datetime.now().isoformat()

        cursor = self.conn.cursor()
        # user_id may be None when the query is served through an MCP/agent tool
        # (no session user). Store a NULL user_id instead of violating NOT NULL.
        cursor.execute(
            'INSERT INTO knowledge_queries (id, user_id, query, answer, created_at) VALUES (?, ?, ?, ?, ?)',
            (query_id, user_id, query, answer, now)
        )
        self.conn.commit()
    
    def _get_sources(self, context_chunks: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Get source information for citation with numbered references
        
        Args:
            context_chunks: Context chunks with their metadata
            
        Returns:
            List of unique sources with document numbers
        """
        sources = []
        seen_documents = set()
        document_counter = 1
        
        for chunk in context_chunks:
            doc_filename = chunk['filename']
            if doc_filename not in seen_documents:
                sources.append({
                    'document': doc_filename,
                    'document_number': document_counter,
                    'chunk_id': chunk['id'],
                    'metadata': chunk.get('metadata') or {}
                })
                seen_documents.add(doc_filename)
                document_counter += 1
            
        return sources
    
    def delete_document(self, document_id: str) -> bool:
        """Delete a document and its chunks from the system
        
        Args:
            document_id: Document ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get file path (and the stable access_id so grants can be cleared)
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT file_path, access_id, collection_name FROM knowledge_documents WHERE id = ?',
                (document_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return False
                
            file_path, access_id = row[0], row[1]
            collection = row[2] or collection_access.DEFAULT_KNOWLEDGE_COLLECTION
            
            # Delete file if it exists
            if os.path.exists(file_path):
                os.remove(file_path)
            
            # Get chunk IDs before deleting them from the database
            chunk_ids = []
            cursor.execute('SELECT id FROM knowledge_chunks WHERE document_id = ?', (document_id,))
            for row in cursor.fetchall():
                chunk_ids.append(row[0])
            
            # Delete from database - explicitly delete child records since SQLite
            # foreign key cascading is not enabled by default
            cursor.execute('DELETE FROM knowledge_document_roles WHERE document_id = ?', (document_id,))
            cursor.execute('DELETE FROM knowledge_document_tags WHERE document_id = ?', (document_id,))
            cursor.execute('DELETE FROM knowledge_chunk_metadata WHERE document_id = ?', (document_id,))
            cursor.execute('DELETE FROM knowledge_chunks WHERE document_id = ?', (document_id,))
            cursor.execute('DELETE FROM knowledge_documents WHERE id = ?', (document_id,))
            self.conn.commit()

            # Drop the document's generic role grants so a future row reusing the
            # id can never inherit them.
            if access_id is not None:
                resource_access.set_access("knowledge_document", int(access_id), [])
            
            # Remove from processing status dict if present
            if document_id in self.processing_status:
                del self.processing_status[document_id]                # Delete embeddings from vector store
            if chunk_ids and self.vector_store:
                try:
                    # Process deletions in smaller batches to avoid syntax errors
                    self.logger.info(f"Deleting {len(chunk_ids)} chunks from vector store in batches")
                    batch_size = 15  # A reasonable batch size to prevent filter syntax errors
                    
                    # Create batches of chunk IDs
                    for i in range(0, len(chunk_ids), batch_size):
                        batch_chunk_ids = chunk_ids[i:i+batch_size]
                        self.logger.info(f"Processing batch {i//batch_size + 1} with {len(batch_chunk_ids)} chunks")
                        
                        # Delete each chunk individually to avoid syntax issues with the filter expression
                        for chunk_id in batch_chunk_ids:
                            try:
                                # Create ChromaDB filter for chunk ID
                                filter_expr = {"chunk_id": chunk_id}
                                
                                self.logger.info(f"Deleting chunk with filter: {filter_expr}")
                                self.vector_store.delete_by_filter(
                                    collection_name=collection,
                                    filter_expr=filter_expr
                                )
                            except Exception as chunk_err:
                                self.logger.info(f"Error deleting chunk {chunk_id}: {str(chunk_err)}")
                        
                        # Flush after each batch
                        # self.vector_store.client.flush('knowledge_chunks')
                    
                    # Reload the collection after all deletions
                    # self.vector_store.client.load_collection('knowledge_chunks')
                    self.logger.info(f"Successfully deleted chunks from vector store for document {document_id}")
                except Exception as e:
                    self.logger.info(f"Error deleting chunks from vector store: {str(e)}", exc_info=True)
                    # Continue despite vector store errors - document is already removed from DB
            
            return True
        except Exception as e:
            self.logger.info(f"Error deleting document {document_id}: {str(e)}", exc_info=True)
            return False

    def get_document_info(self, document_id: str) -> Dict[str, Any]:
        """Get detailed information about a document
        
        Args:
            document_id: Document ID
            
        Returns:
            Dictionary with document information or None if not found
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                '''SELECT id, original_filename, file_path, content_type, status, 
                   created_at, updated_at, processed_at, error,
                   chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name
                   FROM knowledge_documents WHERE id = ?''',
                (document_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            (doc_id, filename, file_path, content_type, status, created_at, updated_at, processed_at, error,
             chunking_method, chunk_size, chunk_overlap, metadata_columns, data_columns, collection_name) = row
            
            # Get chunk count
            cursor.execute('SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?', (doc_id,))
            chunk_count = cursor.fetchone()[0]
            
            # Get tags
            tags = self.get_document_tags(doc_id)
            
            # Get allowed roles
            allowed_roles = self.get_document_roles(doc_id)
            
            return {
                'id': doc_id,
                'access_id': knowledge_access.access_id_for(doc_id),
                'original_filename': filename,
                'file_path': file_path,
                'content_type': content_type,
                'status': status,
                'created_at': created_at,
                'updated_at': updated_at,
                'processed_at': processed_at,
                'error': error,
                'chunk_count': chunk_count,
                'tags': tags,
                'allowed_roles': allowed_roles,
                'chunking_method': chunking_method,
                'chunk_size': chunk_size,
                'chunk_overlap': chunk_overlap,
                'metadata_columns': knowledge_ingest.decode_columns(metadata_columns),
                'data_columns': knowledge_ingest.decode_columns(data_columns),
                'collection_name': collection_name or collection_access.DEFAULT_KNOWLEDGE_COLLECTION,
            }
        except Exception as e:
            self.logger.error(f"Error getting document info {document_id}: {str(e)}", exc_info=True)
            return None

    def get_document_markdown(self, document_id: str) -> str:
        """Get the markdown content of a processed document
        
        Args:
            document_id: Document ID
            
        Returns:
            Markdown content string or None if not found/not processed
        """
        try:
            document_info = self.get_document_info(document_id)
            if not document_info:
                return None
            
            # If document is not completed, return None
            if document_info['status'] != 'completed':
                return None
            
            file_path = document_info['file_path']
            content_type = document_info['content_type']
            
            # Check if file exists
            if not os.path.exists(file_path):
                return None
            
            # For text content, read directly
            if content_type == 'txt':
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            
            # For other file types, convert using markitdown
            try:
                result = self.md_converter.convert(file_path)
                return result.text_content
            except Exception as e:
                self.logger.error(f"Error converting document to markdown: {str(e)}", exc_info=True)
                return None
            
        except Exception as e:
            self.logger.error(f"Error getting document markdown {document_id}: {str(e)}", exc_info=True)
            return None