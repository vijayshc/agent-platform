"""Feedback database persistence and administration operations."""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.utils.database import create_db_engine

logger = logging.getLogger("text2sql.feedback_store")


def connect_feedback_db(manager) -> bool:
    """Establish database connection and connect vector store."""
    start_time = time.time()
    manager.logger.info("Attempting database connection")
    try:
        manager.engine = create_db_engine(manager.connection_string)
        manager.logger.info("Database connection established in %.2fs", time.time() - start_time)
        vector_conn_success = manager.vector_store.connect()
        if not vector_conn_success:
            manager.logger.info("Failed to connect to vector store, vector similarity search will be unavailable")
        return True
    except Exception as e:
        manager.logger.error("Database connection error: %s", e, exc_info=True)
        return False


def save_feedback(
    manager,
    query_text: str,
    sql_query: str,
    results_summary: str,
    workspace: str,
    feedback_rating: int,
    tables_used: List[str],
    is_manual_sample: bool = False,
) -> bool:
    """Save user feedback to database and index in vector store."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database to save feedback")
        return False

    try:
        tables_str = ",".join(tables_used) if tables_used else None
        embedding_vector = manager._generate_embedding(query_text)

        with manager.engine.connect() as conn:
            trans = conn.begin()
            try:
                query = text("""
                INSERT INTO query_feedback 
                (query_text, sql_query, results_summary, workspace, feedback_rating, tables_used, embedding, is_manual_sample)
                VALUES (:query_text, :sql_query, :results_summary, :workspace, :feedback_rating, :tables_used, NULL, :is_manual_sample)
                RETURNING feedback_id
                """)
                result = conn.execute(query, {
                    "query_text": query_text,
                    "sql_query": sql_query,
                    "results_summary": results_summary,
                    "workspace": workspace,
                    "feedback_rating": feedback_rating,
                    "tables_used": tables_str,
                    "is_manual_sample": is_manual_sample,
                })
                feedback_id = result.scalar()
                result.close()
                trans.commit()
            except Exception as e:
                trans.rollback()
                raise e

        if embedding_vector is not None:
            metadata = {
                "sql_query": sql_query,
                "results_summary": results_summary,
                "workspace": workspace,
                "feedback_rating": feedback_rating,
                "tables_used": tables_used,
                "is_manual_sample": is_manual_sample,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            manager.vector_store.insert_embedding(
                feedback_id=feedback_id,
                vector=embedding_vector.tolist(),
                query_text=query_text,
                metadata=metadata,
                collection_name=manager.collection_name,
            )

        if is_manual_sample:
            manager.logger.info("Saved manual sample for query: '%s...'", query_text[:50])
        else:
            manager.logger.info("Saved feedback for query: '%s...' with rating %s", query_text[:50], feedback_rating)
        return True

    except SQLAlchemyError as e:
        manager.logger.error("Error saving feedback: %s", e, exc_info=True)
        return False


def get_feedback_stats(manager) -> Dict[str, Any]:
    """Get feedback statistics from the database."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database to get feedback stats")
        return {"total": 0, "positive": 0, "negative": 0}

    try:
        with manager.engine.connect() as conn:
            query = text("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN feedback_rating = 1 THEN 1 ELSE 0 END) as positive,
                   SUM(CASE WHEN feedback_rating = 0 THEN 1 ELSE 0 END) as negative
            FROM query_feedback
            """)
            result = conn.execute(query).fetchone()
            stats = {
                "total": result.total or 0,
                "positive": result.positive or 0,
                "negative": result.negative or 0,
            }
        manager.logger.info("Feedback stats: %d total, %d positive, %d negative", stats["total"], stats["positive"], stats["negative"])
        return stats
    except SQLAlchemyError as e:
        manager.logger.error("Error getting feedback stats: %s", e, exc_info=True)
        return {"total": 0, "positive": 0, "negative": 0}


def get_samples(manager, page: int = 1, limit: int = 10, search_query: str = None) -> Tuple[List[Dict[str, Any]], int]:
    """Get paginated list of sample entries."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database to get samples")
        return [], 0

    try:
        if search_query and manager.vector_store.client:
            embedding_vector = manager._generate_embedding(search_query)
            if embedding_vector is not None:
                filter_expr = {"feedback_rating": 1}
                vector_results = manager.vector_store.search_similar(
                    collection_name=manager.collection_name,
                    vector=embedding_vector.tolist(),
                    limit=limit,
                    filter_expr=filter_expr,
                )
                if vector_results:
                    with manager.engine.connect() as conn:
                        count_query = text("SELECT COUNT(*) as total FROM query_feedback WHERE feedback_rating = 1")
                        total = conn.execute(count_query).scalar() or 0
                    return vector_results, total

        offset = (page - 1) * limit
        where_clause = "feedback_rating = 1"
        params: Dict[str, Any] = {"limit": limit, "offset": offset}

        if search_query:
            where_clause += " AND LOWER(query_text) LIKE :search_query"
            params["search_query"] = f"%{search_query.lower()}%"

        with manager.engine.connect() as conn:
            count_query = text(f"SELECT COUNT(*) as total FROM query_feedback WHERE {where_clause}")
            total = conn.execute(count_query, params).scalar() or 0

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
                tables_list = row.tables_used.split(",") if row.tables_used else []
                samples.append({
                    "feedback_id": row.feedback_id,
                    "query_text": row.query_text,
                    "sql_query": row.sql_query,
                    "results_summary": row.results_summary,
                    "workspace": row.workspace,
                    "feedback_rating": row.feedback_rating,
                    "created_at": row.created_at,
                    "tables_used": tables_list,
                    "is_manual_sample": bool(row.is_manual_sample),
                })

        manager.logger.info("Retrieved %d samples (page %d, limit %d)", len(samples), page, limit)
        return samples, total
    except SQLAlchemyError as e:
        manager.logger.error("Error getting samples: %s", e, exc_info=True)
        return [], 0


