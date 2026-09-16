#!/usr/bin/env python3
"""
Standalone ChromaDB Service
A separate microservice that provides REST API endpoints for ChromaDB operations.
This service is decoupled from the main Text2SQL application.
"""

import os
import logging
import time
from typing import List, Dict, Any, Optional
from flask import Flask, request, jsonify
from flask_cors import CORS
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('chromadb_service')

app = Flask(__name__)
CORS(app)  # Enable CORS for cross-origin requests

class ChromaDBService:
    """ChromaDB service handler"""
    
    def __init__(self, persist_directory="./chroma_data"):
        """Initialize ChromaDB service
        
        Args:
            persist_directory: Directory to store ChromaDB data
        """
        self.persist_directory = persist_directory
        self.client = None
        self.embedding_function = None
        self.logger = logging.getLogger('chromadb_service')
        
    def connect(self) -> bool:
        """Connect to ChromaDB
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            start_time = time.time()
            self.logger.info(f"Connecting to ChromaDB at {self.persist_directory}")
            
            # Create the data directory if it doesn't exist
            os.makedirs(self.persist_directory, exist_ok=True)
            
            # Initialize ChromaDB client with persistent storage
            self.client = chromadb.PersistentClient(path=self.persist_directory)
            
            # Initialize embedding function
            self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
            
            self.logger.info(f"ChromaDB connection established in {time.time() - start_time:.2f}s")
            return True
        except Exception as e:
            self.logger.error(f"ChromaDB connection error: {str(e)}", exc_info=True)
            return False
    
    def ensure_connected(self) -> bool:
        """Ensure ChromaDB is connected"""
        if not self.client:
            return self.connect()
        return True

# Initialize the service
chroma_service = ChromaDBService()

# Initialize service immediately at startup
with app.app_context():
    chroma_service.connect()

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        if chroma_service.ensure_connected():
            return jsonify({
                'status': 'healthy',
                'service': 'ChromaDB Service',
                'timestamp': time.time()
            })
        else:
            return jsonify({
                'status': 'unhealthy',
                'error': 'Unable to connect to ChromaDB'
            }), 500
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

# Collection Management Endpoints

@app.route('/collections', methods=['GET'])
def list_collections():
    """List all collections
    
    Query Parameters:
        counts: Set to false to skip the per-collection document count. Counting
            every collection is the expensive part when there are many of them,
            and callers that only need the names (existence checks) can opt out.
    
    Returns:
        JSON response with list of collection names
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        include_counts = request.args.get('counts', 'true').lower() != 'false'
        collection_list = chroma_service.client.list_collections()
        collections = []
        
        for col in collection_list:
            entry = {
                'name': col.name,
                'metadata': getattr(col, 'metadata', {})
            }
            if include_counts:
                try:
                    col_obj = chroma_service.client.get_collection(name=col.name)
                    entry['count'] = col_obj.count()
                except Exception as e:
                    logger.warning(f"Error getting collection info for {col.name}: {e}")
                    entry['count'] = 0
                    entry['error'] = str(e)
            collections.append(entry)
        
        return jsonify({
            'success': True,
            'collections': collections
        })
    except Exception as e:
        logger.error(f"Error listing collections: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/collections/<collection_name>', methods=['GET'])
def get_collection(collection_name: str):
    """Get collection details
    
    Args:
        collection_name: Name of the collection
        
    Returns:
        JSON response with collection details
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        try:
            collection = chroma_service.client.get_collection(name=collection_name)
            return jsonify({
                'success': True,
                'collection': {
                    'name': collection_name,
                    'count': collection.count(),
                    'metadata': getattr(collection, 'metadata', {})
                }
            })
        except Exception as e:
            return jsonify({'error': f'Collection {collection_name} not found'}), 404
    except Exception as e:
        logger.error(f"Error getting collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/collections/<collection_name>', methods=['POST'])
def create_collection(collection_name: str):
    """Create a new collection
    
    Args:
        collection_name: Name of the collection to create
        
    Request Body:
        metadata (optional): Collection metadata
        
    Returns:
        JSON response with success status
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        data = request.get_json() or {}
        metadata = data.get('metadata', {})
        
        try:
            # Check if collection already exists
            chroma_service.client.get_collection(name=collection_name)
            return jsonify({'error': f'Collection {collection_name} already exists'}), 409
        except:
            # Collection doesn't exist, create it
            collection = chroma_service.client.create_collection(
                name=collection_name,
                embedding_function=chroma_service.embedding_function,
                metadata=metadata
            )
            
            return jsonify({
                'success': True,
                'message': f'Collection {collection_name} created successfully',
                'collection': {
                    'name': collection_name,
                    'count': 0,
                    'metadata': metadata
                }
            })
    except Exception as e:
        logger.error(f"Error creating collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/collections/<collection_name>', methods=['DELETE'])
def delete_collection(collection_name: str):
    """Delete a collection
    
    Args:
        collection_name: Name of the collection to delete
        
    Returns:
        JSON response with success status
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        try:
            chroma_service.client.delete_collection(name=collection_name)
            return jsonify({
                'success': True,
                'message': f'Collection {collection_name} deleted successfully'
            })
        except Exception as e:
            return jsonify({'error': f'Collection {collection_name} not found or cannot be deleted'}), 404
    except Exception as e:
        logger.error(f"Error deleting collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

# Document Management Endpoints

@app.route('/collections/<collection_name>/documents', methods=['POST'])
def add_documents(collection_name: str):
    """Add documents to a collection
    
    Args:
        collection_name: Name of the collection
        
    Request Body:
        documents: List of documents to add
        ids: List of document IDs
        metadatas: List of metadata objects (optional)
        embeddings: List of embeddings (optional, will auto-generate if not provided)
        
    Returns:
        JSON response with success status
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        documents = data.get('documents', [])
        ids = data.get('ids', [])
        metadatas = data.get('metadatas', [])
        embeddings = data.get('embeddings', [])
        
        if not documents or not ids:
            return jsonify({'error': 'Both documents and ids are required'}), 400
        
        if len(documents) != len(ids):
            return jsonify({'error': 'Documents and ids must have the same length'}), 400
        
        try:
            collection = chroma_service.client.get_collection(name=collection_name)
        except:
            # Collection doesn't exist, create it
            collection = chroma_service.client.create_collection(
                name=collection_name,
                embedding_function=chroma_service.embedding_function
            )
        
        # Prepare data for insertion
        insert_data = {
            'ids': [str(id_) for id_ in ids],
            'documents': documents
        }
        
        if metadatas:
            # Clean metadata to ensure compatibility
            clean_metadatas = []
            for metadata in metadatas:
                clean_metadata = {}
                if metadata:
                    for k, v in metadata.items():
                        if isinstance(v, list):
                            clean_metadata[k] = ','.join(str(x) for x in v)
                        elif v is not None:
                            clean_metadata[k] = str(v)
                clean_metadatas.append(clean_metadata)
            insert_data['metadatas'] = clean_metadatas
        
        if embeddings:
            insert_data['embeddings'] = embeddings
        
        collection.add(**insert_data)
        
        return jsonify({
            'success': True,
            'message': f'Added {len(documents)} documents to collection {collection_name}',
            'count': len(documents)
        })
    except Exception as e:
        logger.error(f"Error adding documents to collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/collections/<collection_name>/documents', methods=['GET'])
def get_documents(collection_name: str):
    """Get documents from a collection
    
    Args:
        collection_name: Name of the collection
        
    Query Parameters:
        limit: Maximum number of documents to return (default: 100)
        where: JSON filter condition (optional)
        include: Comma separated fields to return (default: metadatas,documents).
            Ask for metadatas only when the document text is not needed — it is
            the difference between reading a few kilobytes and a few megabytes.
        
    Returns:
        JSON response with documents
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        limit = request.args.get('limit', 100, type=int)
        where_filter = request.args.get('where')
        include_filter = request.args.get('include')
        
        where_clause = None
        if where_filter:
            try:
                import json
                where_clause = json.loads(where_filter)
            except json.JSONDecodeError:
                return jsonify({'error': 'Invalid where filter format'}), 400
        
        try:
            collection = chroma_service.client.get_collection(name=collection_name)
            
            include_fields = ['metadatas', 'documents']
            if include_filter:
                include_fields = [field.strip() for field in include_filter.split(',') if field.strip()]
            
            query_params = {
                'limit': limit,
                'include': include_fields
            }
            if where_clause:
                query_params['where'] = where_clause
            
            results = collection.get(**query_params)
            
            # Format the results
            documents = []
            if results and 'ids' in results:
                ids = results['ids']
                docs = results.get('documents') or []
                metadatas = results.get('metadatas') or []
                
                for i, doc_id in enumerate(ids):
                    document = {
                        'id': doc_id,
                        'document': docs[i] if i < len(docs) else '',
                        'metadata': metadatas[i] if i < len(metadatas) else {}
                    }
                    documents.append(document)
            
            return jsonify({
                'success': True,
                'documents': documents,
                'count': len(documents)
            })
        except Exception as e:
            return jsonify({'error': f'Collection {collection_name} not found'}), 404
    except Exception as e:
        logger.error(f"Error getting documents from collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/collections/<collection_name>/documents/<document_id>', methods=['DELETE'])
def delete_document(collection_name: str, document_id: str):
    """Delete a document from a collection
    
    Args:
        collection_name: Name of the collection
        document_id: ID of the document to delete
        
    Returns:
        JSON response with success status
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        try:
            collection = chroma_service.client.get_collection(name=collection_name)
            collection.delete(ids=[document_id])
            
            return jsonify({
                'success': True,
                'message': f'Document {document_id} deleted from collection {collection_name}'
            })
        except Exception as e:
            return jsonify({'error': f'Document {document_id} not found in collection {collection_name}'}), 404
    except Exception as e:
        logger.error(f"Error deleting document {document_id} from collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

# Search Endpoints

@app.route('/collections/<collection_name>/search', methods=['POST'])
def search_collection(collection_name: str):
    """Search documents in a collection
    
    Args:
        collection_name: Name of the collection
        
    Request Body:
        query_texts: List of query texts to search for (optional)
        query_embeddings: List of query embeddings (optional)
        n_results: Number of results to return (default: 5)
        where: Filter condition (optional)
        
    Returns:
        JSON response with search results
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No search data provided'}), 400
        
        query_texts = data.get('query_texts', [])
        query_embeddings = data.get('query_embeddings', [])
        n_results = data.get('n_results', 5)
        where_clause = data.get('where')
        
        if not query_texts and not query_embeddings:
            return jsonify({'error': 'Either query_texts or query_embeddings must be provided'}), 400
        
        try:
            collection = chroma_service.client.get_collection(name=collection_name)
            
            query_params = {
                'n_results': n_results,
                'include': ['metadatas', 'documents', 'distances']
            }
            
            if where_clause:
                query_params['where'] = where_clause
            
            if query_texts:
                query_params['query_texts'] = query_texts
            elif query_embeddings:
                query_params['query_embeddings'] = query_embeddings
            
            results = collection.query(**query_params)
            
            # Format the results
            search_results = []
            if results and 'ids' in results and len(results['ids']) > 0:
                ids = results['ids'][0] if results['ids'] else []
                documents = results['documents'][0] if results['documents'] else []
                metadatas = results['metadatas'][0] if results['metadatas'] else []
                distances = results['distances'][0] if results['distances'] else []
                
                for i, doc_id in enumerate(ids):
                    result = {
                        'id': doc_id,
                        'document': documents[i] if i < len(documents) else '',
                        'metadata': metadatas[i] if i < len(metadatas) else {},
                    }
                    
                    if i < len(distances):
                        distance = distances[i]
                        similarity = 1 / (1 + distance) if distance >= 0 else 0
                        result['distance'] = distance
                        result['similarity'] = similarity
                    
                    search_results.append(result)
            
            return jsonify({
                'success': True,
                'results': search_results,
                'count': len(search_results)
            })
        except Exception as e:
            return jsonify({'error': f'Collection {collection_name} not found'}), 404
    except Exception as e:
        logger.error(f"Error searching collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

# Update document endpoint
@app.route('/collections/<collection_name>/documents/<document_id>', methods=['PUT'])
def update_document(collection_name: str, document_id: str):
    """Update a document in a collection
    
    Args:
        collection_name: Name of the collection
        document_id: ID of the document to update
        
    Request Body:
        document: New document text
        metadata: New metadata (optional)
        embedding: New embedding (optional)
        
    Returns:
        JSON response with success status
    """
    try:
        if not chroma_service.ensure_connected():
            return jsonify({'error': 'Unable to connect to ChromaDB'}), 500
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        document_text = data.get('document')
        metadata = data.get('metadata', {})
        embedding = data.get('embedding')
        
        if not document_text:
            return jsonify({'error': 'document text is required'}), 400
        
        try:
            collection = chroma_service.client.get_collection(name=collection_name)
            
            # Delete the existing document
            collection.delete(ids=[document_id])
            
            # Add the updated document
            update_data = {
                'ids': [document_id],
                'documents': [document_text]
            }
            
            if metadata:
                # Clean metadata
                clean_metadata = {}
                for k, v in metadata.items():
                    if isinstance(v, list):
                        clean_metadata[k] = ','.join(str(x) for x in v)
                    elif v is not None:
                        clean_metadata[k] = str(v)
                update_data['metadatas'] = [clean_metadata]
            
            if embedding:
                update_data['embeddings'] = [embedding]
            
            collection.add(**update_data)
            
            return jsonify({
                'success': True,
                'message': f'Document {document_id} updated in collection {collection_name}'
            })
        except Exception as e:
            return jsonify({'error': f'Collection {collection_name} not found'}), 404
    except Exception as e:
        logger.error(f"Error updating document {document_id} in collection {collection_name}: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Get configuration from environment variables
    host = os.getenv('CHROMADB_SERVICE_HOST', '0.0.0.0')
    port = int(os.getenv('CHROMADB_SERVICE_PORT', 8001))
    debug = os.getenv('CHROMADB_SERVICE_DEBUG', 'False').lower() == 'true'
    
    logger.info(f"Starting ChromaDB Service on {host}:{port}")
    app.run(host=host, port=port, debug=debug)
