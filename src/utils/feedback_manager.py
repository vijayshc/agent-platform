"""Feedback Manager facade for Text2SQL application.

Delegates persistence and query search to modular packages:
- Persistence & CRUD: ``src.utils.feedback.feedback_store``
- Semantic search & reranking: ``src.utils.feedback.feedback_search``
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from config.config import DATABASE_URI
from src.utils.feedback.feedback_search import (
    find_similar_queries as _find_similar_queries,
    find_similar_queries_text_based as _find_similar_queries_text_based_fn,
    find_similar_queries_with_reranking as _find_similar_queries_with_reranking,
    generate_embedding as _generate_embedding_fn,
    get_reranking_model as _get_reranking_model_fn,
    migrate_existing_embeddings as _migrate_existing_embeddings,
)
from src.utils.feedback.feedback_store import (
    close_feedback as _close_feedback,
    connect_feedback_db as _connect_feedback_db,
    delete_sample as _delete_sample,
    get_feedback_stats as _get_feedback_stats,
    get_sample_by_id as _get_sample_by_id,
    get_samples as _get_samples,
    save_feedback as _save_feedback,
    update_sample as _update_sample,
)
from src.utils.vector_store import VectorStore


class FeedbackManager:
    """Manager class for handling user feedback on SQL queries."""

    def __init__(self, connection_string=None, vector_uri=None):
        self.logger = logging.getLogger("text2sql.feedback")
        self.connection_string = connection_string or DATABASE_URI
        self.logger.info("Feedback manager initialized with connection: %s", self.connection_string)
        self.engine = None
        self.embedding_model = None
        self.vector_store = VectorStore(uri=vector_uri)
        self.collection_name = "query_embeddings"

    def connect(self) -> bool:
        return _connect_feedback_db(self)

    def _generate_embedding(self, text: str) -> Optional[np.ndarray]:
        return _generate_embedding_fn(self, text)

    def _get_reranking_model(self):
        return _get_reranking_model_fn(self)

    def save_feedback(
        self,
        query_text: str,
        sql_query: str,
        results_summary: str,
        workspace: str,
        feedback_rating: int,
        tables_used: List[str],
        is_manual_sample: bool = False,
    ) -> bool:
        return _save_feedback(
            self, query_text, sql_query, results_summary, workspace,
            feedback_rating, tables_used, is_manual_sample
        )

    def find_similar_queries(self, query_text: str, limit: int = 1, positive_only: bool = True) -> List[Dict[str, Any]]:
        return _find_similar_queries(self, query_text, limit, positive_only)

    def find_similar_queries_with_reranking(self, query_text: str, limit: int = 2, positive_only: bool = True) -> List[Dict[str, Any]]:
        return _find_similar_queries_with_reranking(self, query_text, limit, positive_only)

    def _find_similar_queries_text_based(self, query_text: str, limit: int = 1, positive_only: bool = True) -> List[Dict[str, Any]]:
        return _find_similar_queries_text_based_fn(self, query_text, limit, positive_only)

    def get_feedback_stats(self) -> Dict[str, Any]:
        return _get_feedback_stats(self)

    def get_samples(self, page: int = 1, limit: int = 10, search_query: str = None) -> Tuple[List[Dict[str, Any]], int]:
        return _get_samples(self, page, limit, search_query)

    def get_sample_by_id(self, sample_id: int) -> Optional[Dict[str, Any]]:
        return _get_sample_by_id(self, sample_id)

    def update_sample(self, sample_id: int, data: Dict[str, Any]) -> bool:
        return _update_sample(self, sample_id, data)

    def delete_sample(self, sample_id: int) -> bool:
        return _delete_sample(self, sample_id)

    def migrate_existing_embeddings(self):
        return _migrate_existing_embeddings(self)

    def close(self):
        _close_feedback(self)


__all__ = ["FeedbackManager"]