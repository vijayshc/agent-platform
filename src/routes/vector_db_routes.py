"""
Vector database exploration API.

Lists the collections held by the vector store and searches them: semantically
(query embedding sent to the vector database), by keyword (BM25 over text and
metadata) or both at once.
"""

import logging
import re
import threading
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from src.auth.decorators import admin_required, current_user_id_for_rbac, module_required
from src.utils import collection_access
from src.utils.llm_engine import EMBEDDING_MODEL_NAME
from src.utils.vector_search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    SEARCH_MODES,
    EmbeddingUnavailable,
    collection_fields,
    warm_embedding_model,
)
from src.utils.vector_search import search as run_search
from src.utils.vector_store import VectorStore

logger = logging.getLogger('text2sql.routes.vector_db')

vector_db_bp = Blueprint('vector_db', __name__, url_prefix='/admin')
BASE = '/api/vector-db'

#: Chroma-compatible collection name: 3-63 chars of letters/digits/._-.
_VALID_COLLECTION_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{1,61}[A-Za-z0-9]$')

vector_store = VectorStore()
_warmup_started = threading.Event()


def _connect() -> Optional[Tuple[Any, int]]:
    """Ensure the vector store is reachable; returns an error response or None."""
    if vector_store.client or vector_store.connect():
        return None
    return jsonify({'success': False, 'error': 'Failed to connect to the vector database'}), 503


def _failure(message: str, status: int = 500) -> Tuple[Any, int]:
    return jsonify({'success': False, 'error': message}), status


def _warm_once() -> None:
    """Load the embedding model in the background so the first search is fast."""
    if _warmup_started.is_set():
        return
    _warmup_started.set()
    threading.Thread(target=warm_embedding_model, name='embedding-warmup', daemon=True).start()


@vector_db_bp.route('/vector-db')
@admin_required()
@module_required("vector_db")
def vector_db_page():
    """Render the vector database search console (React)"""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()


@vector_db_bp.route(f'{BASE}/collections', methods=['GET'])
@admin_required()
@module_required("vector_db")
def list_collections():
    """Collections with their document counts and role-grant state.

    Collections are created outside the app (directly in the vector database),
    so any the registry has not seen yet are claimed here for the acting
    administrator.  That is what makes them grantable to roles afterwards.
    """
    error = _connect()
    if error:
        return error
    try:
        collections = vector_store.list_collections_detailed()
        collections.sort(key=lambda item: item['name'])
        names = [item['name'] for item in collections]
        collection_access.register_missing(names, current_user_id_for_rbac())
        registry = collection_access.registry_rows(names)
        for item in collections:
            entry = registry.get(item['name'], {})
            roles = collection_access.granted_role_names(item['name'])
            item['access_id'] = entry.get('access_id')
            item['owner_id'] = entry.get('owner_id')
            item['roles'] = roles
            item['restricted'] = len(roles) > 0
        _warm_once()
        return jsonify({
            'success': True,
            'collections': collections,
            'total_documents': sum(item['count'] for item in collections),
            'embedding_model': EMBEDDING_MODEL_NAME,
        })
    except Exception as exc:
        logger.exception("Error listing vector collections")
        return _failure(str(exc))


@vector_db_bp.route(f'{BASE}/collections', methods=['POST'])
@admin_required()
@module_required("vector_db")
def create_collection():
    """Create a collection and claim it so its access can be granted.

    Body: ``{"name": "my_collection"}``.  Only administrators may create a
    collection; knowledge uploaders can only select one.
    """
    error = _connect()
    if error:
        return error
    data: Dict[str, Any] = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not _VALID_COLLECTION_NAME.match(name):
        return _failure(
            'Collection name must be 3-63 characters using letters, numbers, '
            'underscores, hyphens or dots.',
            400,
        )
    try:
        if name in vector_store.list_collections():
            return _failure(f'Collection "{name}" already exists', 409)
        if not vector_store.init_collection(name):
            return _failure(f'Failed to create collection "{name}"')
        collection_access.register_collection(name, current_user_id_for_rbac())
        return jsonify({'success': True, 'name': name})
    except Exception as exc:
        logger.exception("Error creating vector collection %s", name)
        return _failure(str(exc))


@vector_db_bp.route(f'{BASE}/collections/<path:collection_name>/fields', methods=['GET'])
@admin_required()
@module_required("vector_db")
def get_collection_fields(collection_name: str):
    """Metadata fields available for filtering in a collection."""
    error = _connect()
    if error:
        return error
    if collection_name not in vector_store.list_collections():
        return _failure(f'Collection "{collection_name}" does not exist', 404)
    try:
        return jsonify({'success': True, **collection_fields(vector_store, collection_name)})
    except Exception as exc:
        logger.exception("Error reading collection fields for %s", collection_name)
        return _failure(str(exc))


@vector_db_bp.route(f'{BASE}/collections/<path:collection_name>/search', methods=['POST'])
@admin_required()
@module_required("vector_db", read_methods=("GET", "HEAD", "OPTIONS", "POST"))
def search_collection(collection_name: str):
    """Search a collection.

    Body:
        query (str): Text to search for; optional when ``where`` is given
        mode (str): ``semantic``, ``keyword`` or ``hybrid`` (default ``semantic``)
        limit (int): Maximum results (default 10, max 50)
        where (dict): Native ChromaDB filter clause, e.g. ``{"table": "products"}``
    """
    error = _connect()
    if error:
        return error
    if collection_name not in vector_store.list_collections():
        return _failure(f'Collection "{collection_name}" does not exist', 404)

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    query = (data.get('query') or '').strip()
    where = data.get('where')
    if not query and not where:
        return _failure('Search text is required', 400)

    mode = data.get('mode') or 'semantic'
    if mode not in SEARCH_MODES:
        return _failure(f'Mode must be one of {", ".join(SEARCH_MODES)}', 400)

    try:
        limit = int(data.get('limit') or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return _failure('limit must be a number', 400)
    limit = max(1, min(limit, MAX_LIMIT))

    try:
        outcome = run_search(vector_store, collection_name, query, mode, limit, where)
    except EmbeddingUnavailable as exc:
        return _failure(str(exc), 503)
    except ValueError as exc:
        return _failure(str(exc), 400)
    except Exception as exc:
        logger.exception("Search failed for collection %s", collection_name)
        return _failure(str(exc))

    return jsonify({'success': True, 'query': query, **outcome})
