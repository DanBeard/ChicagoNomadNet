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
    sqlite_db_path: str = os.getenv("ZIMBOT_SQLITE_PATH", "./zimbot.db")
    
    # Performance
    n_threads: int = int(os.getenv("ZIMBOT_N_THREADS", "4"))
    
    # LLM Settings
    max_tokens: int = int(os.getenv("ZIMBOT_MAX_TOKENS", "512"))
    context_window: int = int(os.getenv("ZIMBOT_CONTEXT_WINDOW", "2048"))
    
    # RAG Settings
    retrieval_k: int = int(os.getenv("ZIMBOT_RETRIEVAL_K", "3"))
    chunk_size: int = int(os.getenv("ZIMBOT_CHUNK_SIZE", "800"))
    chunk_overlap: int = int(os.getenv("ZIMBOT_CHUNK_OVERLAP", "100"))
    
    # Bot Settings
    display_name: str = os.getenv("ZIMBOT_DISPLAY_NAME", "ZimBot")
    announce_interval: int = int(os.getenv("ZIMBOT_ANNOUNCE_INTERVAL", "1800"))  # 30 minutes
    
    # Embedding Model
    embedding_model: str = os.getenv("ZIMBOT_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    # Threading/Indexer Settings
    indexer_workers: int = int(os.getenv("ZIMBOT_INDEXER_WORKERS", "4"))
    indexer_readers: int = int(os.getenv("ZIMBOT_INDEXER_READERS", "1"))  # Start with 1 for safety
    raw_queue_size: int = int(os.getenv("ZIMBOT_RAW_QUEUE_SIZE", "500"))
    chunk_queue_size: int = int(os.getenv("ZIMBOT_CHUNK_QUEUE_SIZE", "2000"))
    embed_batch_size: int = int(os.getenv("ZIMBOT_EMBED_BATCH_SIZE", "256"))
    embed_timeout: float = float(os.getenv("ZIMBOT_EMBED_TIMEOUT", "5.0"))

    # Lazy Embedding Settings
    lazy_embedding_enabled: bool = os.getenv("ZIMBOT_LAZY_EMBEDDING", "1") == "1"
    lazy_keywords_count: int = int(os.getenv("ZIMBOT_LAZY_KEYWORDS", "5"))
    lazy_idle_cpu_threshold: float = float(os.getenv("ZIMBOT_IDLE_CPU", "30.0"))
    lazy_drip_enabled: bool = os.getenv("ZIMBOT_DRIP_ENABLED", "0") == "1"  # disabled by default
    lazy_drip_interval: int = int(os.getenv("ZIMBOT_DRIP_INTERVAL", "60"))  # seconds
    lazy_drip_count: int = int(os.getenv("ZIMBOT_DRIP_COUNT", "3"))  # articles per drip
    lazy_link_follow_limit: int = int(os.getenv("ZIMBOT_LINK_FOLLOW_LIMIT", "20"))  # max links per article
    skip_startup_indexing: bool = os.getenv("ZIMBOT_SKIP_INDEXING", "1") == "1"  # skip upfront indexing

    # FAISS Index Settings
    faiss_nprobe: int = int(os.getenv("ZIMBOT_FAISS_NPROBE", "64"))  # clusters to search (accuracy vs speed)

    # Conversation History Settings
    max_conversation_messages: int = int(os.getenv("ZIMBOT_MAX_CONV_MESSAGES", "6"))

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
        if self.indexer_workers < 1:
            raise ValueError("ZIMBOT_INDEXER_WORKERS must be at least 1")
        if self.embed_batch_size < 10:
            raise ValueError("ZIMBOT_EMBED_BATCH_SIZE must be at least 10")


def get_config() -> ZimBotConfig:
    """Get validated configuration."""
    config = ZimBotConfig()
    config.validate()
    return config