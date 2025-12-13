"""
RAG Engine

Handles the Retrieval Augmented Generation pipeline:
- Query retrieval from ChromaDB
- Prompt construction with context
- LLM inference using llama-cpp-python
- Response generation with source attribution
"""

import os
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
            model_files = [f for f in os.listdir(self.config.model_path) if f.endswith('.gguf')]
            if not model_files:
                raise FileNotFoundError(f"No GGUF model files found in {self.config.model_path}")
            model_path = os.path.join(self.config.model_path, model_files[0])
        
        print(f"Loading model: {model_path}")
        
        try:
            self.llm = Llama(
                model_path=model_path,
                n_threads=self.config.n_threads,
                n_ctx=self.config.context_window,
                n_batch=512,
                verbose=False
            )
            self.model_loaded = True
            print(f"Model loaded successfully: {self.llm.model_path}")
            
        except Exception as e:
            print(f"Failed to load model: {e}")
            self.model_loaded = False
            raise
    
    def _build_prompt(self, question: str, context_chunks: List[str], sources: List[Dict]) -> str:
        """Build the prompt for the LLM with context and question."""
        
        # Format sources for the prompt
        source_info = []
        for i, source in enumerate(sources, 1):
            archive = source['metadata'].get('archive', 'Unknown')
            title = source['metadata'].get('title', 'No title')
            source_info.append(f"[{i}] {archive}: {title}")
        
        sources_text = "\n".join(source_info) if source_info else "No specific sources"
        context_content = "\n\n".join(context_chunks)
        
        # Build the system prompt
        system_prompt = f"""You are ZimBot, an AI assistant that answers questions using information from ZIM archives (offline Wikipedia, StackOverflow, etc.).

Answer the question based only on the provided context. If you don't know the answer or the context doesn't contain relevant information, say you don't know.

Provide concise answers suitable for low-bandwidth communication. Keep responses under {self.config.max_tokens} tokens.

When you are done say: -End-

Here is the relevant context:

{sources_text}

Context content:
{context_content}

Question: {question}

Answer:"""
        
        return system_prompt
    
    def _format_response(self, llm_response: str, sources: List[Dict]) -> str:
        """Format the final response with source attribution."""
        
        # Clean up the response
        response = llm_response.strip()
        
        # Add source attribution
        if sources:
            source_citations = []
            for i, source in enumerate(sources, 1):
                archive = source['metadata'].get('archive', 'Unknown')
                title = source['metadata'].get('title', 'No title')
                path = source['metadata'].get('path', 'Unknown')
                source_citations.append(f"[{i}] {archive}: {title}")
            
            sources_text = "\nSources: " + ", ".join(source_citations)
            response = response + sources_text
        
        return response
    
    def generate_response(self, question: str) -> RAGResponse:
        """Generate a response to a question using RAG."""
        
        if not self.model_loaded:
            raise RuntimeError("Model not loaded. Call load_model() first.")
        
        start_time = time.time()
        
        # Step 1: Retrieve relevant context
        print(f"Retrieving context for question: {question}")
        context_results = self.indexer.search(question, k=self.config.retrieval_k)
        
        context_chunks = [result['content'] for result in context_results]
        sources = context_results
        
        # Step 2: Build prompt
        prompt = self._build_prompt(question, context_chunks, sources)
        
        # Step 3: Generate response using LLM
        print(f"Generating response with LLM...")
        
        try:
            response = self.llm(
                prompt=prompt,
                max_tokens=self.config.max_tokens,
                temperature=0.5,
                top_p=0.9,
                echo=False,
                stop=["\nQuestion:", "\nAnswer:", "Question:", "Answer:","-End-"]
            )
            
            llm_output = response['choices'][0]['text']
            tokens_used = response['usage']['total_tokens']
            
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
            processing_time=processing_time
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