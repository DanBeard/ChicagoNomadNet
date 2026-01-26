"""
ZimBot - LXMF RAG Chatbot

Main bot implementation that handles LXMF messaging, command processing,
and integration with the RAG engine.
"""

import os
import sys
import argparse
import asyncio
import time
import traceback
import threading
import sqlite3
import json
from typing import List, Dict, Optional, Tuple

import RNS
from LXMF import LXMessage, LXMRouter

from .config import ZimBotConfig, get_config
from .zim_indexer import ZIMIndexer
from .rag_engine import RAGEngine, RAGResponse
from .lazy_worker import EmbeddingWorkerManager
from .lazy_keywords import KeywordExtractor
from .user_state import UserStateManager, UserState, Mode
from .mode_handlers import (
    HelpModeHandler, ChatModeHandler,
    SearchModeHandler, OptionsModeHandler
)


class ConversationManager:
    """Manages per-user conversation history with SQLite storage."""

    def __init__(self, db_path: str, max_messages: int = 6, max_history_chars: int = 1500):
        self.db_path = db_path
        self.max_messages = max_messages
        self.max_history_chars = max_history_chars
        self._init_db()

    def _init_db(self):
        """Initialize the conversations table."""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                user_hash TEXT PRIMARY KEY,
                history TEXT NOT NULL DEFAULT '[]',
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def get_history(self, user_hash: str) -> List[Dict]:
        """Fetch conversation history for a user."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT history FROM conversations WHERE user_hash = ?",
            (user_hash,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row else []

    def add_exchange(self, user_hash: str, question: str, answer: str):
        """Add a question/answer exchange to history."""
        history = self.get_history(user_hash)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer[:2000]})  # Truncate very long answers

        # Compact if too many messages
        if len(history) > self.max_messages:
            history = self._compact(history)

        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            INSERT OR REPLACE INTO conversations (user_hash, history, last_updated)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (user_hash, json.dumps(history)))
        conn.commit()
        conn.close()

    def _compact(self, history: List[Dict]) -> List[Dict]:
        """Keep only the last N messages."""
        if len(history) <= self.max_messages:
            return history
        return history[-self.max_messages:]

    def get_messages_for_llm(self, user_hash: str) -> List[Dict]:
        """Get conversation history as chat messages for LLM.

        Returns the raw message list in OpenAI chat format:
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        """
        return self.get_history(user_hash)

    def clear_history(self, user_hash: str):
        """Clear conversation history for a user."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM conversations WHERE user_hash = ?",
            (user_hash,)
        )
        conn.commit()
        conn.close()


