"""
Search over vector collections.

Two complementary signals, usable together:

* **semantic** - the query is embedded with the same model that wrote the stored
  vectors and the vector database ranks by embedding distance.
* **keyword** - BM25 over document text, metadata keys/values and ids, which is
  what makes metadata searchable.

A hybrid search fuses both rankings with reciprocal rank fusion.

Any of them can be narrowed by a native ChromaDB ``where`` clause. With a filter
and no query text there is nothing to rank, so the records matching the filter
are listed instead (``filter`` mode).
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from rank_bm25 import BM25Okapi

from src.utils.llm_engine import EMBEDDING_MODEL_NAME, LLMEngine
from src.utils.vector_store import VectorStore

logger = logging.getLogger('text2sql.vector_search')

SEMANTIC = 'semantic'
KEYWORD = 'keyword'
HYBRID = 'hybrid'
FILTER = 'filter'
SEARCH_MODES = (SEMANTIC, KEYWORD, HYBRID)

#: Collection whose chunks are tenant-scoped knowledge documents.
KNOWLEDGE_COLLECTION = 'knowledge_chunks'

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
KEYWORD_SCAN_LIMIT = 1000  # documents ranked per keyword search (bounded work)
FIELD_SAMPLE_SIZE = 200    # metadata records inspected to discover filter columns
MAX_FIELD_VALUES = 3       # sample values returned per metadata field
SAMPLE_VALUE_MAX = 60      # characters kept per sample value
RRF_K = 60                 # reciprocal rank fusion constant

_TOKEN_RE = re.compile(r'[a-z0-9]+')

_engine: Optional[LLMEngine] = None


class EmbeddingUnavailable(RuntimeError):
    """Raised when the embedding model needed for semantic search cannot load."""


def embed_query(text: str) -> List[float]:
    """Embed a query with the application's embedding model.

    Args:
        text: Query text

    Returns:
        List[float]: Query embedding

    Raises:
        EmbeddingUnavailable: If the model cannot be loaded
    """
    global _engine
    if _engine is None:
        _engine = LLMEngine()
    model = _engine.get_embedding_model()
    if model is None:
        raise EmbeddingUnavailable(f'Embedding model {EMBEDDING_MODEL_NAME} is unavailable')
    return [float(value) for value in model.encode(text)]


def warm_embedding_model() -> bool:
    """Load the embedding model ahead of the first semantic search.

    Loading takes a few seconds; doing it in the background keeps the first
    search responsive. Returns True when the model is ready.
    """
    try:
        embed_query('warmup')
        return True
    except Exception as exc:
        logger.warning("Embedding model warmup failed: %s", exc)
        return False


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens (used for BM25 indexing and highlighting)."""
    return _TOKEN_RE.findall((text or '').lower())


def document_tokens(entry: Dict[str, Any]) -> List[str]:
    """Tokens indexed for one collection entry: text, metadata keys/values and id."""
    parts = [entry.get('text') or '', str(entry.get('id') or '')]
    metadata = entry.get('metadata') or {}
    for key, value in metadata.items():
        parts.append(str(key))
        parts.append(str(value))
    return tokenize(' '.join(parts))


def normalize_where(raw_where: Any) -> Optional[Dict[str, Any]]:
    """Validate a native ChromaDB ``where`` clause.

    The clause is passed to the vector database untouched, so the full filter
    language stays available (``$eq``, ``$in``, ``$gte``, ``$and``, ``$or``, ...).
    """
    if raw_where in (None, {}, ''):
        return None
    if not isinstance(raw_where, dict):
        raise ValueError('filter must be a JSON object, e.g. {"table": "products"}')
    return raw_where


