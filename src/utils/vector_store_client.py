"""HTTP-based Vector database client for Text2SQL application.

Handles storage and retrieval of vector embeddings using ChromaDB service via REST API.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("text2sql.vector_client")


class VectorStoreClient:
    """HTTP client for ChromaDB service that maintains compatibility with the VectorStore interface."""

    def __init__(self, service_url=None):
        """Initialize the vector database HTTP client."""
        self.logger = logging.getLogger("text2sql.vector_client")
        self.service_url = service_url or "http://localhost:8001"
        self.client = None
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def connect(self) -> bool:
        """Test connection to ChromaDB service."""
        start_time = time.time()
        self.logger.info("Connecting to ChromaDB service at %s", self.service_url)
        try:
            response = self.session.get(f"{self.service_url}/health", timeout=10)
            if response.status_code == 200:
                health_data = response.json()
                if health_data.get("status") == "healthy":
                    self.client = True
                    self.logger.info("ChromaDB service connection established in %.2fs", time.time() - start_time)
                    return True
            self.logger.error("ChromaDB service health check failed: %s", response.status_code)
            return False
        except Exception as e:
            self.logger.error("ChromaDB service connection error: %s", e, exc_info=True)
            return False

    def count(self, collection_name: str) -> int:
        """Count documents in a collection."""
        try:
            response = self.session.get(f"{self.service_url}/collections/{collection_name}")
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("collection", {}).get("count", 0)
            return 0
        except Exception as e:
            self.logger.error("Error counting documents in collection %s: %s", collection_name, e)
            return 0

    def list_entries(self, collection_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List entries in a collection with metadata."""
        return self.list_entries_filtered(collection_name, None, limit)

    def list_entries_filtered(
        self, collection_name: str, filter_expr: Optional[Dict[str, Any]] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List entries in a collection, optionally restricted by a where clause."""
        try:
            params: Dict[str, Any] = {"limit": limit}
            if filter_expr:
                params["where"] = json.dumps(filter_expr)

            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents",
                params=params,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return [
                        {
                            "id": doc.get("id"),
                            "text": doc.get("document", ""),
                            "metadata": doc.get("metadata", {}),
                        }
                        for doc in data.get("documents", [])
                    ]
            return []
        except Exception as e:
            self.logger.error("Error listing entries in collection %s: %s", collection_name, e)
            return []

    def sample_metadata(self, collection_name: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Read metadata only for a bounded sample of a collection."""
        try:
            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents",
                params={"limit": limit, "include": "metadatas"},
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return [
                        {"id": doc.get("id"), "metadata": doc.get("metadata") or {}}
                        for doc in data.get("documents", [])
                    ]
            return []
        except Exception as e:
            self.logger.error("Error sampling metadata in collection %s: %s", collection_name, e)
            return []

    def init_collection(self, collection_name: str, dimension: int = None) -> bool:
        """Initialize a vector collection if it doesn't exist."""
        try:
            response = self.session.get(f"{self.service_url}/collections/{collection_name}")
            if response.status_code == 200:
                self.logger.info("Collection '%s' already exists", collection_name)
                return True

            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}",
                json={"metadata": {"created_by": "text2sql_app"}},
            )
            if response.status_code == 200:
                self.logger.info("Collection '%s' created successfully", collection_name)
                return True
            self.logger.error("Failed to create collection %s: %s", collection_name, response.status_code)
            return False
        except Exception as e:
            self.logger.error("Error initializing collection %s: %s", collection_name, e, exc_info=True)
            return False

    def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection."""
        try:
            response = self.session.delete(f"{self.service_url}/collections/{collection_name}")
            if response.status_code == 200:
                self.logger.info("Deleted collection %s", collection_name)
                return True
            self.logger.error("Failed to delete collection: %s", response.status_code)
            return False
        except Exception as e:
            self.logger.error("Error deleting collection %s: %s", collection_name, e, exc_info=True)
            return False

    def insert_embedding(
        self,
        collection_name: str,
        feedback_id: int,
        vector: List[float],
        query_text: str,
        metadata: Dict[str, Any] = None,
    ) -> bool:
        """Insert a vector embedding into the database."""
        try:
            doc_data: Dict[str, Any] = {
                "documents": [query_text],
                "ids": [str(feedback_id)],
            }
            if metadata:
                clean_metadata = {}
                for k, v in metadata.items():
                    if isinstance(v, list):
                        clean_metadata[k] = ",".join(str(x) for x in v)
                    elif v is not None:
                        clean_metadata[k] = str(v)
                clean_metadata["query_text"] = query_text
                doc_data["metadatas"] = [clean_metadata]

            if vector and len(vector) > 0:
                doc_data["embeddings"] = [vector]

            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}/documents",
                json=doc_data,
            )
            if response.status_code == 200:
                self.logger.info("Inserted embedding for feedback_id %s into collection %s", feedback_id, collection_name)
                return True
            self.logger.error("Failed to insert embedding: %s - %s", response.status_code, response.text)
            return False
        except Exception as e:
            self.logger.error("Error inserting embedding into collection %s: %s", collection_name, e, exc_info=True)
            return False

    @staticmethod
    def _format_search_result(result: Dict[str, Any]) -> Dict[str, Any]:
        """Format an individual search hit into standard format."""
        formatted = {
            "id": result.get("id"),
            "query_text": result.get("document", ""),
            "text": result.get("document", ""),
            "similarity": result.get("similarity", 0),
            "distance": result.get("distance", 0),
        }
        metadata = result.get("metadata", {}) or {}
        for key, value in metadata.items():
            if isinstance(value, str) and "," in value and key.endswith("_used"):
                formatted[key] = value.split(",")
            else:
                formatted[key] = value
        return formatted

    def search_similar(
        self,
        collection_name: str,
        vector: List[float],
        limit: int = 5,
        filter_expr: Optional[Dict[str, Any]] = None,
        output_fields: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors."""
        try:
            search_data: Dict[str, Any] = {
                "query_embeddings": [vector],
                "n_results": limit,
            }
            if filter_expr:
                search_data["where"] = filter_expr

            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}/search",
                json=search_data,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return [self._format_search_result(r) for r in data.get("results", [])]
            return []
        except Exception as e:
            self.logger.error("Error searching similar vectors in collection %s: %s", collection_name, e, exc_info=True)
            return []

    def query(
        self,
        collection_name: str,
        query_vector: List[float] = None,
        query_text: str = None,
        limit: int = 10,
        filter_expr: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Run a similarity search and return structured hits."""
        search_data: Dict[str, Any] = {"n_results": limit}
        if query_vector:
            search_data["query_embeddings"] = [query_vector]
        elif query_text:
            search_data["query_texts"] = [query_text]
        else:
            raise ValueError("query() requires query_vector or query_text")
        if filter_expr:
            search_data["where"] = filter_expr

        response = self.session.post(
            f"{self.service_url}/collections/{collection_name}/search",
            json=search_data,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Vector search failed ({response.status_code}): {response.text[:200]}")

        data = response.json()
        if not data.get("success"):
            raise RuntimeError(data.get("error") or "Vector search failed")

        return [
            {
                "id": result.get("id"),
                "text": result.get("document", "") or "",
                "metadata": result.get("metadata", {}) or {},
                "distance": result.get("distance"),
                "similarity": result.get("similarity"),
            }
            for result in data.get("results", [])
        ]

    def update_embedding(
        self,
        collection_name: str,
        feedback_id: int,
        vector: List[float],
        query_text: str,
        metadata: Dict[str, Any] = None,
    ) -> bool:
        """Update an existing vector embedding."""
        try:
            update_data: Dict[str, Any] = {"document": query_text}
            if metadata:
                clean_metadata = {}
                for k, v in metadata.items():
                    if isinstance(v, list):
                        clean_metadata[k] = ",".join(str(x) for x in v)
                    elif v is not None:
                        clean_metadata[k] = str(v)
                clean_metadata["query_text"] = query_text
                update_data["metadata"] = clean_metadata

            if vector and len(vector) > 0:
                update_data["embedding"] = vector

            response = self.session.put(
                f"{self.service_url}/collections/{collection_name}/documents/{feedback_id}",
                json=update_data,
            )
            if response.status_code == 200:
                self.logger.info("Updated embedding for feedback_id %s in collection %s", feedback_id, collection_name)
                return True
            self.logger.error("Failed to update embedding: %s", response.status_code)
            return False
        except Exception as e:
            self.logger.error("Error updating embedding in collection %s: %s", collection_name, e, exc_info=True)
            return False

    def delete_embedding(self, collection_name: str, feedback_id: int) -> bool:
        """Delete a vector embedding from the database."""
        try:
            response = self.session.delete(
                f"{self.service_url}/collections/{collection_name}/documents/{feedback_id}"
            )
            if response.status_code == 200:
                self.logger.info("Deleted embedding for feedback_id %s from collection %s", feedback_id, collection_name)
                return True
            self.logger.error("Failed to delete embedding: %s", response.status_code)
            return False
        except Exception as e:
            self.logger.error("Error deleting embedding from collection %s: %s", collection_name, e, exc_info=True)
            return False

    def delete_by_filter(self, collection_name: str, filter_expr: Dict[str, Any]) -> bool:
        """Delete documents from a collection by filter expression."""
        try:
            params = {}
            if filter_expr:
                params["where"] = json.dumps(filter_expr)

            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents",
                params=params,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    documents = data.get("documents", [])
                    deleted_count = 0
                    for doc in documents:
                        doc_id = doc.get("id")
                        if doc_id and self.delete_embedding(collection_name, doc_id):
                            deleted_count += 1
                    self.logger.info(
                        "Deleted %d documents from collection %s with filter: %s",
                        deleted_count, collection_name, filter_expr
                    )
                    return deleted_count > 0
            return False
        except Exception as e:
            self.logger.error("Error deleting by filter from collection %s: %s", collection_name, e, exc_info=True)
            return False

    def query_by_filter(
        self,
        collection_name: str,
        filter_expr: Dict[str, Any],
        limit: int = 100,
        output_fields: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query entries by filter expression."""
        try:
            params: Dict[str, Any] = {"limit": limit}
            if filter_expr:
                params["where"] = json.dumps(filter_expr)

            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents",
                params=params,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    results = []
                    for doc in data.get("documents", []):
                        result = {
                            "id": doc.get("id"),
                            "query_text": doc.get("document", ""),
                            "text": doc.get("document", ""),
                        }
                        metadata = doc.get("metadata", {}) or {}
                        for key, value in metadata.items():
                            if isinstance(value, str) and "," in value and key.endswith("_used"):
                                result[key] = value.split(",")
                            else:
                                result[key] = value
                        results.append(result)
                    return results
            return []
        except Exception as e:
            self.logger.error("Error querying by filter in collection %s: %s", collection_name, e, exc_info=True)
            return []

    def close(self):
        """Close the connection to the vector database."""
        self.logger.info("Closing ChromaDB service connection")
        self.session.close()
        self.client = None

    def search_by_text(
        self,
        collection_name: str,
        query_text: str,
        limit: int = 5,
        filter_expr: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors using text query."""
        try:
            search_data: Dict[str, Any] = {
                "query_texts": [query_text],
                "n_results": limit,
            }
            if filter_expr:
                search_data["where"] = filter_expr

            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}/search",
                json=search_data,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return [self._format_search_result(r) for r in data.get("results", [])]
            return []
        except Exception as e:
            self.logger.error("Error searching by text in collection %s: %s", collection_name, e, exc_info=True)
            return []

    def list_collections(self) -> List[str]:
        """List all collection names."""
        return [item["name"] for item in self._fetch_collections(include_counts=False)]

    def list_collections_detailed(self) -> List[Dict[str, Any]]:
        """List collections with their document counts and metadata in one call."""
        return self._fetch_collections(include_counts=True)

    def _fetch_collections(self, include_counts: bool) -> List[Dict[str, Any]]:
        """Read the collection list from the service."""
        try:
            params = {} if include_counts else {"counts": "false"}
            response = self.session.get(f"{self.service_url}/collections", params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    collections = []
                    for item in data.get("collections", []):
                        name = item.get("name")
                        if not name:
                            continue
                        collections.append({
                            "name": name,
                            "count": item.get("count", 0),
                            "metadata": item.get("metadata") or {},
                        })
                    if include_counts:
                        self.logger.info("Found collections: %s", [c["name"] for c in collections])
                    return collections
            return []
        except Exception as e:
            self.logger.error("Error listing collections: %s", e)
            return []

    def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        """Get metadata for a collection."""
        try:
            response = self.session.get(f"{self.service_url}/collections/{collection_name}")
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    collection = data.get("collection", {})
                    return {
                        "name": collection.get("name", collection_name),
                        "count": collection.get("count", 0),
                        "metadata": collection.get("metadata", {}),
                    }
            return {}
        except Exception as e:
            self.logger.error("Error getting collection metadata for %s: %s", collection_name, e)
            return {}


VectorStore = VectorStoreClient
