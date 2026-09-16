"""
Configuration file for ChromaDB Service
"""

import os

class Config:
    """Base configuration"""
    
    # Service settings
    SERVICE_HOST = os.getenv('CHROMADB_SERVICE_HOST', '0.0.0.0')
    SERVICE_PORT = int(os.getenv('CHROMADB_SERVICE_PORT', 8001))
    DEBUG = os.getenv('CHROMADB_SERVICE_DEBUG', 'False').lower() == 'true'
    
    # ChromaDB settings
    CHROMA_PERSIST_DIRECTORY = os.getenv('CHROMA_PERSIST_DIRECTORY', './chroma_data')
    CHROMA_EMBEDDING_MODEL = os.getenv('CHROMA_EMBEDDING_MODEL', 'all-MiniLM-L6-v2')
    
    # Logging settings
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # API settings
    MAX_BATCH_SIZE = int(os.getenv('MAX_BATCH_SIZE', 1000))
    DEFAULT_SEARCH_LIMIT = int(os.getenv('DEFAULT_SEARCH_LIMIT', 5))
    MAX_SEARCH_LIMIT = int(os.getenv('MAX_SEARCH_LIMIT', 100))

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