def get_sample_by_id(manager, sample_id: int) -> Optional[Dict[str, Any]]:
    """Get a single sample entry by ID."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database to get sample")
        return None

    try:
        with manager.engine.connect() as conn:
            query = text("""
            SELECT feedback_id, query_text, sql_query, results_summary, 
                   workspace, feedback_rating, created_at, tables_used, is_manual_sample
            FROM query_feedback
            WHERE feedback_id = :sample_id
            """)
            result = conn.execute(query, {"sample_id": sample_id}).fetchone()
            if not result:
                return None
            tables_list = result.tables_used.split(",") if result.tables_used else []
            return {
                "feedback_id": result.feedback_id,
                "query_text": result.query_text,
                "sql_query": result.sql_query,
                "results_summary": result.results_summary,
                "workspace": result.workspace,
                "feedback_rating": result.feedback_rating,
                "created_at": result.created_at,
                "tables_used": tables_list,
                "is_manual_sample": bool(result.is_manual_sample),
            }
    except SQLAlchemyError as e:
        manager.logger.error("Error getting sample by ID: %s", e, exc_info=True)
        return None


def update_sample(manager, sample_id: int, data: Dict[str, Any]) -> bool:
    """Update an existing sample."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database to update sample")
        return False

    try:
        tables_used = data.get("tables_used", [])
        tables_str = ",".join(tables_used) if tables_used else None

        with manager.engine.connect() as conn:
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
                    "sample_id": sample_id,
                    "query_text": data["query_text"],
                    "sql_query": data["sql_query"],
                    "results_summary": data.get("results_summary", ""),
                    "workspace": data.get("workspace", "Default"),
                    "feedback_rating": data.get("feedback_rating", 1),
                    "tables_used": tables_str,
                    "is_manual_sample": data.get("is_manual_sample", True),
                })
                trans.commit()
            except Exception as e:
                trans.rollback()
                raise e

        embedding_vector = manager._generate_embedding(data["query_text"])
        if embedding_vector is not None:
            metadata = {
                "sql_query": data["sql_query"],
                "results_summary": data.get("results_summary", ""),
                "workspace": data.get("workspace", "Default"),
                "feedback_rating": data.get("feedback_rating", 1),
                "tables_used": tables_used,
                "is_manual_sample": data.get("is_manual_sample", True),
                "created_at": data.get("created_at", time.strftime("%Y-%m-%d %H:%M:%S")),
            }
            manager.vector_store.update_embedding(
                collection_name=manager.collection_name,
                feedback_id=sample_id,
                vector=embedding_vector.tolist(),
                query_text=data["query_text"],
                metadata=metadata,
            )

        manager.logger.info("Updated sample ID %s", sample_id)
        return True
    except SQLAlchemyError as e:
        manager.logger.error("Error updating sample: %s", e, exc_info=True)
        return False


def delete_sample(manager, sample_id: int) -> bool:
    """Delete a sample from SQL and the vector store."""
    if not manager.engine and not manager.connect():
        manager.logger.error("Failed to connect to database to delete sample")
        return False

    try:
        with manager.engine.connect() as conn:
            trans = conn.begin()
            try:
                query = text("DELETE FROM query_feedback WHERE feedback_id = :sample_id")
                conn.execute(query, {"sample_id": sample_id})
                trans.commit()
            except Exception as e:
                trans.rollback()
                raise e

        manager.vector_store.delete_embedding(
            collection_name=manager.collection_name,
            feedback_id=sample_id,
        )
        manager.logger.info("Deleted sample ID %s", sample_id)
        return True
    except SQLAlchemyError as e:
        manager.logger.error("Error deleting sample: %s", e, exc_info=True)
        return False


def close_feedback(manager):
    """Close database and vector store connections."""
    manager.logger.info("Closing database connections")
    if manager.engine:
        manager.engine.dispose()
        manager.logger.debug("Database engine disposed")
    manager.vector_store.close()
