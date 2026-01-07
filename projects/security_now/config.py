"""Configuration for Security Now! service."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    # Authentication
    authkey: str = os.environ.get("SN_AUTHKEY", "insecure")

    # Paths
    base_dir: Path = Path(__file__).parent
    db_path: Path = Path(os.environ.get("SN_DB_PATH", str(base_dir / "security_now.db")))
    transcripts_dir: Path = base_dir / "transcripts"
    training_data_dir: Path = base_dir / "training_data"

    # Backend service
    host_port: int = int(os.environ.get("SN_HOST_PORT", "6001"))
    host_address: str = "localhost"

    # LLM inference
    llama_url: str = os.environ.get("SN_LLAMA_URL", "http://127.0.0.1:8080")
    model_version: str = os.environ.get("SN_MODEL_VERSION", "v1")

    # News settings
    max_news_age_days: int = 7
    max_stories_per_digest: int = 10
    digest_hour_utc: int = 6

    # Pagination
    page_size_chars: int = 8000


config = Config()
