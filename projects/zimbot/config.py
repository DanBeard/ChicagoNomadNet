"""
ZimBot Configuration

Central configuration management using dataclasses and environment variables.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ZimBotConfig:
    """Configuration for ZimBot using environment variables with sensible defaults."""
    
    # Paths
    zim_path: str = os.getenv("ZIM_PATH", "/zim/")
    model_path: str = os.getenv("ZIMBOT_MODEL_PATH", "./models/")
    chromadb_path: str = os.getenv("ZIMBOT_CHROMADB_PATH", "./chromadb_data")
    
    # Performance
    n_threads: int = int(os.getenv("ZIMBOT_N_THREADS", "4"))
    
    # LLM Settings
    max_tokens: int = int(os.getenv("ZIMBOT_MAX_TOKENS", "512"))
    context_window: int = int(os.getenv("ZIMBOT_CONTEXT_WINDOW", "2048"))
    
    # RAG Settings
    retrieval_k: int = int(os.getenv("ZIMBOT_RETRIEVAL_K", "5"))
    chunk_size: int = int(os.getenv("ZIMBOT_CHUNK_SIZE", "800"))
    chunk_overlap: int = int(os.getenv("ZIMBOT_CHUNK_OVERLAP", "100"))
    
    # Bot Settings
    display_name: str = os.getenv("ZIMBOT_DISPLAY_NAME", "ZimBot")
    announce_interval: int = int(os.getenv("ZIMBOT_ANNOUNCE_INTERVAL", "1800"))  # 30 minutes
    
    # Embedding Model
    embedding_model: str = os.getenv("ZIMBOT_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    
    def validate(self):
        """Validate configuration values."""
        if self.n_threads < 1:
            raise ValueError("ZIMBOT_N_THREADS must be at least 1")
        if self.max_tokens < 64:
            raise ValueError("ZIMBOT_MAX_TOKENS must be at least 64")
        if self.context_window < 512:
            raise ValueError("ZIMBOT_CONTEXT_WINDOW must be at least 512")
        if self.retrieval_k < 1:
            raise ValueError("ZIMBOT_RETRIEVAL_K must be at least 1")
        if self.chunk_size < 100:
            raise ValueError("ZIMBOT_CHUNK_SIZE must be at least 100")
        if self.announce_interval < 60:
            raise ValueError("ZIMBOT_ANNOUNCE_INTERVAL must be at least 60 seconds")


def get_config() -> ZimBotConfig:
    """Get validated configuration."""
    config = ZimBotConfig()
    config.validate()
    return config