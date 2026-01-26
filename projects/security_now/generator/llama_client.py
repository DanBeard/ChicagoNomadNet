"""HTTP client for llama.cpp server."""
import requests
from typing import Optional
import logging

from ..config import config

logger = logging.getLogger(__name__)


class LlamaClient:
    """Client for llama.cpp HTTP server (OpenAI-compatible API)."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or config.llama_url

    def health_check(self) -> bool:
        """Check if the server is running (LMStudio-compatible)."""
        try:
            # LMStudio uses /v1/models for health check
            response = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        top_p: float = 0.9,
        stop: Optional[list[str]] = None
    ) -> str:
        """
        Generate text completion using chat API.

        Uses the /v1/chat/completions endpoint for LMStudio compatibility.
        """
        # Build messages array for chat API
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": False
        }

        if stop:
            payload["stop"] = stop

        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=600  # 10 minute timeout for long generations
            )
            response.raise_for_status()

            result = response.json()
            return result["choices"][0]["message"]["content"].strip()

        except requests.RequestException as e:
            logger.error(f"LLM request failed: {e}")
            raise

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 8192,
        temperature: float = 0.7
    ) -> str:
        """
        Generate chat completion using OpenAI-compatible API.

        Messages format: [{"role": "system|user|assistant", "content": "..."}]
        """
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False
        }

        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=600  # 10 minute timeout
            )
            response.raise_for_status()

            result = response.json()
            return result["choices"][0]["message"]["content"].strip()

        except requests.RequestException as e:
            logger.error(f"Chat request failed: {e}")
            raise


# Singleton instance
_client = None


def get_client() -> LlamaClient:
    """Get or create the LLama client singleton."""
    global _client
    if _client is None:
        _client = LlamaClient()
    return _client
