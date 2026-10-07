"""Embedding generation and reranking model caching."""

import logging
import threading
import time
from typing import Optional
import numpy as np

logger = logging.getLogger("text2sql.embeddings")

# Single source of truth: the model that writes the stored vectors must also be
# the one that embeds search queries, otherwise similarity is meaningless.
EMBEDDING_MODEL_NAME = "all-MiniLM-L12-v2"
RERANKING_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_EMBEDDING_MODEL_LOCK = threading.Lock()
_EMBEDDING_MODEL_CACHE = None
_RERANKING_MODEL_LOCK = threading.Lock()
_RERANKING_MODEL_CACHE = None


def get_embedding_model():
    """Get or initialize the sentence transformer model for embeddings.

    Thread-safe and cached process-wide to avoid meta-device tensor race conditions.
    """
    global _EMBEDDING_MODEL_CACHE
    if _EMBEDDING_MODEL_CACHE is not None:
        return _EMBEDDING_MODEL_CACHE

    with _EMBEDDING_MODEL_LOCK:
        if _EMBEDDING_MODEL_CACHE is None:
            start_time = time.time()
            logger.info("Loading embedding model 'sentence-transformers/%s'", EMBEDDING_MODEL_NAME)
            last_error = None
            for attempt in range(3):
                try:
                    from sentence_transformers import SentenceTransformer
                    _EMBEDDING_MODEL_CACHE = SentenceTransformer(EMBEDDING_MODEL_NAME)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    logger.warning("Embedding model load attempt %s failed: %s", attempt + 1, e)
                    time.sleep(0.5)

            if last_error is not None:
                logger.error("Failed to load embedding model: %s", last_error, exc_info=True)
                return None
            logger.info("Embedding model loaded in %.2fs", time.time() - start_time)

    return _EMBEDDING_MODEL_CACHE


def generate_embedding(text: str) -> Optional[np.ndarray]:
    """Generate embedding vector for the given text.

    Returns None if text is empty or embedding model is unavailable.
    Does NOT use random fallback vectors.
    """
    if not text or not str(text).strip():
        logger.warning("Empty text passed to generate_embedding; returning None")
        return None

    model = get_embedding_model()
    if model is None:
        logger.error("Embedding model not available; returning None")
        return None

    try:
        start_time = time.time()
        embedding = model.encode(text)
        logger.debug("Generated embedding in %.2fs with shape %s", time.time() - start_time, getattr(embedding, 'shape', None))
        return embedding
    except Exception as e:
        logger.error("Error generating embedding: %s", e, exc_info=True)
        return None


def get_reranking_model():
    """Get or initialize a cross-encoder model for semantic reranking."""
    global _RERANKING_MODEL_CACHE
    if _RERANKING_MODEL_CACHE is not None:
        return _RERANKING_MODEL_CACHE

    with _RERANKING_MODEL_LOCK:
        if _RERANKING_MODEL_CACHE is None:
            start_time = time.time()
            logger.info("Loading reranking model '%s'", RERANKING_MODEL_NAME)
            try:
                from sentence_transformers import CrossEncoder
                _RERANKING_MODEL_CACHE = CrossEncoder(RERANKING_MODEL_NAME)
                logger.info("Reranking model loaded in %.2fs", time.time() - start_time)
            except Exception as e:
                logger.error("Failed to load reranking model: %s", e, exc_info=True)
                return None

    return _RERANKING_MODEL_CACHE


__all__ = [
    "EMBEDDING_MODEL_NAME",
    "RERANKING_MODEL_NAME",
    "get_embedding_model",
    "generate_embedding",
    "get_reranking_model",
]
