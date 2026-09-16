"""
Feedback Manager for Text2SQL application.
Handles storing and retrieving user feedback on SQL queries.
"""

import sqlite3
import logging
import time
import json
import pickle
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Dict, Any, Optional, Tuple

from config.config import DATABASE_URI
from src.utils.database import create_db_engine
from src.utils.vector_store import VectorStore

class FeedbackManager:
    """Manager class for handling user feedback on SQL queries"""
    
    def __init__(self, connection_string=None, vector_uri=None):
        """Initialize the feedback manager

        Args:
            connection_string (str, optional): SQLAlchemy connection string, defaults to config
            vector_uri (str, optional): URI for the vector database, defaults to local file storage
        """
        self.logger = logging.getLogger('text2sql.feedback')
        self.connection_string = connection_string or DATABASE_URI
        self.logger.info(f"Feedback manager initialized with connection: {self.connection_string}")
        self.engine = None
        self.embedding_model = None
        
        # Initialize vector store
        self.vector_store = VectorStore(uri=vector_uri)
        
        # Default collection name for storing query embeddings
        self.collection_name = "query_embeddings"
        
    def connect(self):
        """Establish database connection
        
        Returns:
            bool: True if successful, False otherwise
        """
        start_time = time.time()
        self.logger.info("Attempting database connection")
        
        try:
            self.engine = create_db_engine(self.connection_string)
            self.logger.info(f"Database connection established in {time.time() - start_time:.2f}s")
            
            # Also connect to vector store
            vector_conn_success = self.vector_store.connect()
            if not vector_conn_success:
                self.logger.info("Failed to connect to vector store, vector similarity search will be unavailable")
            
            return True
        except Exception as e:
            self.logger.error(f"Database connection error: {str(e)}", exc_info=True)
            print(f"Database connection error: {e}")
            return False
    
    # _get_embedding_model method has been moved to LLMEngine class
    
    def _generate_embedding(self, text: str) -> Optional[np.ndarray]:
        """Generate embedding for the given text using the centralized LLM engine
        
        Args:
            text (str): Text to generate embedding for
            
        Returns:
            Optional[np.ndarray]: Embedding vector or None if failed
        """
        if not text:
            return None
            
        try:
            # Use the centralized LLM engine to generate embeddings
            from src.utils.llm_engine import LLMEngine
            llm_engine = LLMEngine()
            embedding = llm_engine.generate_embedding(text)
            
            # Return embedding result
            return embedding
            
        except Exception as e:
            self.logger.error(f"Error generating embedding: {str(e)}", exc_info=True)
            return None
            
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
            self.logger.error(f"Failed to get reranking model from LLMEngine: {str(e)}", exc_info=True)
            return None
    
    def save_feedback(self, query_text: str, sql_query: str, results_summary: str, 
                      workspace: str, feedback_rating: int, tables_used: List[str], 
                      is_manual_sample: bool = False) -> bool:
        """Save user feedback to database
        
        Args:
            query_text (str): The original natural language query
            sql_query (str): The generated SQL query
            results_summary (str): Summary of the query results (e.g., "10 rows returned")
            workspace (str): Name of the workspace used
            feedback_rating (int): 1 for thumbs up, 0 for thumbs down
            tables_used (List[str]): List of tables used in the query
            is_manual_sample (bool): Flag to indicate if this is a manually added sample
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database to save feedback")
            return False
            
        try:
            # Convert tables list to comma-separated string
            tables_str = ','.join(tables_used) if tables_used else None
            
            # Generate embedding for the query text
            embedding_vector = self._generate_embedding(query_text)
            
            # Insert feedback into database (store a null for embedding, we'll use vector store)
            with self.engine.connect() as conn:
                # Start a transaction
                trans = conn.begin()
                try:
                    query = text("""
                    INSERT INTO query_feedback 
                    (query_text, sql_query, results_summary, workspace, feedback_rating, tables_used, embedding, is_manual_sample)
                    VALUES (:query_text, :sql_query, :results_summary, :workspace, :feedback_rating, :tables_used, NULL, :is_manual_sample)
                    RETURNING feedback_id
                    """)
                    
                    result = conn.execute(query, {
                        'query_text': query_text,
                        'sql_query': sql_query,
                        'results_summary': results_summary,
                        'workspace': workspace,
                        'feedback_rating': feedback_rating,
                        'tables_used': tables_str,
                        'is_manual_sample': is_manual_sample
                    })
                    feedback_id = result.scalar()
                    result.close()  # Close the result to free up the connection
                    trans.commit()  # Commit the transaction
                except Exception as e:
                    trans.rollback()  # Rollback on error
                    raise e
            
            # Store embedding in vector database if available
            if embedding_vector is not None:
                metadata = {
                    'sql_query': sql_query,
                    'results_summary': results_summary,
                    'workspace': workspace,
                    'feedback_rating': feedback_rating,
                    'tables_used': tables_used,
                    'is_manual_sample': is_manual_sample,
                    'created_at': time.strftime('%Y-%m-%d %H:%M:%S')
                }
                
                self.vector_store.insert_embedding(
                    feedback_id=feedback_id,
                    vector=embedding_vector.tolist(),
                    query_text=query_text,
                    metadata=metadata,
                    collection_name=self.collection_name
                )
            
            if is_manual_sample:
                self.logger.info(f"Saved manual sample for query: '{query_text[:50]}...'")
            else:
                self.logger.info(f"Saved feedback for query: '{query_text[:50]}...' with rating {feedback_rating}")
            return True
            
        except SQLAlchemyError as e:
            self.logger.error(f"Error saving feedback: {str(e)}", exc_info=True)
            return False
    
    def find_similar_queries(self, query_text: str, limit: int = 1, positive_only: bool = True) -> List[Dict[str, Any]]:
        """Find similar previous queries based on vector similarity
        
        Args:
            query_text (str): The natural language query to find similar queries for
            limit (int): Maximum number of similar queries to return
            positive_only (bool): If True, only return queries with positive feedback (thumbs up)
            
        Returns:
            List[Dict]: List of similar queries with their SQL and other details
        """
        # Generate embedding for the input query
        embedding_vector = self._generate_embedding(query_text)
        self.logger.info(f"Generated embedding for query: '{embedding_vector}...'")
        
        # If embedding generation failed, fall back to text-based search
        if embedding_vector is None:
            self.logger.info("Embedding generation failed, falling back to text-based search")
            return self._find_similar_queries_text_based(query_text, limit, positive_only)
        
        try:
            # Create filter expression for positive_only if needed
            filter_expr = {"feedback_rating": 1} if positive_only else None
            
            # Use vector store for similarity search with collection name
            similar_queries = self.vector_store.search_similar(
                collection_name=self.collection_name,
                vector=embedding_vector.tolist(),
                limit=limit,
                filter_expr=filter_expr
            )
            
            if similar_queries:
                self.logger.info(f"Found {len(similar_queries)} similar queries using vector similarity for '{query_text[:50]}...'")
                return similar_queries
            else:
                self.logger.info(f"No similar queries found in vector store, falling back to text-based search")
                return self._find_similar_queries_text_based(query_text, limit, positive_only)
            
        except Exception as e:
            self.logger.error(f"Error finding similar queries with vector search: {str(e)}", exc_info=True)
            return self._find_similar_queries_text_based(query_text, limit, positive_only)
    
    def find_similar_queries_with_reranking(self, query_text: str, limit: int = 2, positive_only: bool = True) -> List[Dict[str, Any]]:
        """Find similar previous queries using a two-stage search: vector similarity + reranking
        
        Args:
            query_text (str): The natural language query to find similar queries for
            limit (int): Maximum number of final results to return after reranking
            positive_only (bool): If True, only return queries with positive feedback (thumbs up)
            
        Returns:
            List[Dict]: List of similar queries with their SQL and other details
        """
        try:
            # Stage 1: Vector search to get initial candidates (top 10)
            self.logger.info(f"Stage 1: Vector search for '{query_text[:50]}...'")
            initial_candidates_limit = 10  # Get top 10 candidates from vector search
            
            # Generate embedding for the input query
            embedding_vector = self._generate_embedding(query_text)
            
            # If embedding generation failed, fall back to text-based search
            if embedding_vector is None:
                self.logger.info("Embedding generation failed, falling back to text-based search")
                return self._find_similar_queries_text_based(query_text, limit, positive_only)
            
            # Create filter expression for positive_only if needed
            filter_expr = {"feedback_rating": 1} if positive_only else None
            
            # Use vector store for similarity search to get initial candidates
            top_candidates = self.vector_store.search_similar(
                collection_name=self.collection_name,
                vector=embedding_vector.tolist(),
                limit=initial_candidates_limit,
                # filter_expr=filter_expr
            )

 
           
            if not top_candidates:
                self.logger.info("No candidates found from vector search, falling back to text-based search")
                return self._find_similar_queries_text_based(query_text, limit, positive_only)
                
            self.logger.info(f"Found {len(top_candidates)} initial candidates from vector search")
            
            # Stage 2: Rerank the top candidates using a more powerful model
            if len(top_candidates) > limit:
                self.logger.info(f"Stage 2: Reranking {len(top_candidates)} candidates")
                reranker = self._get_reranking_model()
                
                if (reranker):
                    # Prepare candidate pairs for reranking
                    # Handle cases where 'query_text' might be missing in the candidates
                    candidate_pairs = []
                    valid_candidates = []
                    
                    for candidate in top_candidates:
                        # Check if query_text exists, otherwise try to find it in other fields
                        if 'query_text' in candidate:
                            candidate_pairs.append((query_text, candidate['query_text']))
                            valid_candidates.append(candidate)
                        else:
                            # Look for query text in metadata fields
                            self.logger.debug(f"'query_text' not found in candidate, checking alternative fields")
                            if 'text' in candidate:
                                candidate_pairs.append((query_text, candidate['text']))
                                valid_candidates.append(candidate)
                                # Create query_text field for consistency
                                candidate['query_text'] = candidate['text']
                            else:
                                self.logger.info(f"Skipping candidate without query text: {candidate}")
                    
                    # Update top_candidates to only include valid ones
                    top_candidates = valid_candidates
                    
                    if not candidate_pairs:
                        self.logger.info("No valid candidates found with query text, skipping reranking")
                        return top_candidates[:limit]
                        
                    # Get reranking scores
                    try:
                        rerank_scores = reranker.predict(candidate_pairs)
                        self.logger.info(f"Reranking scores: {rerank_scores}")
                        
                        # Add rerank scores to candidates
                        for idx, score in enumerate(rerank_scores):
                            top_candidates[idx]['rerank_score'] = float(score)
                         
                        # Filter out candidates with negative scores (indicating poor relevance)
                        positive_scored_candidates = [c for c in top_candidates if c['rerank_score'] >= 0]
                        
                        if positive_scored_candidates:
                            self.logger.info(f"Found {len(positive_scored_candidates)} candidates with positive reranking scores")
                            # Sort by rerank score (higher is better) among positive scores
                            reranked_candidates = sorted(positive_scored_candidates, key=lambda x: x['rerank_score'], reverse=True)
                            
                            self.logger.info(f"Reranking successful, returning top {min(limit, len(reranked_candidates))} results")
                            return reranked_candidates[:limit]
                        else:
                            self.logger.info("No candidates with positive reranking scores, returning vector search results")
                            return top_candidates[:limit]
                        
                    except Exception as e:
                        self.logger.error(f"Reranking failed: {str(e)}", exc_info=True)
                        self.logger.info("Falling back to vector search results")
                        return top_candidates[:limit]
                else:
                    self.logger.info("Reranker not available, returning vector search results")
                    return top_candidates[:limit]
            else:
                # Not enough candidates to rerank
                self.logger.info(f"Less than {limit+1} candidates, skipping reranking")
                return top_candidates[:limit]
                
        except Exception as e:
            self.logger.error(f"Error in find_similar_queries_with_reranking: {str(e)}", exc_info=True)
            return self._find_similar_queries_text_based(query_text, limit, positive_only)
    
    def _find_similar_queries_text_based(self, query_text: str, limit: int = 1, positive_only: bool = True) -> List[Dict[str, Any]]:
        """Fallback method to find similar queries using text-based search
        
        Args:
            query_text (str): The natural language query to find similar queries for
            limit (int): Maximum number of similar queries to return
            positive_only (bool): If True, only return queries with positive feedback (thumbs up)
            
        Returns:
            List[Dict]: List of similar queries with their SQL and other details
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database for text-based search")
            return []
            
        try:
            # Create a simple filter condition based on text similarity
            search_terms = [term for term in query_text.lower().split() if len(term) > 3]
            
            if not search_terms:
                self.logger.info("No valid search terms found in query")
                return []
                
            # Build a simple LIKE query for each search term
            like_conditions = []
            for term in search_terms:
                like_conditions.append(f"LOWER(query_text) LIKE '%{term}%'")
                
            # Combine conditions
            where_clause = " OR ".join(like_conditions)
            
            # Add feedback filter if positive_only is True
            if positive_only:
                where_clause = f"({where_clause}) AND feedback_rating = 1"
                
            # Execute query to find similar queries
            with self.engine.connect() as conn:
                query = text(f"""
                SELECT feedback_id, query_text, sql_query, results_summary, 
                       workspace, feedback_rating, created_at, tables_used
                FROM query_feedback
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit
                """)
                
                result = conn.execute(query, {'limit': limit})
                similar_queries = []
                
                for row in result:
                    tables_list = row.tables_used.split(',') if row.tables_used else []
                    similar_queries.append({
                        'feedback_id': row.feedback_id,
                        'query_text': row.query_text,
                        'sql_query': row.sql_query,
                        'results_summary': row.results_summary,
                        'workspace': row.workspace,
                        'feedback_rating': row.feedback_rating,
                        'created_at': row.created_at,
                        'tables_used': tables_list
                    })
                    
            self.logger.info(f"Found {len(similar_queries)} similar queries using text-based search for '{query_text[:50]}...'")
            return similar_queries
            
        except SQLAlchemyError as e:
            self.logger.error(f"Error in text-based search: {str(e)}", exc_info=True)
            return []
            
    def get_feedback_stats(self) -> Dict[str, Any]:
        """Get feedback statistics from the database
        
        Returns:
            Dict: Statistics about stored feedback
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database to get feedback stats")
            return {'total': 0, 'positive': 0, 'negative': 0}
            
        try:
            with self.engine.connect() as conn:
                query = text("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN feedback_rating = 1 THEN 1 ELSE 0 END) as positive,
                       SUM(CASE WHEN feedback_rating = 0 THEN 1 ELSE 0 END) as negative
                FROM query_feedback
                """)
                
                result = conn.execute(query).fetchone()
                
                stats = {
                    'total': result.total or 0,
                    'positive': result.positive or 0,
                    'negative': result.negative or 0
                }
                
            self.logger.info(f"Feedback stats: {stats['total']} total, {stats['positive']} positive, {stats['negative']} negative")
            return stats
            
        except SQLAlchemyError as e:
            self.logger.error(f"Error getting feedback stats: {str(e)}", exc_info=True)
            return {'total': 0, 'positive': 0, 'negative': 0}
            
    def get_samples(self, page: int = 1, limit: int = 10, search_query: str = None) -> Tuple[List[Dict[str, Any]], int]:
        """Get paginated list of sample entries (manual + positive feedback)
        
        Args:
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            search_query (str, optional): Filter samples by query text
            
        Returns:
            Tuple[List[Dict], int]: List of samples and total count
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database to get samples")
            return [], 0
            
        try:
            if search_query and self.vector_store.client:
                # Use vector search for more semantic search capabilities if a query is provided
                embedding_vector = self._generate_embedding(search_query)
                if embedding_vector is not None:
                    filter_expr = {"feedback_rating": 1}
                    vector_results = self.vector_store.search_similar(
                        collection_name=self.collection_name,
                        vector=embedding_vector.tolist(),
                        limit=limit,
                        filter_expr=filter_expr
                    )
                    
                    if vector_results:
                        # Calculate total from database for pagination
                        with self.engine.connect() as conn:
                            count_query = text("SELECT COUNT(*) as total FROM query_feedback WHERE feedback_rating = 1")
                            total = conn.execute(count_query).scalar() or 0
                        return vector_results, total
            
            # Fallback to traditional database query
            offset = (page - 1) * limit
            
            # Build where clause
            where_clause = "feedback_rating = 1"  # Only positive feedback samples
            params = {'limit': limit, 'offset': offset}
            
            if search_query:
                where_clause += " AND LOWER(query_text) LIKE :search_query"
                params['search_query'] = f"%{search_query.lower()}%"
            
            with self.engine.connect() as conn:
                # Get total count
                count_query = text(f"SELECT COUNT(*) as total FROM query_feedback WHERE {where_clause}")
                total = conn.execute(count_query, params).scalar() or 0
                
                # Get samples
                query = text(f"""
                SELECT feedback_id, query_text, sql_query, results_summary, 
                       workspace, feedback_rating, created_at, tables_used, is_manual_sample
                FROM query_feedback
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """)
                
                result = conn.execute(query, params)
                samples = []
                
                for row in result:
                    tables_list = row.tables_used.split(',') if row.tables_used else []
                    samples.append({
                        'feedback_id': row.feedback_id,
                        'query_text': row.query_text,
                        'sql_query': row.sql_query,
                        'results_summary': row.results_summary,
                        'workspace': row.workspace,
                        'feedback_rating': row.feedback_rating,
                        'created_at': row.created_at,
                        'tables_used': tables_list,
                        'is_manual_sample': bool(row.is_manual_sample)
                    })
                
            self.logger.info(f"Retrieved {len(samples)} samples (page {page}, limit {limit})")
            return samples, total
            
        except SQLAlchemyError as e:
            self.logger.error(f"Error getting samples: {str(e)}", exc_info=True)
            return [], 0
    
    def get_sample_by_id(self, sample_id: int) -> Optional[Dict[str, Any]]:
        """Get a single sample entry by ID
        
        Args:
            sample_id (int): ID of the sample to retrieve
            
        Returns:
            Optional[Dict]: Sample data or None if not found
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database to get sample")
            return None
            
        try:
            with self.engine.connect() as conn:
                query = text("""
                SELECT feedback_id, query_text, sql_query, results_summary, 
                       workspace, feedback_rating, created_at, tables_used, is_manual_sample
                FROM query_feedback
                WHERE feedback_id = :sample_id
                """)
                
                result = conn.execute(query, {'sample_id': sample_id}).fetchone()
                
                if not result:
                    return None
                    
                tables_list = result.tables_used.split(',') if result.tables_used else []
                sample = {
                    'feedback_id': result.feedback_id,
                    'query_text': result.query_text,
                    'sql_query': result.sql_query,
                    'results_summary': result.results_summary,
                    'workspace': result.workspace,
                    'feedback_rating': result.feedback_rating,
                    'created_at': result.created_at,
                    'tables_used': tables_list,
                    'is_manual_sample': bool(result.is_manual_sample)
                }
                
                return sample
                
        except SQLAlchemyError as e:
            self.logger.error(f"Error getting sample by ID: {str(e)}", exc_info=True)
            return None
    
    def update_sample(self, sample_id: int, data: Dict[str, Any]) -> bool:
        """Update an existing sample
        
        Args:
            sample_id (int): ID of the sample to update
            data (Dict): Updated sample data
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database to update sample")
            return False
            
        try:
            # Convert tables list to comma-separated string
            tables_used = data.get('tables_used', [])
            tables_str = ','.join(tables_used) if tables_used else None
            
            # Update in SQLite
            with self.engine.connect() as conn:
                trans = conn.begin()
                try:
                    query = text("""
                    UPDATE query_feedback
                    SET query_text = :query_text,
                        sql_query = :sql_query,
                        results_summary = :results_summary,
                        workspace = :workspace,
                        feedback_rating = :feedback_rating,
                        tables_used = :tables_used,
                        is_manual_sample = :is_manual_sample
                    WHERE feedback_id = :sample_id
                    """)
                    
                    conn.execute(query, {
                        'sample_id': sample_id,
                        'query_text': data['query_text'],
                        'sql_query': data['sql_query'],
                        'results_summary': data.get('results_summary', ''),
                        'workspace': data.get('workspace', 'Default'),
                        'feedback_rating': data.get('feedback_rating', 1),
                        'tables_used': tables_str,
                        'is_manual_sample': data.get('is_manual_sample', True)
                    })
                    trans.commit()
                except Exception as e:
                    trans.rollback()
                    raise e
            
            # Update vector embedding in ChromaDB
            embedding_vector = self._generate_embedding(data['query_text'])
            if embedding_vector is not None:
                metadata = {
                    'sql_query': data['sql_query'],
                    'results_summary': data.get('results_summary', ''),
                    'workspace': data.get('workspace', 'Default'),
                    'feedback_rating': data.get('feedback_rating', 1),
                    'tables_used': tables_used,
                    'is_manual_sample': data.get('is_manual_sample', True),
                    'created_at': data.get('created_at', time.strftime('%Y-%m-%d %H:%M:%S'))
                }
                
                self.vector_store.update_embedding(
                    collection_name=self.collection_name,
                    feedback_id=sample_id,
                    vector=embedding_vector.tolist(),
                    query_text=data['query_text'],
                    metadata=metadata
                )
                
            self.logger.info(f"Updated sample ID {sample_id}")
            return True
            
        except SQLAlchemyError as e:
            self.logger.error(f"Error updating sample: {str(e)}", exc_info=True)
            return False
    
    def delete_sample(self, sample_id: int) -> bool:
        """Delete a sample
        
        Args:
            sample_id (int): ID of the sample to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database to delete sample")
            return False
            
        try:
            # Delete from SQLite
            with self.engine.connect() as conn:
                trans = conn.begin()
                try:
                    query = text("DELETE FROM query_feedback WHERE feedback_id = :sample_id")
                    conn.execute(query, {'sample_id': sample_id})
                    trans.commit()
                except Exception as e:
                    trans.rollback()
                    raise e
            
            # Delete from vector store
            self.vector_store.delete_embedding(
                collection_name=self.collection_name,
                feedback_id=sample_id
            )
                
            self.logger.info(f"Deleted sample ID {sample_id}")
            return True
            
        except SQLAlchemyError as e:
            self.logger.error(f"Error deleting sample: {str(e)}", exc_info=True)
            return False
    
    def migrate_existing_embeddings(self):
        """Migrate existing embeddings from SQLite to the vector store
        
        Returns:
            Dict: Migration statistics
        """
        if not self.engine and not self.connect():
            self.logger.error("Failed to connect to database for migration")
            return {'success': False, 'total': 0, 'migrated': 0}
        
        try:
            self.logger.info("Starting migration of existing embeddings to vector store")
            start_time = time.time()
            migrated = 0
            failed = 0
            
            with self.engine.connect() as conn:
                # Get all entries with embeddings
                query = text("""
                SELECT feedback_id, query_text, sql_query, results_summary, 
                       workspace, feedback_rating, created_at, tables_used, 
                       is_manual_sample, embedding
                FROM query_feedback
                WHERE embedding IS NOT NULL
                """)
                
                result = conn.execute(query)
                
                for row in result:
                    try:
                        # Deserialize stored embedding from SQLite
                        stored_embedding = pickle.loads(row.embedding)
                        
                        # Prepare metadata
                        tables_list = row.tables_used.split(',') if row.tables_used else []
                        metadata = {
                            'sql_query': row.sql_query,
                            'results_summary': row.results_summary,
                            'workspace': row.workspace,
                            'feedback_rating': row.feedback_rating,
                            'tables_used': tables_list,
                            'is_manual_sample': bool(row.is_manual_sample),
                            'created_at': str(row.created_at)
                        }
                        
                        # Insert into vector store
                        success = self.vector_store.insert_embedding(
                            collection_name=self.collection_name,
                            feedback_id=row.feedback_id,
                            vector=stored_embedding.tolist(),
                            query_text=row.query_text,
                            metadata=metadata
                        )
                        
                        if success:
                            migrated += 1
                        else:
                            failed += 1
                            
                    except Exception as e:
                        self.logger.error(f"Error migrating embedding {row.feedback_id}: {str(e)}")
                        failed += 1
            
            total_time = time.time() - start_time
            total = migrated + failed
            
            self.logger.info(f"Embedding migration completed in {total_time:.2f}s: {migrated} succeeded, {failed} failed")
            
            return {
                'success': True,
                'total': total,
                'migrated': migrated,
                'failed': failed,
                'time_seconds': total_time
            }
            
        except SQLAlchemyError as e:
            self.logger.error(f"Error during embedding migration: {str(e)}", exc_info=True)
            return {'success': False, 'total': 0, 'migrated': 0}
            
    def close(self):
        """Close database connections"""
        self.logger.info("Closing database connections")
        if self.engine:
            self.engine.dispose()
            self.logger.debug("SQLite database engine disposed")
        
        self.vector_store.close()