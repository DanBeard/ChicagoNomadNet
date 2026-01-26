"""
Remote Inference Clients

HTTP clients for offloading LLM and embedding work to a remote LMStudio server.
Provides OpenAI-compatible API access for chat completions and embeddings.
"""

import logging
from typing import List, Optional, Union

import numpy as np
import requests

logger = logging.getLogger(__name__)


class RemoteLLM:
    """
    HTTP client for LMStudio chat completions.

    Uses the OpenAI-compatible /v1/chat/completions endpoint.
    """

    def __init__(self, base_url: str, timeout: int = 120):
        """
        Initialize the remote LLM client.

        Args:
            base_url: Base URL of the LMStudio server (e.g., "http://10.0.0.89:1234")
            timeout: Request timeout in seconds (default 120 for long generations)
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def health_check(self) -> bool:
        """Check if the remote server is running and accessible."""
        try:
            # LMStudio serves models list at /v1/models
            response = requests.get(
                f"{self.base_url}/v1/models",
                timeout=5
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def chat(
        self,
        messages: List[dict],
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Optional[List[str]] = None
    ) -> str:
        """
        Generate chat completion.

        Args:
            messages: List of message dicts with "role" and "content" keys
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0-2.0)
            stop: Optional list of stop sequences

        Returns:
            Generated text response

        Raises:
            requests.RequestException: On network or API errors
        """
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False
        }

        if stop:
            payload["stop"] = stop

        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()

            result = response.json()
            return result["choices"][0]["message"]["content"].strip()

        except requests.RequestException as e:
            logger.error(f"Remote LLM request failed: {e}")
            raise

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Optional[List[str]] = None
    ) -> str:
        """
        Generate text completion using chat API.

        Converts prompt to chat format for LMStudio compatibility.

        Args:
            prompt: User prompt
            system_prompt: Optional system instructions
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            stop: Optional list of stop sequences

        Returns:
            Generated text response
        """
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        return self.chat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop
        )


class RemoteEmbedding:
    """
    HTTP client for LMStudio embeddings.

    Uses the OpenAI-compatible /v1/embeddings endpoint.
    Compatible with all-MiniLM-L6-v2 (384 dimensions).
    """

    def __init__(
        self,
        base_url: str,
        model: str = "text-embedding-all-minilm-l6-v2-embedding",
        timeout: int = 60
    ):
        """
        Initialize the remote embedding client.

        Args:
            base_url: Base URL of the LMStudio server
            model: Model name/path for embeddings (LMStudio needs this)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout

    def health_check(self) -> bool:
        """Check if the remote server is running and accessible."""
        try:
            response = requests.get(
                f"{self.base_url}/v1/models",
                timeout=5
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def encode(
        self,
        texts: Union[str, List[str]],
        normalize: bool = True,
        normalize_embeddings: Optional[bool] = None,
        batch_size: int = 64,
        show_progress_bar: bool = False
    ) -> np.ndarray:
        """
        Generate embeddings for texts.

        Matches the SentenceTransformer.encode() interface for drop-in replacement.

        Args:
            texts: Single text or list of texts to embed
            normalize: Whether to L2-normalize embeddings (for cosine similarity)
            normalize_embeddings: Alias for normalize (SentenceTransformer compatibility)
            batch_size: Batch size for processing (handled by server)
            show_progress_bar: Ignored (for API compatibility)

        Returns:
            numpy array of embeddings, shape (n_texts, embedding_dim)
        """
        # Support both parameter names for compatibility
        if normalize_embeddings is not None:
            normalize = normalize_embeddings
        # Handle single string input
        if isinstance(texts, str):
            texts = [texts]
            single_input = True
        else:
            texts = list(texts)
            single_input = False

        if not texts:
            return np.array([])

        # Process in batches to avoid overwhelming the server
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            payload = {
                "input": batch,
                "model": self.model
            }

            try:
                response = requests.post(
                    f"{self.base_url}/v1/embeddings",
                    json=payload,
                    timeout=self.timeout
                )
                response.raise_for_status()

                result = response.json()

                # Extract embeddings, sorted by index
                batch_embeddings = sorted(result["data"], key=lambda x: x["index"])
                batch_vectors = [item["embedding"] for item in batch_embeddings]
                all_embeddings.extend(batch_vectors)

            except requests.RequestException as e:
                logger.error(f"Remote embedding request failed: {e}")
                raise

        embeddings = np.array(all_embeddings, dtype=np.float32)

        # Normalize if requested (standard for cosine similarity)
        if normalize:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)  # Avoid division by zero
            embeddings = embeddings / norms

        return embeddings[0] if single_input else embeddings

    def encode_single(self, text: str, normalize: bool = True) -> np.ndarray:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed
            normalize: Whether to L2-normalize the embedding

        Returns:
            1D numpy array of embedding values
        """
        return self.encode([text], normalize=normalize)[0]


def test_remote_server(base_url: str) -> dict:
    """
    Test connectivity and capabilities of a remote LMStudio server.

    Args:
        base_url: Server URL to test

    Returns:
        Dict with test results
    """
    results = {
        "url": base_url,
        "reachable": False,
        "models": [],
        "chat_working": False,
        "embeddings_working": False,
        "errors": []
    }

    # Test basic connectivity
    try:
        response = requests.get(f"{base_url}/v1/models", timeout=5)
        if response.status_code == 200:
            results["reachable"] = True
            data = response.json()
            results["models"] = [m.get("id", "unknown") for m in data.get("data", [])]
    except requests.RequestException as e:
        results["errors"].append(f"Connection failed: {e}")
        return results

    # Test chat completions
    try:
        llm = RemoteLLM(base_url, timeout=30)
        response = llm.chat(
            messages=[{"role": "user", "content": "Say 'test' and nothing else."}],
            max_tokens=10
        )
        if response:
            results["chat_working"] = True
    except Exception as e:
        results["errors"].append(f"Chat test failed: {e}")

    # Test embeddings
    try:
        embedder = RemoteEmbedding(base_url, timeout=30)
        embedding = embedder.encode_single("test")
        if len(embedding) > 0:
            results["embeddings_working"] = True
            results["embedding_dim"] = len(embedding)
    except Exception as e:
        results["errors"].append(f"Embedding test failed: {e}")

    return results


if __name__ == "__main__":
    # Quick test
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else "http://10.0.0.89:1234"
    print(f"Testing remote server: {url}")

    results = test_remote_server(url)

    print(f"  Reachable: {results['reachable']}")
    print(f"  Models: {results['models']}")
    print(f"  Chat working: {results['chat_working']}")
    print(f"  Embeddings working: {results['embeddings_working']}")

    if results.get('embedding_dim'):
        print(f"  Embedding dim: {results['embedding_dim']}")

    if results['errors']:
        print(f"  Errors: {results['errors']}")
