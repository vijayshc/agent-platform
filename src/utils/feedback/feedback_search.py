"""Semantic similarity search, reranking, and migration for feedback queries."""

import logging
import pickle
import time
from typing import Any, Dict, List, Optional
import numpy as np
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("text2sql.feedback_search")


def generate_embedding(manager, text_val: str) -> Optional[np.ndarray]:
    """Generate embedding for the given text using centralized embedding module."""
    if not text_val:
        return None
    try:
        from src.utils.llm_engine import LLMEngine
        llm_engine = LLMEngine()
        return llm_engine.generate_embedding(text_val)
    except Exception as e:
        manager.logger.error("Error generating embedding: %s", e, exc_info=True)
        return None


def get_reranking_model(manager):
    """Get the cross-encoder reranking model."""
    try:
        from src.utils.llm_engine import LLMEngine
        llm_engine = LLMEngine()
        return llm_engine.get_reranking_model()
    except Exception as e:
        manager.logger.error("Failed to get reranking model: %s", e, exc_info=True)
        return None


def find_similar_queries(manager, query_text: str, limit: int = 1, positive_only: bool = True) -> List[Dict[str, Any]]:
    """Find similar previous queries based on vector similarity."""
    embedding_vector = manager._generate_embedding(query_text)
    manager.logger.info("Generated embedding for query: '%s...'", embedding_vector)

    if embedding_vector is None:
        manager.logger.info("Embedding generation failed, falling back to text-based search")
        return manager._find_similar_queries_text_based(query_text, limit, positive_only)

    try:
        filter_expr = {"feedback_rating": 1} if positive_only else None
        similar_queries = manager.vector_store.search_similar(
            collection_name=manager.collection_name,
            vector=embedding_vector.tolist(),
            limit=limit,
            filter_expr=filter_expr,
        )

        if similar_queries:
            manager.logger.info("Found %d similar queries using vector similarity for '%s...'", len(similar_queries), query_text[:50])
            return similar_queries
        else:
            manager.logger.info("No similar queries found in vector store, falling back to text-based search")
            return manager._find_similar_queries_text_based(query_text, limit, positive_only)

    except Exception as e:
        manager.logger.error("Error finding similar queries with vector search: %s", e, exc_info=True)
        return manager._find_similar_queries_text_based(query_text, limit, positive_only)


def find_similar_queries_with_reranking(
    manager, query_text: str, limit: int = 2, positive_only: bool = True
) -> List[Dict[str, Any]]:
    """Find similar queries using vector search followed by cross-encoder reranking."""
    try:
        manager.logger.info("Stage 1: Vector search for '%s...'", query_text[:50])
        initial_candidates_limit = 10
        embedding_vector = manager._generate_embedding(query_text)

        if embedding_vector is None:
            manager.logger.info("Embedding generation failed, falling back to text-based search")
            return manager._find_similar_queries_text_based(query_text, limit, positive_only)

        top_candidates = manager.vector_store.search_similar(
            collection_name=manager.collection_name,
            vector=embedding_vector.tolist(),
            limit=initial_candidates_limit,
        )

        if not top_candidates:
            manager.logger.info("No candidates found from vector search, falling back to text-based search")
            return manager._find_similar_queries_text_based(query_text, limit, positive_only)

        manager.logger.info("Found %d initial candidates from vector search", len(top_candidates))

        if len(top_candidates) > limit:
            manager.logger.info("Stage 2: Reranking %d candidates", len(top_candidates))
            reranker = manager._get_reranking_model()
            if reranker:
                candidate_pairs = []
                valid_candidates = []
                for candidate in top_candidates:
                    if "query_text" in candidate:
                        candidate_pairs.append((query_text, candidate["query_text"]))
                        valid_candidates.append(candidate)
                    elif "text" in candidate:
                        candidate_pairs.append((query_text, candidate["text"]))
                        candidate["query_text"] = candidate["text"]
                        valid_candidates.append(candidate)

                top_candidates = valid_candidates
                if not candidate_pairs:
                    return top_candidates[:limit]

                try:
                    rerank_scores = reranker.predict(candidate_pairs)
                    for idx, score in enumerate(rerank_scores):
                        top_candidates[idx]["rerank_score"] = float(score)

                    positive_scored = [c for c in top_candidates if c["rerank_score"] >= 0]
                    if positive_scored:
                        reranked = sorted(positive_scored, key=lambda x: x["rerank_score"], reverse=True)
                        return reranked[:limit]
                    return top_candidates[:limit]
                except Exception as e:
                    manager.logger.error("Reranking failed: %s", e, exc_info=True)
                    return top_candidates[:limit]
            return top_candidates[:limit]
        return top_candidates[:limit]
    except Exception as e:
        manager.logger.error("Error in find_similar_queries_with_reranking: %s", e, exc_info=True)
        return manager._find_similar_queries_text_based(query_text, limit, positive_only)


