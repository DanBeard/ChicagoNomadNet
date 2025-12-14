"""
ZimBot - LXMF RAG Chatbot

Main bot implementation that handles LXMF messaging, command processing,
and integration with the RAG engine.
"""

import os
import asyncio
import time
import traceback
from typing import List, Dict, Optional, Tuple

import RNS
from LXMF import LXMessage, LXMRouter

from .config import ZimBotConfig, get_config
from .zim_indexer import ZIMIndexer
from .rag_engine import RAGEngine, RAGResponse


class ZimBot:
    """Main ZimBot class handling LXMF communication and RAG integration."""
    
    def __init__(self, config: Optional[ZimBotConfig] = None):
        self.config = config or get_config()

        # RNS/LXMF initialized later (after indexing completes)
        self.rns = None
        self.router = None
        self.identity = None
        self.source = None

        # Initialize components
        self.indexer = ZIMIndexer(self.config)
        self.rag_engine = RAGEngine(self.config, self.indexer)

        # State management
        self.is_ready = False
        self.is_indexing = False
        self.last_announce = 0

        # Message queues
        self._msg_queue = []
        self._response_queue = []
        
        # Help text
        self.help_text = f"""ZimBot - Offline AI Assistant

Ask me questions about topics in the available ZIM archives. I use local AI models to provide answers.

Commands:
/help - Show this help message
/sources - Show information about available knowledge sources
/status - Show bot status and statistics

Example: "What is quantum computing?"

Note: Responses may take a few seconds as I search through offline archives."""
    
    def _setup_identity(self) -> RNS.Identity:
        """Set up or load the bot's identity."""
        # Ensure storage directory exists
        base_storage_dir = os.path.join("storage", "zimbot")
        os.makedirs(base_storage_dir, exist_ok=True)
        
        # Configure path to identity file
        identity_file = os.path.join(base_storage_dir, "identity")
        
        # Generate new identity if it doesn't exist
        if not os.path.exists(identity_file):
            identity = RNS.Identity(create_keys=True)
            with open(identity_file, "wb") as file:
                file.write(identity.get_private_key())
            print(f"ZimBot Identity <{identity.hash.hex()}> generated and saved.")
        else:
            # Load existing identity
            identity = RNS.Identity(create_keys=False)
            identity.load(identity_file)
            print(f"ZimBot Identity <{identity.hash.hex()}> loaded.")
        
        return identity
    
    async def _initialize_components(self):
        """Initialize all components (indexer, RAG engine, etc.)."""
        print("Initializing ZimBot components...")

        # Initialize ChromaDB
        self.indexer.initialize_chroma()

        # Load ZIM archives
        try:
            archive_names = self.indexer.load_archives()
            print(f"Loaded {len(archive_names)} ZIM archives: {', '.join(archive_names)}")
        except Exception as e:
            print(f"Failed to load ZIM archives: {e}")
            archive_names = []

        # Index archives if needed (do this BEFORE starting network)
        if archive_names:
            self.is_indexing = True
            print("Checking if indexing is needed...")

            try:
                indexing_result = self.indexer.index_archives()
                if indexing_result:
                    print("Indexing completed successfully.")
                else:
                    print("Using existing index (archives unchanged).")
            except Exception as e:
                print(f"Indexing failed: {e}")
                traceback.print_exc()
            finally:
                self.is_indexing = False

        # Load LLM model
        try:
            self.rag_engine.load_model()
            print("LLM model loaded successfully.")
        except Exception as e:
            print(f"Failed to load LLM model: {e}")
            # Continue without model - bot can still provide basic functionality

        # Now initialize Reticulum and LXMF (after indexing is complete)
        print("Initializing Reticulum network stack...")
        self.rns = RNS.Reticulum()
        self.router = LXMRouter(storagepath="./tmp_zimbot")

        # Set up identity
        self.identity = self._setup_identity()
        self.source = self.router.register_delivery_identity(
            self.identity,
            display_name=self.config.display_name
        )

        # Register message callback
        self.router.register_delivery_callback(self._on_message_received)

        self.is_ready = True
        print("ZimBot is ready!")

        # Initial announcement
        self.router.announce(self.source.hash)
        self.last_announce = time.time()
    
    def _on_message_received(self, message: LXMessage):
        """Callback for when a message is received."""
        reply_hash = message.source_hash
        content = message.content.decode().strip()
        
        print(f"Received message from {reply_hash.hex()}: {content}")
        
        # Handle commands
        if content.startswith("/"):
            self._handle_command(content, reply_hash)
        else:
            # Regular question - queue for processing
            self._msg_queue.append((reply_hash, content))
    
    def _handle_command(self, command: str, reply_hash: bytes):
        """Handle bot commands."""
        cmd = command.split()[0].lower() if command else ""
        
        if cmd == "/help":
            response = self.help_text
        elif cmd == "/sources":
            response = self._get_sources_info()
        elif cmd == "/status":
            response = self._get_status_info()
        else:
            response = f"Unknown command: {cmd}. Type /help for available commands."
        
        self._response_queue.append((reply_hash, response))
    
    def _get_sources_info(self) -> str:
        """Get information about available knowledge sources."""
        collection_info = self.indexer.get_collection_info()
        
        if collection_info.get("status") == "not_initialized":
            return "Knowledge sources not yet initialized."
        
        archives = collection_info.get("archives", [])
        doc_count = collection_info.get("count", 0)
        
        if not archives:
            return "No knowledge sources available."
        
        sources_text = "Available Knowledge Sources:\n\n"
        for i, archive in enumerate(archives, 1):
            sources_text += f"{i}. {archive}\n"
        
        sources_text += f"\nTotal indexed documents: {doc_count}"
        
        return sources_text
    
    def _get_status_info(self) -> str:
        """Get bot status information."""
        health = self.rag_engine.health_check()
        
        status_lines = [
            "=== ZimBot Status ===",
            f"Ready: {'Yes' if self.is_ready else 'No'}",
            f"Indexing: {'Yes' if self.is_indexing else 'No'}",
            f"Model: {health['model_status']}",
        ]
        
        if health['indexer_status'].get('count'):
            status_lines.extend([
                f"Indexed documents: {health['indexer_status']['count']}",
                f"Archives: {len(health['indexer_status']['archives'])}",
            ])
        
        status_lines.extend([
            f"Max response tokens: {health['config']['max_tokens']}",
            f"Retrieval chunks: {health['config']['retrieval_k']}",
        ])
        
        return "\n".join(status_lines)
    
    async def _process_questions(self):
        """Process queued questions using the RAG engine."""
        if not self.is_ready or self.is_indexing:
            return
        
        if not self.rag_engine.model_loaded:
            # Model not loaded - provide basic response
            for reply_hash, question in self._msg_queue:
                response = "Sorry, my AI model is not currently available. Please try again later."
                self._response_queue.append((reply_hash, response))
            self._msg_queue = []
            return
        
        # Process each question
        processed_questions = []
        
        for reply_hash, question in self._msg_queue:
            try:
                print(f"Processing question: {question}")
                
                # Generate response using RAG
                rag_response = self.rag_engine.generate_response(question)
                
                # Format the response
                response = f"{rag_response.answer}"
                
                # Add processing info
                response += f"\n\n({rag_response.processing_time:.1f}s, {rag_response.tokens_used} tkns)"
                
                self._response_queue.append((reply_hash, response))
                processed_questions.append((reply_hash, question))
                
            except Exception as e:
                error_msg = f"Sorry, I encountered an error processing your question: {str(e)}"
                self._response_queue.append((reply_hash, error_msg))
                print(f"Error processing question: {e}")
                traceback.print_exc()
        
        # Remove processed questions from queue
        for reply_hash, question in processed_questions:
            if (reply_hash, question) in self._msg_queue:
                self._msg_queue.remove((reply_hash, question))
    
    async def _send_responses(self):
        """Send queued responses."""
        sent_responses = []
        
        for reply_hash, text in self._response_queue:
            try:
                dest_id = RNS.Identity.recall(reply_hash)
                if dest_id is not None and RNS.Transport.has_path(reply_hash):
                    destination = RNS.Destination(dest_id, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
                    
                    lxm = LXMessage(
                        destination, 
                        self.source,
                        text,
                        "ZimBot Response",
                        desired_method=LXMessage.OPPORTUNISTIC
                    )
                    
                    self.router.handle_outbound(lxm)
                    print(f"Sent response to {reply_hash.hex()}")
                    sent_responses.append((reply_hash, text))
                else:
                    # No path available, request it
                    RNS.Transport.request_path(reply_hash)
                    
            except Exception as e:
                print(f"Failed to send response to {reply_hash.hex()}: {e}")
        
        # Remove sent responses from queue
        for reply_hash, text in sent_responses:
            if (reply_hash, text) in self._response_queue:
                self._response_queue.remove((reply_hash, text))
    
    async def _periodic_announce(self):
        """Periodically announce the bot's presence."""
        now = time.time()
        if now - self.last_announce > self.config.announce_interval:
            print("Announcing ZimBot presence on the network...")
            self.router.announce(self.source.hash)
            self.last_announce = now
    
    async def run(self):
        """Main run loop for the bot."""
        print("Starting ZimBot...")
        
        # Initialize components
        await self._initialize_components()
        
        # Main loop
        while True:
            try:
                # Process questions
                await self._process_questions()
                
                # Send responses
                await self._send_responses()
                
                # Periodic announcement
                await self._periodic_announce()
                
                # Small sleep to prevent CPU overload
                await asyncio.sleep(1.0)
                
            except KeyboardInterrupt:
                print("Shutting down ZimBot...")
                break
            except Exception as e:
                print(f"Error in main loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(5.0)  # Wait before retrying


def main():
    """Main entry point."""
    try:
        # Create and run the bot
        bot = ZimBot()
        
        # Run the bot
        asyncio.run(bot.run())
        
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()