class ZimBot:
    """Main ZimBot class handling LXMF communication and RAG integration."""

    def __init__(self, config: Optional[ZimBotConfig] = None, rns_verbosity: int = 0):
        self.config = config or get_config()
        self.rns_verbosity = rns_verbosity  # 0=quiet, 4=default, 7=extreme

        # RNS/LXMF initialized later (after indexing completes)
        self.rns = None
        self.router = None
        self.identity = None
        self.source = None

        # Initialize components
        self.indexer = ZIMIndexer(self.config)
        self.rag_engine = RAGEngine(self.config, self.indexer)
        self.conversation_manager = ConversationManager(
            db_path=self.config.sqlite_db_path,
            max_messages=self.config.max_conversation_messages
        )

        # State management
        self.is_ready = False
        self.is_indexing = False
        self.last_announce = 0
        self.last_worker_check = 0

        # Message queues
        self._msg_queue = []
        self._response_queue = []

        # Lazy embedding components
        self.embedding_worker: Optional[EmbeddingWorkerManager] = None
        self.keyword_extractor: Optional[KeywordExtractor] = None

        # User state and mode handlers
        self.state_manager = UserStateManager(db_path=self.config.sqlite_db_path)
        self.mode_handlers = {}  # Initialized after components ready

        # Help text
        self.help_text = """Welcome to ZimBot!

I'm an offline AI assistant powered by ZIM archives (Wikipedia, StackOverflow, etc.).

MODES:
/chat - Ask questions, get AI-powered answers
/search - Quick search, returns titles + snippets
/options - Configure bot settings

COMMANDS:
/help - Show this message
/sources - List available archives
/status - Show bot status
/mode - Show current mode
/clear - Clear conversation history

Type /chat to start asking questions!"""
    
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
        if archive_names and not self.config.skip_startup_indexing:
            self.is_indexing = True
            print("Checking if indexing is needed...")

            try:
                # Use threaded indexer for speed (set ZIMBOT_INDEXER_WORKERS env var, default 4)
                indexing_result = self.indexer.index_archives_threaded()
                if indexing_result:
                    print("Indexing completed successfully.")
                else:
                    print("Using existing index (archives unchanged).")
            except Exception as e:
                print(f"Indexing failed: {e}")
                traceback.print_exc()
            finally:
                self.is_indexing = False
        elif self.config.skip_startup_indexing:
            print("Skipping startup indexing (ZIMBOT_SKIP_INDEXING=1)")

        # Load LLM model (fatal if fails)
        try:
            self.rag_engine.load_model()
            print("LLM model loaded successfully.")
        except Exception as e:
            print(f"\n{'='*60}")
            print(f"FATAL: Failed to load LLM model!")
            print(f"{'='*60}")
            print(f"Model path: {self.config.model_path}")
            print(f"Error: {e}")
            print(f"\nMake sure:")
            print(f"  1. ZIMBOT_MODEL_PATH points to a directory with .gguf files")
            print(f"  2. The .gguf file is a valid llama.cpp model")
            print(f"  3. You have enough RAM to load the model")
            print(f"{'='*60}\n")
            raise SystemExit(1)

        # Initialize lazy embedding components
        if self.config.lazy_embedding_enabled:
            print("Initializing lazy embedding system...")
            self.keyword_extractor = KeywordExtractor(
                max_keywords=self.config.lazy_keywords_count
            )
            self.embedding_worker = EmbeddingWorkerManager(self.config)
            self.embedding_worker.start()
            print("Lazy embedding worker started.")

        # Preload embedding model for fast first search
        print("Preloading embedding model for vector search...")
        try:
            self.indexer._init_model()
            print("Embedding model preloaded.")
        except Exception as e:
            print(f"Warning: Failed to preload embedding model: {e}")

        # Initialize mode handlers (after LLM is loaded)
        print("Initializing mode handlers...")
        self.mode_handlers = {
            Mode.HELP: HelpModeHandler(),
            Mode.CHAT: ChatModeHandler(self.rag_engine, self.conversation_manager),
            Mode.SEARCH: SearchModeHandler(self.indexer),
            Mode.OPTIONS: OptionsModeHandler(self.state_manager, self.rag_engine.llm),
        }
        print("Mode handlers initialized.")

        # Now initialize Reticulum and LXMF (after indexing is complete)
        print("Initializing Reticulum network stack...")
        # Set RNS log level before AND after Reticulum init (constructor may reset it)
        RNS.loglevel = self.rns_verbosity
        self.rns = RNS.Reticulum(verbosity=self.rns_verbosity)
        RNS.loglevel = self.rns_verbosity  # Re-apply in case constructor changed it
        self.router = LXMRouter(storagepath="./tmp_zimbot")
        RNS.loglevel = self.rns_verbosity  # Re-apply after LXMRouter too

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
        user_hash = reply_hash.hex()

        print(f"Received message from {user_hash}: {content}")

        # Get user state
        user_state = self.state_manager.get_state(user_hash)

        # Handle commands (work in any mode)
        if content.startswith("/"):
            response, new_mode = self._handle_command(content, user_state)
            if new_mode:
                self.state_manager.set_mode(user_hash, new_mode)
            self._response_queue.append((reply_hash, response))
            return

        # Route to mode handler
        handler = self.mode_handlers.get(user_state.mode)
        if handler:
            # CHAT mode needs async processing (uses LLM for RAG)
            if user_state.mode == Mode.CHAT:
                self._msg_queue.append((reply_hash, content, user_state))
            else:
                # Other modes can be processed synchronously
                # (SEARCH uses embeddings only, OPTIONS uses grammar-constrained LLM which is fast)
                result = handler.handle_message(content, user_state)
                self._response_queue.append((reply_hash, result.text))

                # Mark as onboarded after first non-help interaction
                if user_state.is_new_user and user_state.mode != Mode.HELP:
                    self.state_manager.mark_not_new(user_hash)
        else:
            # Fallback (shouldn't happen)
            self._response_queue.append((reply_hash, "Error: Unknown mode. Type /help for help."))
    
    def _handle_command(self, command: str, user_state: UserState) -> Tuple[str, Optional[Mode]]:
        """Handle bot commands. Returns (response_text, new_mode_or_None)."""
        parts = command.split()
        cmd = parts[0].lower() if parts else ""
        new_mode = None

        # Mode switching commands
        if cmd in ("/chat", "/c"):
            new_mode = Mode.CHAT
            response = "Switched to CHAT mode. Ask me anything!"

        elif cmd in ("/search", "/s"):
            new_mode = Mode.SEARCH
            response = "Switched to SEARCH mode. Enter search terms."

        elif cmd in ("/help", "/h", "/about"):
            new_mode = Mode.HELP
            handler = self.mode_handlers.get(Mode.HELP)
            if handler:
                response = handler.handle_message("", user_state).text
            else:
                response = self.help_text

        elif cmd in ("/options", "/settings", "/o"):
            new_mode = Mode.OPTIONS
            handler = self.mode_handlers.get(Mode.OPTIONS)
            if handler:
                response = handler.handle_message("", user_state).text
            else:
                response = "Options mode not available."

        elif cmd == "/back":
            # Return to chat from options
            if user_state.mode == Mode.OPTIONS:
                new_mode = Mode.CHAT
                response = "Back to CHAT mode."
            else:
                response = "Use /chat, /search, or /options to switch modes."

        # Options commands that work from OPTIONS mode
        elif cmd in ("/toggle", "/set", "/show") and user_state.mode == Mode.OPTIONS:
            handler = self.mode_handlers.get(Mode.OPTIONS)
            if handler:
                response = handler.handle_message(command, user_state).text
            else:
                response = "Options mode not available."

        # Info commands (don't change mode)
        elif cmd == "/sources":
            response = self._get_sources_info()

        elif cmd == "/status":
            response = self._get_status_info()

        elif cmd == "/mode":
            response = f"Current mode: {user_state.mode.value.upper()}"

        elif cmd == "/clear":
            self.conversation_manager.clear_history(user_state.user_hash)
            response = "Conversation history cleared. Starting fresh!"

        else:
            response = f"Unknown command: {cmd}. Type /help for available commands."

        return response, new_mode
    
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
        """Process queued CHAT mode questions using the RAG engine."""
        if not self.is_ready or self.is_indexing:
            return

        if not self.rag_engine.model_loaded:
            # Model not loaded - provide basic response
            for item in self._msg_queue:
                reply_hash = item[0]
                response = "Sorry, my AI model is not currently available. Please try again later."
                self._response_queue.append((reply_hash, response))
            self._msg_queue = []
            return

        # Process each question
        processed_questions = []
        chat_handler = self.mode_handlers.get(Mode.CHAT)

        for item in self._msg_queue:
            # Unpack: now includes user_state
            reply_hash, question, user_state = item
            user_hash = user_state.user_hash

            try:
                print(f"Processing question: {question}")

                # Get conversation history for this user (as proper chat messages)
                conversation_history = self.conversation_manager.get_messages_for_llm(user_hash)
                if conversation_history:
                    print(f"[Conv History] {len(conversation_history)} messages for user {user_hash[:8]}...")

                # Generate response using RAG (with conversation context and verbosity preference)
                verbosity = user_state.preferences.verbosity
                rag_response = self.rag_engine.generate_response(question, conversation_history, verbosity)

                # Format the response based on user preferences
                if chat_handler:
                    response = chat_handler.format_rag_response(rag_response, user_state.preferences)
                else:
                    response = rag_response.answer

                # Check if coverage was insufficient and trigger lazy embedding
                if not rag_response.coverage_sufficient and self.embedding_worker:
                    # Add acknowledgment to response
                    coverage_msg = ("\n\n[Note: I don't have much indexed on this topic yet. "
                                    "My knowledge will improve for future questions.]")
                    response += coverage_msg

                    # Extract keywords and queue for embedding (async - don't block response)
                    if self.keyword_extractor:
                        self._async_queue_keywords(question)

                # Save this exchange to conversation history
                self.conversation_manager.add_exchange(user_hash, question, rag_response.answer)

                # Mark as onboarded after first chat
                if user_state.is_new_user:
                    self.state_manager.mark_not_new(user_hash)

                self._response_queue.append((reply_hash, response))
                processed_questions.append(item)

            except Exception as e:
                error_msg = f"Sorry, I encountered an error processing your question: {str(e)}"
                self._response_queue.append((reply_hash, error_msg))
                print(f"Error processing question: {e}")
                traceback.print_exc()

        # Remove processed questions from queue
        for item in processed_questions:
            if item in self._msg_queue:
                self._msg_queue.remove(item)
    
    async def _send_responses(self):
        """Send queued responses."""
        sent_responses = []

        for reply_hash, text in self._response_queue:
            try:
                dest_id = RNS.Identity.recall(reply_hash)
                has_path = RNS.Transport.has_path(reply_hash)

                if dest_id is not None and has_path:
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
    
    def _async_queue_keywords(self, question: str):
        """Queue keywords for embedding in background thread (non-blocking)."""
        def _do_queue():
            try:
                keywords = self.keyword_extractor.extract_all(question)
                if keywords:
                    print(f"[Async] Queueing keywords: {keywords}")
                    self.embedding_worker.queue_keywords(keywords, priority=10)
            except Exception as e:
                print(f"[Async] Keyword extraction failed: {e}")

        thread = threading.Thread(target=_do_queue, daemon=True)
        thread.start()

    async def _periodic_announce(self):
        """Periodically announce the bot's presence."""
        now = time.time()
        if now - self.last_announce > self.config.announce_interval:
            print("Announcing ZimBot presence on the network...")
            self.router.announce(self.source.hash)
            self.last_announce = now
    
    async def _check_worker_health(self):
        """Periodically check and restart embedding worker if needed."""
        now = time.time()
        if now - self.last_worker_check < 60:  # Check every 60 seconds
            return

        self.last_worker_check = now

        if self.embedding_worker:
            self.embedding_worker.restart_if_dead()

            # Get and log status
            status = self.embedding_worker.get_status()
            if status:
                print(f"[Worker Status] embedded={status.get('articles_embedded', 0)}, "
                      f"queue={status.get('queue_depth', 0)}, "
                      f"cpu={status.get('cpu_avg', 0):.1f}%")

    def _shutdown_worker(self):
        """Gracefully shutdown the embedding worker."""
        if self.embedding_worker:
            print("Stopping embedding worker...")
            self.embedding_worker.stop()

    async def run(self):
        """Main run loop for the bot."""
        print("Starting ZimBot...")

        # Initialize components
        await self._initialize_components()

        # Main loop
        try:
            while True:
                try:
                    # Process questions
                    await self._process_questions()

                    # Send responses
                    await self._send_responses()

                    # Periodic announcement
                    await self._periodic_announce()

                    # Check embedding worker health
                    await self._check_worker_health()

                    # Small sleep to prevent CPU overload
                    await asyncio.sleep(1.0)

                except KeyboardInterrupt:
                    print("Shutting down ZimBot...")
                    break
                except Exception as e:
                    print(f"Error in main loop: {e}")
                    traceback.print_exc()
                    await asyncio.sleep(5.0)  # Wait before retrying
        finally:
            # Cleanup
            self._shutdown_worker()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="ZimBot - LXMF RAG Chatbot over Reticulum",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase Reticulum verbosity (can be repeated: -v, -vv, -vvv)"
    )
    parser.add_argument(
        "--rns-verbosity",
        type=int,
        choices=range(-1, 8),
        metavar="-1 to 7",
        help="Set exact Reticulum log level (-1=none, 0=critical, 4=info, 7=extreme)"
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Determine RNS verbosity: explicit --rns-verbosity takes precedence over -v count
    if args.rns_verbosity is not None:
        rns_verbosity = args.rns_verbosity
    else:
        # Map -v flags: -1=none(default), -v=4(info), -vv=5(verbose), -vvv=6(debug), -vvvv=7(extreme)
        # Using -1 (LOG_NONE) as default to completely suppress RNS logging
        verbosity_map = {0: -1, 1: 4, 2: 5, 3: 6}
        rns_verbosity = verbosity_map.get(args.verbose, 7)

    # Set RNS log level BEFORE any RNS initialization happens
    # This must be done early to suppress logs from shared instance connections
    RNS.loglevel = rns_verbosity
    if rns_verbosity < 0:
        print("RNS logging disabled (use -v to enable)")

    try:
        # Create and run the bot
        bot = ZimBot(rns_verbosity=rns_verbosity)

        # Run the bot
        asyncio.run(bot.run())

    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()