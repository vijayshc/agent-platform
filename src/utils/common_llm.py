# src/utils/common_llm.py
"""
Common LLM engine instance to be shared across the application.
This module provides a singleton pattern for the LLM engine to avoid
multiple initializations and ensure consistent configuration.
"""

import logging
from src.utils.llm_engine import LLMEngine

logger = logging.getLogger('text2sql.common_llm')

# Global instance
_llm_engine_instance = None

def get_llm_engine() -> LLMEngine:
    """
    Get the shared LLM engine instance.
    
    Returns:
        LLMEngine: The shared LLM engine instance
    """
    global _llm_engine_instance
    
    if _llm_engine_instance is None:
        logger.info("Initializing shared LLM engine instance")
        _llm_engine_instance = LLMEngine()
        logger.info("Shared LLM engine instance initialized successfully")
    
    return _llm_engine_instance

def reset_llm_engine():
    """
    Reset the shared LLM engine instance.
    This can be useful for testing or when configuration changes.
    """
    global _llm_engine_instance
    logger.info("Resetting shared LLM engine instance")
    
    if _llm_engine_instance:
        try:
            _llm_engine_instance.close()
        except Exception as e:
            logger.warning(f"Error closing LLM engine: {e}")
    
    _llm_engine_instance = None
    logger.info("Shared LLM engine instance reset")
