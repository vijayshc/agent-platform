# ChromaDB Service

A standalone microservice that provides REST API endpoints for ChromaDB operations. This service is decoupled from the main Text2SQL application and can be used by any application that needs vector database functionality.

## Features

- **Collection Management**: Create, list, get details, and delete collections
- **Document Management**: Add, retrieve, update, and delete documents
- **Vector Search**: Search documents using text queries or embeddings
- **Metadata Filtering**: Filter documents based on metadata
- **Health Monitoring**: Health check endpoint for monitoring
- **Auto-embedding**: Automatic embedding generation using sentence-transformers

## API Endpoints

### Health Check
- `GET /health` - Check service health status

### Collection Management
- `GET /collections` - List all collections
- `GET /collections/{name}` - Get collection details
- `POST /collections/{name}` - Create a new collection
- `DELETE /collections/{name}` - Delete a collection

### Document Management
- `POST /collections/{name}/documents` - Add documents to collection
- `GET /collections/{name}/documents` - Get documents from collection
- `PUT /collections/{name}/documents/{id}` - Update a document
- `DELETE /collections/{name}/documents/{id}` - Delete a document

### Search
- `POST /collections/{name}/search` - Search documents in collection

## Installation

1. Navigate to the ChromaDB service directory:
```bash
cd chromadb_service
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Start the service:
```bash
./start_service.sh
```

Or manually:
```bash
python app.py
```

## Configuration

The service can be configured using environment variables:

- `CHROMADB_SERVICE_HOST`: Host to bind to (default: 0.0.0.0)
- `CHROMADB_SERVICE_PORT`: Port to listen on (default: 8001)
- `CHROMADB_SERVICE_DEBUG`: Enable debug mode (default: False)
- `CHROMA_PERSIST_DIRECTORY`: ChromaDB data directory (default: ./chroma_data)
- `CHROMA_EMBEDDING_MODEL`: Embedding model to use (default: all-MiniLM-L6-v2)

## API Usage Examples

### Create a Collection
```bash
curl -X POST http://localhost:8001/collections/my_collection \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"description": "My test collection"}}'
```

### Add Documents
```bash
curl -X POST http://localhost:8001/collections/my_collection/documents \
  -H "Content-Type: application/json" \
  -d '{
    "documents": ["This is a test document", "Another document"],
    "ids": ["doc1", "doc2"],
    "metadatas": [{"category": "test"}, {"category": "example"}]
  }'
```

### Search Documents
```bash
curl -X POST http://localhost:8001/collections/my_collection/search \
  -H "Content-Type: application/json" \
  -d '{
    "query_texts": ["test document"],
    "n_results": 5
  }'
```

### List Collections
```bash
curl http://localhost:8001/collections
```

## Integration with Text2SQL Application

The main Text2SQL application should be updated to use this service via HTTP requests instead of direct ChromaDB integration. Update the `VectorStore` class to make HTTP requests to this service.

## Monitoring

- The service logs to `logs/chromadb_service.log`
- Health check is available at `/health`
- Service status can be monitored via standard HTTP monitoring tools

## Production Deployment

For production deployment:

1. Use a production WSGI server like Gunicorn:
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8001 app:app
```

2. Set up proper logging and monitoring
3. Configure environment variables for production settings
4. Set up reverse proxy (nginx) if needed
5. Ensure data directory persistence and backups