def find_similar_queries_text_based(
    manager, query_text: str, limit: int = 1, positive_only: bool = True
) -> List[Dict[str, Any]]:
    """Fallback method using SQL LIKE text search."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database for text-based search")
        return []

    try:
        search_terms = [term for term in query_text.lower().split() if len(term) > 3]
        if not search_terms:
            manager.logger.info("No valid search terms found in query")
            return []

        like_conditions = [f"LOWER(query_text) LIKE '%{term}%'" for term in search_terms]
        where_clause = " OR ".join(like_conditions)
        if positive_only:
            where_clause = f"({where_clause}) AND feedback_rating = 1"

        with manager.engine.connect() as conn:
            query = text(f"""
            SELECT feedback_id, query_text, sql_query, results_summary, 
                   workspace, feedback_rating, created_at, tables_used
            FROM query_feedback
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT :limit
            """)
            result = conn.execute(query, {"limit": limit})
            similar_queries = []
            for row in result:
                tables_list = row.tables_used.split(",") if row.tables_used else []
                similar_queries.append({
                    "feedback_id": row.feedback_id,
                    "query_text": row.query_text,
                    "sql_query": row.sql_query,
                    "results_summary": row.results_summary,
                    "workspace": row.workspace,
                    "feedback_rating": row.feedback_rating,
                    "created_at": row.created_at,
                    "tables_used": tables_list,
                })

        manager.logger.info("Found %d similar queries using text-based search for '%s...'", len(similar_queries), query_text[:50])
        return similar_queries
    except SQLAlchemyError as e:
        manager.logger.error("Error in text-based search: %s", e, exc_info=True)
        return []


def migrate_existing_embeddings(manager) -> Dict[str, Any]:
    """Migrate existing embeddings from SQLite to vector store."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database for migration")
        return {"success": False, "total": 0, "migrated": 0}

    try:
        manager.logger.info("Starting migration of existing embeddings to vector store")
        start_time = time.time()
        migrated = 0
        failed = 0

        with manager.engine.connect() as conn:
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
                    stored_embedding = pickle.loads(row.embedding)
                    tables_list = row.tables_used.split(",") if row.tables_used else []
                    metadata = {
                        "sql_query": row.sql_query,
                        "results_summary": row.results_summary,
                        "workspace": row.workspace,
                        "feedback_rating": row.feedback_rating,
                        "tables_used": tables_list,
                        "is_manual_sample": bool(row.is_manual_sample),
                        "created_at": str(row.created_at),
                    }
                    success = manager.vector_store.insert_embedding(
                        collection_name=manager.collection_name,
                        feedback_id=row.feedback_id,
                        vector=stored_embedding.tolist(),
                        query_text=row.query_text,
                        metadata=metadata,
                    )
                    if success:
                        migrated += 1
                    else:
                        failed += 1
                except Exception as e:
                    manager.logger.error("Error migrating embedding %s: %s", row.feedback_id, e)
                    failed += 1

        total_time = time.time() - start_time
        return {
            "success": True,
            "total": migrated + failed,
            "migrated": migrated,
            "failed": failed,
            "time_seconds": total_time,
        }
    except SQLAlchemyError as e:
        manager.logger.error("Error during embedding migration: %s", e, exc_info=True)
        return {"success": False, "total": 0, "migrated": 0}
