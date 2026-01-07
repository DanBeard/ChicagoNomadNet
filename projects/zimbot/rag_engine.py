"""
RAG Engine

Handles the Retrieval Augmented Generation pipeline:
- Query retrieval from SQLite + sqlite-vec
- Prompt construction with context
- LLM inference using llama-cpp-python
- Response generation with source attribution
"""

import os
import re
import time
from typing import List, Dict, Optional
from dataclasses import dataclass

from llama_cpp import Llama
from .config import ZimBotConfig
from .zim_indexer import ZIMIndexer


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
        self.llm = None
        self.model_loaded = False
        
    def load_model(self, model_path: Optional[str] = None):
        """Load the LLM model."""
        if model_path is None:
            # Find the first .gguf file in the model path
            if not os.path.isdir(self.config.model_path):
                raise FileNotFoundError(f"Model directory does not exist: {self.config.model_path}")

            model_files = [f for f in os.listdir(self.config.model_path) if f.endswith('.gguf')]
            if not model_files:
                raise FileNotFoundError(f"No GGUF model files found in {self.config.model_path}")

            model_path = os.path.join(self.config.model_path, model_files[0])
            if len(model_files) > 1:
                print(f"Found {len(model_files)} models, using: {model_files[0]}")

        print(f"Loading model: {model_path}")
        file_size_gb = os.path.getsize(model_path) / (1024**3)
        print(f"  File size: {file_size_gb:.2f} GB")
        
        try:
            self.llm = Llama(
                model_path=model_path,
                n_threads=self.config.n_threads,
                n_ctx=self.config.context_window,
                n_batch=512,
                verbose=True  # Enable verbose to see llama.cpp errors
            )
            self.model_loaded = True
            print(f"Model loaded successfully!")

        except Exception as e:
            import traceback
            print(f"Failed to load model: {e}")
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
    
    def generate_response(self, question: str, conversation_history: str = "",
                          verbosity: str = "detailed") -> RAGResponse:
        """Generate a response to a question using RAG.

        Args:
            question: The user's question
            conversation_history: Formatted previous conversation for context
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

        # Step 2: Build prompt with verbosity preference
        prompt = self._build_prompt(question, context_chunks, sources, conversation_history, verbosity)

        # Debug: Echo full prompt to console
        print(f"\n{'='*60}")
        print("FULL PROMPT BEING SENT TO LLM:")
        print(f"{'='*60}")
        print(prompt)
        print(f"{'='*60}\n")

        # Step 3: Generate response using LLM
        print(f"Generating response with LLM...")
        llm_start = time.time()

        try:
            response = self.llm(
                prompt=prompt,
                max_tokens=self.config.max_tokens,
                temperature=0.5,
                top_p=0.9,
                echo=False,
                stop=["<|im_end|>", "<|im_start|>"]
            )

            llm_output = response['choices'][0]['text']
            tokens_used = response['usage']['total_tokens']
            print(f"  LLM inference took {time.time() - llm_start:.2f}s ({tokens_used} tokens)")

        except Exception as e:
            print(f"LLM generation failed: {e}")
            llm_output = f"Sorry, I encountered an error while processing your question: {str(e)}"
            tokens_used = 0

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
        """Get information about the loaded model."""
        if not self.model_loaded:
            return {"status": "not_loaded"}
        
        return {
            "model_path": self.llm.model_path,
            "n_ctx": self.llm.n_ctx,
            "n_threads": self.llm.n_threads,
            "model_loaded": self.model_loaded
        }
    
    def health_check(self) -> Dict:
        """Perform a health check of the RAG engine."""
        
        # Check if model is loaded
        model_status = "loaded" if self.model_loaded else "not_loaded"
        
        # Check indexer status
        indexer_info = self.indexer.get_collection_info()
        
        return {
            "model_status": model_status,
            "indexer_status": indexer_info,
            "config": {
                "max_tokens": self.config.max_tokens,
                "context_window": self.config.context_window,
                "retrieval_k": self.config.retrieval_k
            }
        }