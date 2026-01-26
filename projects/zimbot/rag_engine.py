"""
RAG Engine

Handles the Retrieval Augmented Generation pipeline:
- Query retrieval from SQLite + sqlite-vec
- Prompt construction with context
- LLM inference using remote LMStudio server
- Response generation with source attribution
"""

import re
import time
from typing import List, Dict, Optional
from dataclasses import dataclass

from .config import ZimBotConfig
from .zim_indexer import ZIMIndexer

# Import remote inference client
import sys
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])
from shared.remote_inference import RemoteLLM


@dataclass
class RAGResponse:
    """Response from the RAG engine."""
    answer: str
    sources: List[Dict]
    tokens_used: int
    processing_time: float
    result_count: int = 0
    avg_distance: float = 0.0
    coverage_sufficient: bool = True


class RAGEngine:
    """Main RAG engine class."""

    def __init__(self, config: ZimBotConfig, indexer: ZIMIndexer):
        self.config = config
        self.indexer = indexer
        self.llm: Optional[RemoteLLM] = None
        self.model_loaded = False

    def load_model(self, model_path: Optional[str] = None):
        """
        Connect to remote LLM server.

        The model_path parameter is ignored - we use the remote LMStudio server.
        """
        print(f"Connecting to remote LLM server: {self.config.llm_url}")

        try:
            self.llm = RemoteLLM(
                base_url=self.config.llm_url,
                timeout=self.config.llm_timeout
            )

            # Test the connection
            if self.llm.health_check():
                self.model_loaded = True
                print(f"Remote LLM server connected successfully!")
            else:
                raise ConnectionError(f"Remote LLM server not responding at {self.config.llm_url}")

        except Exception as e:
            import traceback
            print(f"Failed to connect to remote LLM: {e}")
            print(f"Exception type: {type(e).__name__}")
            print(f"Full traceback:")
            traceback.print_exc()
            self.model_loaded = False
            raise
    
    def _build_prompt(self, question: str, context_chunks: List[str], sources: List[Dict],
                      conversation_history: str = "", verbosity: str = "detailed") -> str:
        """Build ChatML-formatted prompt for SmolLM3.

        Key design choices:
        - Context goes in system message as "knowledge" so model treats it as its own knowledge
        - User message contains ONLY the question for clear separation
        - Explicit instructions to avoid "book report" style responses
        - Verbosity-aware instructions
        """

        # Join context with clear separators
        context_content = "\n\n---\n\n".join(context_chunks) if context_chunks else "No relevant information found."

        # Verbosity instruction
        if verbosity == "concise":
            verbosity_instruction = "Be concise - give direct answers without unnecessary elaboration."
        else:
            verbosity_instruction = "Be thorough but focused - explain clearly without rambling."

        # Build system message with knowledge injection
        system_content = f"""You are ZimBot, a knowledgeable assistant. You have access to the following reference information:

<knowledge>
{context_content}
</knowledge>

INSTRUCTIONS:
- Use this knowledge naturally to answer questions, as if you already knew it
- Do NOT say "according to the context", "the passage states", "based on the information provided", or similar phrases
- Do NOT summarize or report on the knowledge - use it to directly answer the question
- If the knowledge doesn't cover the question, say so honestly
- {verbosity_instruction}
{conversation_history}"""

        # User message is ONLY the question - no context mixing
        prompt = f"""<|im_start|>system
{system_content}<|im_end|>
<|im_start|>user
{question}<|im_end|>
<|im_start|>assistant
"""
        return prompt
    
    def _format_response(self, llm_response: str, sources: List[Dict]) -> str:
        """Format the final response with compact source attribution."""

        # Strip thinking blocks from model output (SmolLM3 uses <think>...</think>)
        response = re.sub(r'<think>.*?</think>', '', llm_response, flags=re.DOTALL)
        response = response.strip()

        if sources:
            # Compact format: just path, truncated to 40 chars
            paths = [
                f"[{i}] {s['metadata'].get('path', '?')[:40]}"
                for i, s in enumerate(sources, 1)
            ]
            response += "\nSrc: " + " ".join(paths)

        return response
    
    def generate_response(self, question: str, conversation_history: Optional[List[Dict]] = None,
                          verbosity: str = "detailed") -> RAGResponse:
        """Generate a response to a question using RAG.

        Args:
            question: The user's question
            conversation_history: List of previous messages in OpenAI chat format
                                  [{"role": "user", "content": "..."}, ...]
            verbosity: "concise" or "detailed" - affects response style
        """

        if not self.model_loaded:
            raise RuntimeError("Model not loaded. Call load_model() first.")

        start_time = time.time()

        # Step 1: Retrieve relevant context
        print(f"Retrieving context for: {question[:50]}...")
        search_start = time.time()
        context_results = self.indexer.search(question, k=self.config.retrieval_k)
        search_time = time.time() - search_start
        print(f"  Context retrieval: {search_time:.2f}s ({len(context_results)} results)")

        context_chunks = [result['content'] for result in context_results]
        sources = context_results

        # Assess coverage quality
        result_count = len(context_results)
        avg_distance = 0.0
        if context_results:
            distances = [r.get('distance', 1.0) for r in context_results]
            avg_distance = sum(distances) / len(distances)

        # Coverage is insufficient if:
        # - Fewer than expected results (less than half of k)
        # - OR average distance is high (poor semantic match)
        coverage_sufficient = (
            result_count >= self.config.retrieval_k // 2 and
            avg_distance < 0.8  # Cosine distance threshold
        )

        # Step 2: Build system message and user message for chat API
        context_content = "\n\n---\n\n".join(context_chunks) if context_chunks else "No relevant information found."

        # Verbosity instruction
        if verbosity == "concise":
            verbosity_instruction = "Be concise - give direct answers without unnecessary elaboration."
        else:
            verbosity_instruction = "Be thorough but focused - explain clearly without rambling."

        system_content = f"""You are ZimBot, a knowledgeable assistant. You have access to the following reference information:

<knowledge>
{context_content}
</knowledge>

INSTRUCTIONS:
- Use this knowledge naturally to answer questions, as if you already knew it
- Do NOT say "according to the context", "the passage states", "based on the information provided", or similar phrases
- Do NOT summarize or report on the knowledge - use it to directly answer the question
- If the knowledge doesn't cover the question, say so honestly
- {verbosity_instruction}"""

        # Build messages array with proper chat format
        messages = [{"role": "system", "content": system_content}]

        # Add conversation history as proper chat messages (user/assistant turns)
        if conversation_history:
            messages.extend(conversation_history)

        # Add the current question
        messages.append({"role": "user", "content": question})

        # Debug: Echo messages to console
        print(f"\n{'='*60}")
        print("MESSAGES BEING SENT TO REMOTE LLM:")
        print(f"{'='*60}")
        for i, msg in enumerate(messages):
            content_preview = msg['content'][:300] + "..." if len(msg['content']) > 300 else msg['content']
            print(f"[{i}] {msg['role']}: {content_preview}")
        print(f"{'='*60}\n")

        # Step 3: Generate response using remote LLM
        print(f"Generating response with remote LLM...")
        llm_start = time.time()
        tokens_used = 0

        try:
            llm_output = self.llm.chat(
                messages=messages,
                max_tokens=self.config.max_tokens,
                temperature=0.5,
                stop=["<|im_end|>", "<|im_start|>"]
            )
            print(f"  LLM inference took {time.time() - llm_start:.2f}s")

        except Exception as e:
            print(f"LLM generation failed: {e}")
            llm_output = f"Sorry, I encountered an error while processing your question: {str(e)}"

        # Step 4: Format the final response
        final_response = self._format_response(llm_output, sources)

        processing_time = time.time() - start_time

        return RAGResponse(
            answer=final_response,
            sources=sources,
            tokens_used=tokens_used,
            processing_time=processing_time,
            result_count=result_count,
            avg_distance=avg_distance,
            coverage_sufficient=coverage_sufficient
        )
    
    def get_model_info(self) -> Dict:
        """Get information about the remote LLM connection."""
        if not self.model_loaded:
            return {"status": "not_loaded"}

        return {
            "remote_url": self.config.llm_url,
            "timeout": self.config.llm_timeout,
            "model_loaded": self.model_loaded,
            "type": "remote_lmstudio"
        }
    
    def health_check(self) -> Dict:
        """Perform a health check of the RAG engine."""

        # Check remote LLM connection
        if self.llm:
            llm_healthy = self.llm.health_check()
            model_status = "connected" if llm_healthy else "disconnected"
        else:
            model_status = "not_initialized"

        # Check indexer status
        indexer_info = self.indexer.get_collection_info()

        return {
            "model_status": model_status,
            "remote_url": self.config.llm_url,
            "indexer_status": indexer_info,
            "config": {
                "max_tokens": self.config.max_tokens,
                "context_window": self.config.context_window,
                "retrieval_k": self.config.retrieval_k
            }
        }