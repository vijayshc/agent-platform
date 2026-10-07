"""Vector database client facade for Text2SQL application."""

import logging
from typing import Any, Dict, List, Optional
from config.config import CHROMADB_SERVICE_URL
from .vector_store_client import VectorStoreClient

logger = logging.getLogger("text2sql.vector")


class VectorStore:
    """Vector database facade delegating to VectorStoreClient."""

    def __init__(self, uri=None):
        self.logger = logging.getLogger("text2sql.vector")
        service_url = uri or CHROMADB_SERVICE_URL
        self.client = VectorStoreClient(service_url=service_url)
        self.default_vector_dim = 384

    def connect(self) -> bool:
        return self.client.connect()

    def count(self, collection_name: str) -> int:
        return self.client.count(collection_name)

    def list_entries(self, collection_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.client.list_entries(collection_name, limit)

    def list_entries_filtered(self, collection_name: str, filter_expr: Dict[str, Any] = None, limit: int = 100) -> List[Dict[str, Any]]:
        return self.client.list_entries_filtered(collection_name, filter_expr, limit)

    def sample_metadata(self, collection_name: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self.client.sample_metadata(collection_name, limit)

    def init_collection(self, collection_name: str, dimension: int = None) -> bool:
        return self.client.init_collection(collection_name, dimension)

    def delete_collection(self, collection_name: str) -> bool:
        return self.client.delete_collection(collection_name)

    def insert_embedding(self, collection_name: str, feedback_id: int, vector: List[float], query_text: str, metadata: Dict[str, Any] = None) -> bool:
        return self.client.insert_embedding(collection_name, feedback_id, vector, query_text, metadata)

    def search_similar(self, collection_name: str, vector: List[float], limit: int = 5, filter_expr: Optional[Dict[str, Any]] = None, output_fields: List[str] = None) -> List[Dict[str, Any]]:
        return self.client.search_similar(collection_name, vector, limit, filter_expr, output_fields)

    def query(self, collection_name: str, query_vector: List[float] = None, query_text: str = None, limit: int = 10, filter_expr: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self.client.query(collection_name, query_vector, query_text, limit, filter_expr)

    def update_embedding(self, collection_name: str, feedback_id: int, vector: List[float], query_text: str, metadata: Dict[str, Any] = None) -> bool:
        return self.client.update_embedding(collection_name, feedback_id, vector, query_text, metadata)

    def delete_embedding(self, collection_name: str, feedback_id: int) -> bool:
        return self.client.delete_embedding(collection_name, feedback_id)

    def delete_by_filter(self, collection_name: str, filter_expr: Dict[str, Any]) -> bool:
        return self.client.delete_by_filter(collection_name, filter_expr)

    def query_by_filter(self, collection_name: str, filter_expr: Dict[str, Any], limit: int = 100, output_fields: List[str] = None) -> List[Dict[str, Any]]:
        return self.client.query_by_filter(collection_name, filter_expr, limit, output_fields)

    def close(self):
        self.client.close()

    def search_by_text(self, collection_name: str, query_text: str, limit: int = 5, filter_expr: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self.client.search_by_text(collection_name, query_text, limit, filter_expr)

    def list_collections(self) -> List[str]:
        return self.client.list_collections()

    def list_collections_detailed(self) -> List[Dict[str, Any]]:
        return self.client.list_collections_detailed()

    def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        return self.client.get_collection_metadata(collection_name)