def _hit(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Uniform result shape shared by the ranking signals and filter listing."""
    return {
        'id': entry.get('id'),
        'text': entry.get('text') or '',
        'metadata': entry.get('metadata') or {},
        'match': None,
        'similarity': None,
        'distance': None,
        'bm25': None,
        'matched_terms': [],
    }


def _semantic_hits(store: VectorStore, collection_name: str, query: str, limit: int,
                   where: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank by embedding distance, computed by the vector database."""
    vector = embed_query(query)
    hits = []
    for raw in store.query(collection_name, query_vector=vector, limit=limit, filter_expr=where):
        hit = _hit(raw)
        hit['match'] = SEMANTIC
        hit['similarity'] = raw.get('similarity')
        hit['distance'] = raw.get('distance')
        hit['matched_terms'] = sorted(set(tokenize(query)) & set(document_tokens(raw)))
        hits.append(hit)
    return hits


def _keyword_hits(store: VectorStore, collection_name: str, query: str, limit: int,
                  where: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Rank by BM25 over text, metadata and ids.

    A ``where`` clause is applied by the vector database, so the filtered set is
    ranked exactly like the unfiltered one.
    """
    entries = store.list_entries_filtered(collection_name, where, KEYWORD_SCAN_LIMIT)
    scanned = len(entries)

    query_tokens = tokenize(query)
    if not entries or not query_tokens:
        return {'hits': [], 'scanned': scanned, 'candidates': len(entries)}

    corpus = [document_tokens(entry) for entry in entries]
    scores = BM25Okapi(corpus).get_scores(query_tokens)

    query_terms = set(query_tokens)
    ranked = []
    for entry, score in zip(entries, scores):
        if score <= 0:
            continue
        hit = _hit(entry)
        hit['match'] = KEYWORD
        hit['bm25'] = float(score)
        hit['matched_terms'] = sorted(query_terms & set(document_tokens(entry)))
        ranked.append(hit)
    ranked.sort(key=lambda hit: hit['bm25'], reverse=True)

    return {
        'hits': ranked[:limit],
        'scanned': scanned,
        'candidates': len(entries),
    }


def _filtered_hits(store: VectorStore, collection_name: str, limit: int,
                   where: Dict[str, Any]) -> Dict[str, Any]:
    """List records matching a where clause, for filter-only queries."""
    entries = store.list_entries_filtered(collection_name, where, limit)
    hits = []
    for entry in entries:
        hit = _hit(entry)
        hit['match'] = FILTER
        hits.append(hit)
    return {'hits': hits, 'scanned': len(entries)}


def _fuse(semantic_hits: List[Dict[str, Any]], keyword_hits: List[Dict[str, Any]],
          limit: int) -> List[Dict[str, Any]]:
    """Reciprocal rank fusion of both rankings, deduplicated by document id."""
    fused: Dict[Any, Dict[str, Any]] = {}
    ranks: Dict[Any, float] = {}

    for ranked, signal in ((semantic_hits, SEMANTIC), (keyword_hits, KEYWORD)):
        for position, hit in enumerate(ranked, start=1):
            key = hit['id']
            entry = fused.get(key)
            if entry is None:
                entry = dict(hit)
                entry['match'] = signal
                fused[key] = entry
            else:
                entry['match'] = 'both'
                if signal == SEMANTIC:
                    entry['similarity'] = hit['similarity']
                    entry['distance'] = hit['distance']
                else:
                    entry['bm25'] = hit['bm25']
                entry['matched_terms'] = sorted(set(entry['matched_terms']) | set(hit['matched_terms']))
            ranks[key] = ranks.get(key, 0.0) + 1.0 / (RRF_K + position)

    ordered = sorted(fused.values(), key=lambda hit: ranks[hit['id']], reverse=True)
    return ordered[:limit]


def _resolve_user_id(user_id: Any) -> Optional[int]:
    """The caller's user id, falling back to the live Flask request identity.

    No request context (background job, stdio MCP process) yields ``None``,
    which the knowledge scope turns into the documented public-only rule.
    """
    if user_id is not None:
        try:
            return int(user_id)
        except (TypeError, ValueError):
            return None
    try:
        from src.auth.decorators import current_user_id_for_rbac

        resolved = current_user_id_for_rbac()
    except Exception:
        return None
    try:
        return int(resolved) if resolved is not None else None
    except (TypeError, ValueError):
        return None


def _knowledge_scope(collection_name: str, user_id: Any) -> Optional[set]:
    """Document ids the caller may read for a knowledge collection.

    ``None`` means no narrowing (administrator / everything).  Any other
    collection is untouched.
    """
    if collection_name != KNOWLEDGE_COLLECTION:
        return None
    from src.utils import knowledge_access

    return knowledge_access.retrieval_document_ids(_resolve_user_id(user_id))


def _with_scope(where: Optional[Dict[str, Any]], scope: Optional[set]) -> Optional[Dict[str, Any]]:
    """AND the caller's ChromaDB clause with a ``document_id IN scope`` clause."""
    if scope is None:
        return where
    clause = {"document_id": {"$in": sorted(scope)}}
    if where is None:
        return clause
    return {"$and": [where, clause]}


def _empty_result(mode: str, started: float, total: int = 0) -> Dict[str, Any]:
    return {
        'results': [],
        'stats': {
            'mode': mode,
            'returned': 0,
            'semantic_hits': 0,
            'keyword_hits': 0,
            'scanned': 0,
            'total': total,
            'truncated': False,
            'took_ms': round((time.perf_counter() - started) * 1000, 1),
        },
    }


def search(store: VectorStore, collection_name: str, query: str, mode: str = SEMANTIC,
           limit: int = DEFAULT_LIMIT, where: Any = None, user_id: Any = None) -> Dict[str, Any]:
    """Search a collection.

    Args:
        store: Vector store client
        collection_name: Collection to search
        query: Query text; may be empty when ``where`` selects records on its own
        mode: One of ``semantic``, ``keyword`` or ``hybrid``
        limit: Maximum number of results
        where: Native ChromaDB filter clause
        user_id: Requesting user id; knowledge searches are narrowed to the
            documents that user may read.  Omitted, the live request identity is
            used; with neither, only public (owner-less) knowledge documents are
            searched.

    Returns:
        Dict: ``{results, stats}`` where every result carries the signal that
        matched it (``semantic``, ``keyword``, ``both`` or ``filter``).
    """
    if mode not in SEARCH_MODES:
        raise ValueError(f'mode must be one of {", ".join(SEARCH_MODES)}')

    query = (query or '').strip()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    started = time.perf_counter()

    scope = _knowledge_scope(collection_name, user_id)
    if scope is not None and not scope:
        return _empty_result(mode, started, store.count(collection_name))

    caller_filter = normalize_where(where)
    if not query and not caller_filter:
        raise ValueError('Enter search text or a filter')
    filter_clause = _with_scope(caller_filter, scope)

    # Nothing to embed: the filter itself selects the records.
    if not query:
        filtered = _filtered_hits(store, collection_name, limit, filter_clause)
        return {
            'results': filtered['hits'],
            'stats': {
                'mode': FILTER,
                'returned': len(filtered['hits']),
                'semantic_hits': 0,
                'keyword_hits': 0,
                'scanned': filtered['scanned'],
                'total': store.count(collection_name),
                'truncated': filtered['scanned'] >= limit,
                'took_ms': round((time.perf_counter() - started) * 1000, 1),
            },
        }

    semantic_hits: List[Dict[str, Any]] = []
    keyword_result: Dict[str, Any] = {'hits': [], 'scanned': None, 'candidates': 0}

    if mode in (SEMANTIC, HYBRID):
        semantic_hits = _semantic_hits(store, collection_name, query, limit, filter_clause)
    if mode in (KEYWORD, HYBRID):
        keyword_result = _keyword_hits(store, collection_name, query, limit, filter_clause)

    if mode == SEMANTIC:
        results = semantic_hits
    elif mode == KEYWORD:
        results = keyword_result['hits']
    else:
        results = _fuse(semantic_hits, keyword_result['hits'], limit)

    return {
        'results': results,
        'stats': {
            'mode': mode,
            'returned': len(results),
            'semantic_hits': len(semantic_hits),
            'keyword_hits': len(keyword_result['hits']),
            'scanned': keyword_result['scanned'],
            'total': store.count(collection_name),
            'truncated': keyword_result['scanned'] is not None and keyword_result['scanned'] >= KEYWORD_SCAN_LIMIT,
            'took_ms': round((time.perf_counter() - started) * 1000, 1),
        },
    }


def collection_fields(store: VectorStore, collection_name: str,
                      sample_size: int = FIELD_SAMPLE_SIZE) -> Dict[str, Any]:
    """Metadata fields present in a collection, with a few sample values.

    Only a bounded, metadata-only sample is read: discovering filter columns must
    stay cheap regardless of how many vectors the collection holds. Fields are
    reported in first-seen order with up to ``MAX_FIELD_VALUES`` sample values.
    """
    records = store.sample_metadata(collection_name, sample_size)

    samples: Dict[str, List[str]] = {}
    for record in records:
        for key, value in (record.get('metadata') or {}).items():
            values = samples.setdefault(key, [])
            if len(values) >= MAX_FIELD_VALUES:
                continue
            text = str(value)[:SAMPLE_VALUE_MAX]
            if text not in values:
                values.append(text)

    return {
        'fields': [{'name': name, 'values': values} for name, values in samples.items()],
        'sampled': len(records),
    }
