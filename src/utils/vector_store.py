"""
Vector database client for Text2SQL application.
Handles storage and retrieval of vector embeddings using ChromaDB REST API.
"""

import logging
from typing import List, Dict, Any, Optional
from .vector_store_client import VectorStoreClient
from config.config import CHROMADB_SERVICE_URL

logger = logging.getLogger('text2sql.vector')

class VectorStore:
    """Vector database client for storing and retrieving embeddings using ChromaDB REST API"""
    
    def __init__(self, uri=None):
        """Initialize the vector database client
        
        Args:
            uri (str, optional): ChromaDB service URL, defaults to config setting
        """
        self.logger = logging.getLogger('text2sql.vector')
        service_url = uri or CHROMADB_SERVICE_URL
        self.client = VectorStoreClient(service_url=service_url)
        # Default dimension for popular embedding models - can be overridden per collection
        self.default_vector_dim = 384
        
    def connect(self) -> bool:
        """Connect to ChromaDB service via REST API
        
        Returns:
            bool: True if successful, False otherwise
        """
        return self.client.connect()
    
    def count(self, collection_name: str) -> int:
        """Count documents in a collection
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            int: Number of documents in the collection
        """
        return self.client.count(collection_name)
            
    def list_entries(self, collection_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List entries in a collection with metadata
        
        Args:
            collection_name: Name of the collection
            limit: Maximum number of entries to return
            
        Returns:
            List[Dict]: List of entries with metadata
        """
        return self.client.list_entries(collection_name, limit)

    def list_entries_filtered(self, collection_name: str, filter_expr: Dict[str, Any] = None,
                              limit: int = 100) -> List[Dict[str, Any]]:
        """List entries in a collection, optionally restricted by a where clause

        Args:
            collection_name (str): Name of the collection
            filter_expr (Dict, optional): ChromaDB where clause
            limit (int): Maximum number of entries to return

        Returns:
            List[Dict]: List of ``{id, text, metadata}`` entries
        """
        return self.client.list_entries_filtered(collection_name, filter_expr, limit)

    def sample_metadata(self, collection_name: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Read metadata only for a bounded sample of a collection

        Args:
            collection_name (str): Name of the collection
            limit (int): Maximum number of records to sample

        Returns:
            List[Dict]: ``{id, metadata}`` per sampled record
        """
        return self.client.sample_metadata(collection_name, limit)
    
    def init_collection(self, collection_name: str, dimension: int = None) -> bool:
        """Initialize a vector collection if it doesn't exist
        
        Args:
            collection_name (str): Name of the collection to initialize
            dimension (int, optional): Vector dimension (not used, kept for compatibility)
            
        Returns:
            bool: True if successful, False otherwise
        """
        return self.client.init_collection(collection_name, dimension)
    
    def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection
        
        Args:
            collection_name (str): Name of the collection to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        return self.client.delete_collection(collection_name)

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
        return self.client.insert_embedding(collection_name, feedback_id, vector, query_text, metadata)
    
    def search_similar(self, collection_name: str, vector: List[float], limit: int = 5, 
                       filter_expr: Optional[Dict[str, Any]] = None, output_fields: List[str] = None) -> List[Dict[str, Any]]:
        """Search for similar vectors
        
        Args:
            collection_name (str): Name of the collection to search in
            vector (List[float]): Query vector to search with
            limit (int): Maximum number of results to return
            filter_expr (Optional[Dict], optional): ChromaDB filter dictionary
            output_fields (List[str], optional): Specific fields to return (kept for compatibility)
            
        Returns:
            List[Dict]: List of search results with similarity scores
        """
        return self.client.search_similar(collection_name, vector, limit, filter_expr, output_fields)
    
    def query(self, collection_name: str, query_vector: List[float] = None, query_text: str = None,
              limit: int = 10, filter_expr: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Similarity search returning structured hits

        Args:
            collection_name (str): Name of the collection to search
            query_vector (List[float], optional): Query embedding (same model as the stored vectors)
            query_text (str, optional): Query text, embedded by the vector service
            limit (int): Maximum number of results
            filter_expr (Optional[Dict], optional): ChromaDB where clause

        Returns:
            List[Dict]: ``{id, text, metadata, distance, similarity}`` per hit
        """
        return self.client.query(collection_name, query_vector, query_text, limit, filter_expr)

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
        return self.client.update_embedding(collection_name, feedback_id, vector, query_text, metadata)
    
    def delete_embedding(self, collection_name: str, feedback_id: int) -> bool:
        """Delete a vector embedding from the database
        
        Args:
            collection_name (str): Name of the collection to delete from
            feedback_id (int): ID of the feedback entry to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        return self.client.delete_embedding(collection_name, feedback_id)
    
    def delete_by_filter(self, collection_name: str, filter_expr: Dict[str, Any]) -> bool:
        """Delete documents from a collection by filter expression
        
        Args:
            collection_name (str): Name of the collection to delete from
            filter_expr (Dict): ChromaDB filter dictionary to identify documents to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        return self.client.delete_by_filter(collection_name, filter_expr)

    def query_by_filter(self, collection_name: str, filter_expr: Dict[str, Any], limit: int = 100, 
                         output_fields: List[str] = None) -> List[Dict[str, Any]]:
        """Query entries by filter expression
        
        Args:
            collection_name (str): Name of the collection to query
            filter_expr (Dict): ChromaDB filter dictionary for the query
            limit (int): Maximum number of results to return
            output_fields (List[str], optional): Specific fields to return (kept for compatibility)
            
        Returns:
            List[Dict]: List of query results
        """
        return self.client.query_by_filter(collection_name, filter_expr, limit, output_fields)
    
    def close(self):
        """Close the connection to the vector database"""
        self.client.close()

    def search_by_text(self, collection_name: str, query_text: str, limit: int = 5, 
                       filter_expr: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Search for similar vectors using text query
        
        Args:
            collection_name (str): Name of the collection to search in
            query_text (str): Text query to search with
            limit (int): Maximum number of results to return
            filter_expr (Optional[Dict], optional): ChromaDB filter dictionary
            
        Returns:
            List[Dict]: List of search results with similarity scores
        """
        return self.client.search_by_text(collection_name, query_text, limit, filter_expr)
    
    def list_collections(self) -> List[str]:
        """List all collections
        
        Returns:
            List[str]: List of collection names
        """
        return self.client.list_collections()

    def list_collections_detailed(self) -> List[Dict[str, Any]]:
        """List collections with document counts and metadata

        Returns:
            List[Dict]: ``{name, count, metadata}`` per collection
        """
        return self.client.list_collections_detailed()
    
    def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        """Get metadata for a collection
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            Dict: Collection metadata
        """
        return self.client.get_collection_metadata(collection_name)
