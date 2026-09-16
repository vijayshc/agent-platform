"""
HTTP-based Vector database client for Text2SQL application.
Handles storage and retrieval of vector embeddings using ChromaDB service via REST API.
"""

import logging
import time
import requests
import json
from typing import List, Dict, Any, Optional

logger = logging.getLogger('text2sql.vector_client')

class VectorStoreClient:
    """HTTP client for ChromaDB service that maintains compatibility with the original VectorStore interface"""
    
    def __init__(self, service_url=None):
        """Initialize the vector database HTTP client
        
        Args:
            service_url (str, optional): URL of the ChromaDB service, defaults to http://localhost:8001
        """
        self.logger = logging.getLogger('text2sql.vector_client')
        self.service_url = service_url or "http://localhost:8001"
        self.client = None  # For compatibility with existing code
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        
    def connect(self) -> bool:
        """Test connection to ChromaDB service
        
        Returns:
            bool: True if successful, False otherwise
        """
        start_time = time.time()
        self.logger.info(f"Connecting to ChromaDB service at {self.service_url}")
        
        try:
            response = self.session.get(f"{self.service_url}/health", timeout=10)
            if response.status_code == 200:
                health_data = response.json()
                if health_data.get('status') == 'healthy':
                    self.client = True  # Set for compatibility
                    self.logger.info(f"ChromaDB service connection established in {time.time() - start_time:.2f}s")
                    return True
            
            self.logger.error(f"ChromaDB service health check failed: {response.status_code}")
            return False
        except Exception as e:
            self.logger.error(f"ChromaDB service connection error: {str(e)}", exc_info=True)
            return False
    
    def count(self, collection_name: str) -> int:
        """Count documents in a collection
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            int: Number of documents in the collection
        """
        try:
            response = self.session.get(f"{self.service_url}/collections/{collection_name}")
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return data.get('collection', {}).get('count', 0)
            return 0
        except Exception as e:
            self.logger.error(f"Error counting documents in collection {collection_name}: {str(e)}")
            return 0
            
    def list_entries(self, collection_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List entries in a collection with metadata
        
        Args:
            collection_name: Name of the collection
            limit: Maximum number of entries to return
            
        Returns:
            List[Dict]: List of entries with metadata
        """
        return self.list_entries_filtered(collection_name, None, limit)

    def list_entries_filtered(self, collection_name: str, filter_expr: Optional[Dict[str, Any]] = None,
                              limit: int = 100) -> List[Dict[str, Any]]:
        """List entries in a collection, optionally restricted by a where clause

        Args:
            collection_name: Name of the collection
            filter_expr (Optional[Dict], optional): ChromaDB where clause
            limit: Maximum number of entries to return

        Returns:
            List[Dict]: List of ``{id, text, metadata}`` entries
        """
        try:
            params = {'limit': limit}
            if filter_expr:
                params['where'] = json.dumps(filter_expr)

            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents", 
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    entries = []
                    for doc in data.get('documents', []):
                        entry = {
                            'id': doc.get('id'),
                            'text': doc.get('document', ''),
                            'metadata': doc.get('metadata', {})
                        }
                        entries.append(entry)
                    return entries
            return []
        except Exception as e:
            self.logger.error(f"Error listing entries in collection {collection_name}: {str(e)}")
            return []
    
    def sample_metadata(self, collection_name: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Read metadata only for a bounded sample of a collection.

        Used to discover the filter columns: it must stay cheap no matter how
        many vectors the collection holds, so no document text is transferred.

        Args:
            collection_name: Name of the collection
            limit: Maximum number of records to sample

        Returns:
            List[Dict]: ``{id, metadata}`` per sampled record
        """
        try:
            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents",
                params={'limit': limit, 'include': 'metadatas'}
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    return [
                        {'id': doc.get('id'), 'metadata': doc.get('metadata') or {}}
                        for doc in data.get('documents', [])
                    ]
            return []
        except Exception as e:
            self.logger.error(f"Error sampling metadata in collection {collection_name}: {str(e)}")
            return []

    def init_collection(self, collection_name: str, dimension: int = None) -> bool:
        """Initialize a vector collection if it doesn't exist
        
        Args:
            collection_name (str): Name of the collection to initialize
            dimension (int, optional): Vector dimension (not used, kept for compatibility)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Check if collection already exists
            response = self.session.get(f"{self.service_url}/collections/{collection_name}")
            if response.status_code == 200:
                self.logger.info(f"Collection '{collection_name}' already exists")
                return True
            
            # Create the collection
            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}",
                json={"metadata": {"created_by": "text2sql_app"}}
            )
            
            if response.status_code == 200:
                self.logger.info(f"Collection '{collection_name}' created successfully")
                return True
            else:
                self.logger.error(f"Failed to create collection {collection_name}: {response.status_code}")
                return False
        except Exception as e:
            self.logger.error(f"Error initializing collection {collection_name}: {str(e)}", exc_info=True)
            return False
    
    def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection
        
        Args:
            collection_name (str): Name of the collection to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            response = self.session.delete(f"{self.service_url}/collections/{collection_name}")
            
            if response.status_code == 200:
                self.logger.info(f"Deleted collection {collection_name}")
                return True
            else:
                self.logger.error(f"Failed to delete collection: {response.status_code}")
                return False
        except Exception as e:
            self.logger.error(f"Error deleting collection {collection_name}: {str(e)}", exc_info=True)
            return False

    def insert_embedding(self, collection_name: str, feedback_id: int, vector: List[float], 
                        query_text: str, metadata: Dict[str, Any] = None) -> bool:
        """Insert a vector embedding into the database
        
        Args:
            collection_name (str): Name of the collection to insert into
            feedback_id (int): ID of the feedback entry (primary key)
            vector (List[float]): Vector embedding to store (optional)
            query_text (str): The query text associated with the embedding
            metadata (Dict): Additional metadata to store with the vector
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Prepare the document data
            doc_data = {
                "documents": [query_text],
                "ids": [str(feedback_id)]
            }
            
            # Add metadata if provided
            if metadata:
                clean_metadata = {}
                for k, v in metadata.items():
                    if isinstance(v, list):
                        clean_metadata[k] = ','.join(str(x) for x in v)
                    elif v is not None:
                        clean_metadata[k] = str(v)
                clean_metadata['query_text'] = query_text
                doc_data["metadatas"] = [clean_metadata]
            
            # Add embedding if provided
            if vector and len(vector) > 0:
                doc_data["embeddings"] = [vector]
            
            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}/documents",
                json=doc_data
            )
            
            if response.status_code == 200:
                self.logger.info(f"Inserted embedding for feedback_id {feedback_id} into collection {collection_name}")
                return True
            else:
                self.logger.error(f"Failed to insert embedding: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            self.logger.error(f"Error inserting embedding into collection {collection_name}: {str(e)}", exc_info=True)
            return False
    
    def search_similar(self, collection_name: str, vector: List[float], limit: int = 5, 
                       filter_expr: Optional[Dict[str, Any]] = None, output_fields: List[str] = None) -> List[Dict[str, Any]]:
        """Search for similar vectors
        
        Args:
            collection_name (str): Name of the collection to search in
            vector (List[float]): Query vector to search with
            limit (int): Maximum number of results to return
            filter_expr (Dict[str, Any], optional): ChromaDB where clause for filtering
            output_fields (List[str], optional): Specific fields to return (kept for compatibility)
            
        Returns:
            List[Dict]: List of search results with similarity scores
        """
        try:
            # Prepare search data
            search_data = {
                "query_embeddings": [vector],
                "n_results": limit
            }
            
            # Use filter expression directly as where clause
            if filter_expr:
                search_data["where"] = filter_expr
            
            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}/search",
                json=search_data
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    results = []
                    for result in data.get('results', []):
                        # Format result to match original interface
                        formatted_result = {
                            'id': result.get('id'),
                            'query_text': result.get('document', ''),
                            'text': result.get('document', ''),
                            'similarity': result.get('similarity', 0),
                            'distance': result.get('distance', 0)
                        }
                        
                        # Add metadata fields
                        metadata = result.get('metadata', {})
                        for key, value in metadata.items():
                            if isinstance(value, str) and ',' in value and key.endswith('_used'):
                                formatted_result[key] = value.split(',')
                            else:
                                formatted_result[key] = value
                        
                        results.append(formatted_result)
                    return results
            return []
        except Exception as e:
            self.logger.error(f"Error searching similar vectors in collection {collection_name}: {str(e)}", exc_info=True)
            return []
    
    def query(self, collection_name: str, query_vector: List[float] = None, query_text: str = None,
              limit: int = 10, filter_expr: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Run a similarity search and return structured hits.

        Prefer ``query_vector``: the embedding must come from the same model that
        produced the stored vectors, otherwise the distances are meaningless.

        Args:
            collection_name: Collection to search
            query_vector: Query embedding
            query_text: Query text (embedded by the service's own model)
            limit: Maximum number of hits
            filter_expr: ChromaDB where clause restricting the search

        Returns:
            List[Dict]: ``{id, text, metadata, distance, similarity}`` per hit
        """
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
            json=search_data
        )
        if response.status_code != 200:
            raise RuntimeError(f"Vector search failed ({response.status_code}): {response.text[:200]}")

        data = response.json()
        if not data.get('success'):
            raise RuntimeError(data.get('error') or 'Vector search failed')

        hits = []
        for result in data.get('results', []):
            hits.append({
                'id': result.get('id'),
                'text': result.get('document', '') or '',
                'metadata': result.get('metadata', {}) or {},
                'distance': result.get('distance'),
                'similarity': result.get('similarity'),
            })
        return hits

    def update_embedding(self, collection_name: str, feedback_id: int, vector: List[float], 
                        query_text: str, metadata: Dict[str, Any] = None) -> bool:
        """Update an existing vector embedding
        
        Args:
            collection_name (str): Name of the collection to update in
            feedback_id (int): ID of the feedback entry to update
            vector (List[float]): New vector embedding
            query_text (str): Updated query text
            metadata (Dict): Updated metadata
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Prepare update data
            update_data = {
                "document": query_text
            }
            
            if metadata:
                clean_metadata = {}
                for k, v in metadata.items():
                    if isinstance(v, list):
                        clean_metadata[k] = ','.join(str(x) for x in v)
                    elif v is not None:
                        clean_metadata[k] = str(v)
                clean_metadata['query_text'] = query_text
                update_data["metadata"] = clean_metadata
            
            if vector and len(vector) > 0:
                update_data["embedding"] = vector
            
            response = self.session.put(
                f"{self.service_url}/collections/{collection_name}/documents/{feedback_id}",
                json=update_data
            )
            
            if response.status_code == 200:
                self.logger.info(f"Updated embedding for feedback_id {feedback_id} in collection {collection_name}")
                return True
            else:
                self.logger.error(f"Failed to update embedding: {response.status_code}")
                return False
        except Exception as e:
            self.logger.error(f"Error updating embedding in collection {collection_name}: {str(e)}", exc_info=True)
            return False
    
    def delete_embedding(self, collection_name: str, feedback_id: int) -> bool:
        """Delete a vector embedding from the database
        
        Args:
            collection_name (str): Name of the collection to delete from
            feedback_id (int): ID of the feedback entry to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            response = self.session.delete(
                f"{self.service_url}/collections/{collection_name}/documents/{feedback_id}"
            )
            
            if response.status_code == 200:
                self.logger.info(f"Deleted embedding for feedback_id {feedback_id} from collection {collection_name}")
                return True
            else:
                self.logger.error(f"Failed to delete embedding: {response.status_code}")
                return False
        except Exception as e:
            self.logger.error(f"Error deleting embedding from collection {collection_name}: {str(e)}", exc_info=True)
            return False
    
    def delete_by_filter(self, collection_name: str, filter_expr: Dict[str, Any]) -> bool:
        """Delete documents from a collection by filter expression
        
        Args:
            collection_name (str): Name of the collection to delete from
            filter_expr (Dict[str, Any]): ChromaDB where clause to identify documents to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # First, get the documents matching the filter
            params = {}
            if filter_expr:
                params['where'] = json.dumps(filter_expr)
            
            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents",
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    documents = data.get('documents', [])
                    # Delete each document by ID
                    deleted_count = 0
                    for doc in documents:
                        doc_id = doc.get('id')
                        if doc_id:
                            if self.delete_embedding(collection_name, doc_id):
                                deleted_count += 1
                    
                    self.logger.info(f"Deleted {deleted_count} documents from collection {collection_name} with filter: {filter_expr}")
                    return deleted_count > 0
                        
            return False
        except Exception as e:
            self.logger.error(f"Error deleting by filter from collection {collection_name}: {str(e)}", exc_info=True)
            return False

    def query_by_filter(self, collection_name: str, filter_expr: Dict[str, Any], limit: int = 100,
                         output_fields: List[str] = None) -> List[Dict[str, Any]]:
        """Query entries by filter expression
        
        Args:
            collection_name (str): Name of the collection to query
            filter_expr (Dict[str, Any]): ChromaDB where clause for filtering
            limit (int): Maximum number of results to return
            output_fields (List[str], optional): Specific fields to return (kept for compatibility)
            
        Returns:
            List[Dict]: List of query results
        """
        try:
            params = {'limit': limit}
            
            # Use filter expression directly as where clause
            if filter_expr:
                params['where'] = json.dumps(filter_expr)
            
            response = self.session.get(
                f"{self.service_url}/collections/{collection_name}/documents",
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    results = []
                    for doc in data.get('documents', []):
                        result = {
                            'id': doc.get('id'),
                            'query_text': doc.get('document', ''),
                            'text': doc.get('document', ''),
                        }
                        
                        # Add metadata fields
                        metadata = doc.get('metadata', {})
                        for key, value in metadata.items():
                            if isinstance(value, str) and ',' in value and key.endswith('_used'):
                                result[key] = value.split(',')
                            else:
                                result[key] = value
                        
                        results.append(result)
                    return results
            return []
        except Exception as e:
            self.logger.error(f"Error querying by filter in collection {collection_name}: {str(e)}", exc_info=True)
            return []
    
    def close(self):
        """Close the connection to the vector database"""
        self.logger.info("Closing ChromaDB service connection")
        self.session.close()
        self.client = None

    def search_by_text(self, collection_name: str, query_text: str, limit: int = 5, 
                       filter_expr: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Search for similar vectors using text query
        
        Args:
            collection_name (str): Name of the collection to search in
            query_text (str): Text query to search with
            limit (int): Maximum number of results to return
            filter_expr (Dict[str, Any], optional): ChromaDB where clause for filtering
            
        Returns:
            List[Dict]: List of search results with similarity scores
        """
        try:
            # Prepare search data
            search_data = {
                "query_texts": [query_text],
                "n_results": limit
            }
            
            # Use filter expression directly as where clause
            if filter_expr:
                search_data["where"] = filter_expr
            
            response = self.session.post(
                f"{self.service_url}/collections/{collection_name}/search",
                json=search_data
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    results = []
                    for result in data.get('results', []):
                        formatted_result = {
                            'id': result.get('id'),
                            'query_text': result.get('document', ''),
                            'text': result.get('document', ''),
                            'similarity': result.get('similarity', 0),
                            'distance': result.get('distance', 0)
                        }
                        
                        # Add metadata fields
                        metadata = result.get('metadata', {})
                        for key, value in metadata.items():
                            if isinstance(value, str) and ',' in value and key.endswith('_used'):
                                formatted_result[key] = value.split(',')
                            else:
                                formatted_result[key] = value
                        
                        results.append(formatted_result)
                    return results
            return []
        except Exception as e:
            self.logger.error(f"Error searching by text in collection {collection_name}: {str(e)}", exc_info=True)
            return []
    
    def list_collections(self) -> List[str]:
        """List all collection names.

        Asks the service to skip the per-collection document count: this is the
        hot path for existence checks and must stay cheap with many collections.

        Returns:
            List[str]: List of collection names
        """
        return [item['name'] for item in self._fetch_collections(include_counts=False)]

    def list_collections_detailed(self) -> List[Dict[str, Any]]:
        """List collections with their document counts and metadata in one call.

        Returns:
            List[Dict]: ``{name, count, metadata}`` per collection
        """
        return self._fetch_collections(include_counts=True)

    def _fetch_collections(self, include_counts: bool) -> List[Dict[str, Any]]:
        """Read the collection list from the service.

        Args:
            include_counts: Ask for the per-collection document count

        Returns:
            List[Dict]: ``{name, count, metadata}`` per collection (count is 0
            when counts were not requested)
        """
        try:
            params = {} if include_counts else {'counts': 'false'}
            response = self.session.get(f"{self.service_url}/collections", params=params)

            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    collections = []
                    for item in data.get('collections', []):
                        name = item.get('name')
                        if not name:
                            continue
                        collections.append({
                            'name': name,
                            'count': item.get('count', 0),
                            'metadata': item.get('metadata') or {},
                        })
                    if include_counts:
                        self.logger.info(f"Found collections: {[c['name'] for c in collections]}")
                    return collections
            return []
        except Exception as e:
            self.logger.error(f"Error listing collections: {e}")
            return []

    def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        """Get metadata for a collection
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            Dict: Collection metadata
        """
        try:
            response = self.session.get(f"{self.service_url}/collections/{collection_name}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    collection = data.get('collection', {})
                    return {
                        'name': collection.get('name', collection_name),
                        'count': collection.get('count', 0),
                        'metadata': collection.get('metadata', {})
                    }
            return {}
        except Exception as e:
            self.logger.error(f"Error getting collection metadata for {collection_name}: {e}")
            return {}
    
    def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection
        
        Args:
            collection_name (str): Name of the collection to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            response = self.session.delete(f"{self.service_url}/collections/{collection_name}")
            
            if response.status_code == 200:
                self.logger.info(f"Deleted collection {collection_name}")
                return True
            else:
                self.logger.error(f"Failed to delete collection: {response.status_code}")
                return False
        except Exception as e:
            self.logger.error(f"Error deleting collection {collection_name}: {str(e)}", exc_info=True)
            return False


# For backward compatibility, create an alias
VectorStore = VectorStoreClient
