"""
Mode Handlers

Implements behavior for each mode (HELP, CHAT, SEARCH, OPTIONS).
Each handler is a separate class for clean separation of concerns.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from llama_cpp import LlamaGrammar

if TYPE_CHECKING:
    from llama_cpp import Llama
    from .user_state import UserState, UserPreferences, UserStateManager
    from .rag_engine import RAGEngine
    from .zim_indexer import ZIMIndexer


@dataclass
class ModeResponse:
    """Response from a mode handler."""
    text: str
    new_mode: Optional[str] = None  # If mode should change


class BaseModeHandler(ABC):
    """Abstract base class for mode handlers."""

    @abstractmethod
    def handle_message(self, message: str, user_state: "UserState") -> ModeResponse:
        """Handle a user message in this mode."""
        pass


class HelpModeHandler(BaseModeHandler):
    """Handler for HELP mode - displays info and prompts mode selection."""

    WELCOME_TEXT = """Welcome to ZimBot!

I'm an offline AI assistant that answers questions using ZIM archives (Wikipedia, StackOverflow, etc.).

MODES:
/chat - Ask questions, get AI-powered answers with sources
/search - Quick search, returns titles + snippets (no AI)
/options - Configure bot settings

OTHER COMMANDS:
/help - Show this message
/sources - List available knowledge archives
/status - Show bot status

Type /chat to start asking questions!"""

    RETURNING_TEXT = """ZimBot Help

MODES:
/chat - Ask questions, get AI-powered answers
/search - Quick search, returns titles + snippets
/options - Configure settings

COMMANDS:
/help - Show this message
/sources - List knowledge archives
/status - Show bot status
/mode - Show current mode

Send a command to switch modes."""

    def handle_message(self, message: str, user_state: "UserState") -> ModeResponse:
        """Show help text for any non-command message."""
        if user_state.is_new_user:
            return ModeResponse(text=self.WELCOME_TEXT)
        return ModeResponse(text=self.RETURNING_TEXT)


class ChatModeHandler(BaseModeHandler):
    """Handler for CHAT mode - RAG-based Q&A.

    Note: This handler is used for formatting responses. The actual RAG
    processing happens in _process_questions() since it's async.
    """

    def __init__(self, rag_engine: "RAGEngine", conversation_manager):
        self.rag_engine = rag_engine
        self.conversation_manager = conversation_manager

    def handle_message(self, message: str, user_state: "UserState") -> ModeResponse:
        """Process question through RAG pipeline."""
        # Get conversation history
        user_hash = user_state.user_hash
        conversation_history = self.conversation_manager.format_for_prompt(user_hash)

        # Generate response
        rag_response = self.rag_engine.generate_response(message, conversation_history)

        # Format based on preferences
        response_text = self._format_response(rag_response, user_state.preferences)

        # Save to conversation history
        self.conversation_manager.add_exchange(user_hash, message, rag_response.answer)

        return ModeResponse(text=response_text)

    def _format_response(self, rag_response, preferences: "UserPreferences") -> str:
        """Format response based on user preferences."""
        text = rag_response.answer

        if preferences.show_sources and rag_response.sources:
            paths = [f"[{i}] {s['metadata'].get('path', '?')[:40]}"
                     for i, s in enumerate(rag_response.sources, 1)]
            text += "\nSrc: " + " ".join(paths)

        if preferences.show_stats:
            text += f"\n({rag_response.processing_time:.1f}s, {rag_response.tokens_used} tkns)"

        return text

    def format_rag_response(self, rag_response, preferences: "UserPreferences") -> str:
        """Public method to format a RAG response with given preferences."""
        return self._format_response(rag_response, preferences)


class SearchModeHandler(BaseModeHandler):
    """Handler for SEARCH mode - direct embedding search without LLM."""

    MAX_RESULTS = 5
    SNIPPET_LENGTH = 150

    def __init__(self, indexer: "ZIMIndexer"):
        self.indexer = indexer

    def handle_message(self, message: str, user_state: "UserState") -> ModeResponse:
        """Search embeddings directly and return results."""
        if not message.strip():
            return ModeResponse(text="Enter search terms to find relevant documents.")

        results = self.indexer.search(message, k=self.MAX_RESULTS)

        if not results:
            return ModeResponse(text="No results found. Try different search terms.")

        # Format results
        lines = [f"Search results for: {message}", ""]
        for i, result in enumerate(results, 1):
            title = result['metadata'].get('title', 'Untitled')[:50]
            path = result['metadata'].get('path', '')[:40]
            content = result['content'][:self.SNIPPET_LENGTH].replace('\n', ' ')

            lines.append(f"{i}. {title}")
            lines.append(f"   {path}")
            lines.append(f"   {content}...")
            lines.append("")

        return ModeResponse(text="\n".join(lines))


class OptionsModeHandler(BaseModeHandler):
    """Handler for OPTIONS mode - settings configuration.

    Supports two input methods:
    1. Direct commands: /toggle stats, /set verbosity concise, /show
    2. Natural language: Uses LLM with grammar constraints to parse intent
    """

    # GBNF grammar for constrained JSON output - supports array of changes
    GRAMMAR = r'''root ::= "{" ws "\"changes\":" ws changes ws "}"
changes ::= "[" ws "]" | "[" ws change (ws "," ws change)* ws "]"
change ::= "{" ws "\"action\":" ws action "," ws "\"setting\":" ws setting "," ws "\"value\":" ws value ws "}"
action ::= "\"toggle\"" | "\"set\"" | "\"show\""
setting ::= "\"stats\"" | "\"sources\"" | "\"verbosity\"" | "null"
value ::= "true" | "false" | "\"concise\"" | "\"detailed\"" | "null"
ws ::= [ \t\n]*'''

    def __init__(self, state_manager: "UserStateManager", llm: "Llama"):
        self.state_manager = state_manager
        self.llm = llm
        self.grammar = LlamaGrammar.from_string(self.GRAMMAR)

    def handle_message(self, message: str, user_state: "UserState") -> ModeResponse:
        """Handle options menu interaction."""
        msg = message.strip()

        # Direct commands take precedence
        if msg.startswith("/"):
            return self._handle_direct_command(msg, user_state)

        # Empty message or just entering options mode - show current settings
        if not msg:
            return ModeResponse(text=self._format_settings(user_state.preferences))

        # Natural language -> LLM with grammar constraint
        return self._handle_natural_language(msg, user_state)

    def _handle_direct_command(self, command: str, user_state: "UserState") -> ModeResponse:
        """Handle direct /toggle and /set commands."""
        parts = command.lower().split()
        cmd = parts[0]
        prefs = user_state.preferences

        if cmd == "/show":
            return ModeResponse(text=self._format_settings(prefs))

        if cmd == "/toggle" and len(parts) >= 2:
            setting = parts[1]
            if setting == "stats":
                prefs.show_stats = not prefs.show_stats
                self.state_manager.update_preferences(user_state.user_hash, prefs)
                status = "enabled" if prefs.show_stats else "disabled"
                return ModeResponse(text=f"Stats {status}.\n\n{self._format_settings(prefs)}")
            elif setting == "sources":
                prefs.show_sources = not prefs.show_sources
                self.state_manager.update_preferences(user_state.user_hash, prefs)
                status = "enabled" if prefs.show_sources else "disabled"
                return ModeResponse(text=f"Sources {status}.\n\n{self._format_settings(prefs)}")
            elif setting == "verbosity":
                prefs.verbosity = "concise" if prefs.verbosity == "detailed" else "detailed"
                self.state_manager.update_preferences(user_state.user_hash, prefs)
                return ModeResponse(text=f"Verbosity set to {prefs.verbosity}.\n\n{self._format_settings(prefs)}")

        if cmd == "/set" and len(parts) >= 3:
            setting = parts[1]
            value = parts[2]
            if setting == "verbosity" and value in ("concise", "detailed"):
                prefs.verbosity = value
                self.state_manager.update_preferences(user_state.user_hash, prefs)
                return ModeResponse(text=f"Verbosity set to {value}.\n\n{self._format_settings(prefs)}")
            elif setting == "stats" and value in ("on", "off", "true", "false"):
                prefs.show_stats = value in ("on", "true")
                self.state_manager.update_preferences(user_state.user_hash, prefs)
                status = "enabled" if prefs.show_stats else "disabled"
                return ModeResponse(text=f"Stats {status}.\n\n{self._format_settings(prefs)}")
            elif setting == "sources" and value in ("on", "off", "true", "false"):
                prefs.show_sources = value in ("on", "true")
                self.state_manager.update_preferences(user_state.user_hash, prefs)
                status = "enabled" if prefs.show_sources else "disabled"
                return ModeResponse(text=f"Sources {status}.\n\n{self._format_settings(prefs)}")

        return ModeResponse(text=f"Unknown command. Try /toggle stats, /set verbosity concise, or /show.\n\n{self._format_settings(prefs)}")

    def _handle_natural_language(self, message: str, user_state: "UserState") -> ModeResponse:
        """Use LLM with grammar constraint to interpret natural language settings request."""
        prefs = user_state.preferences

        prompt = f"""<|im_start|>system
Parse the user's request into settings changes. Output a JSON object with a "changes" array.
Each change has: action ("toggle"/"set"/"show"), setting ("stats"/"sources"/"verbosity"/null), value (true/false/"concise"/"detailed"/null).
Multiple changes can be in one request. Example: {{"changes":[{{"action":"set","setting":"verbosity","value":"concise"}},{{"action":"set","setting":"sources","value":false}}]}}
Current settings: stats={prefs.show_stats}, sources={prefs.show_sources}, verbosity={prefs.verbosity}<|im_end|>
<|im_start|>user
{message}<|im_end|>
<|im_start|>assistant
"""
        try:
            response = self.llm(prompt, grammar=self.grammar, max_tokens=200)
            json_str = response['choices'][0]['text']

            # Parse and apply all changes
            parsed = json.loads(json_str)
            return self._apply_changes(parsed.get("changes", []), user_state)

        except Exception as e:
            # Fallback to showing current settings
            print(f"Options LLM parsing failed: {e}")
            return ModeResponse(text=f"I didn't understand that. Here are your current settings:\n\n{self._format_settings(prefs)}")

    def _apply_changes(self, changes: list, user_state: "UserState") -> ModeResponse:
        """Apply an array of parsed settings changes."""
        prefs = user_state.preferences
        messages = []

        # If no changes or empty array, just show settings
        if not changes:
            return ModeResponse(text=self._format_settings(prefs))

        for change in changes:
            action = change.get("action")
            setting = change.get("setting")
            value = change.get("value")

            # Skip show actions or null settings in multi-change context
            if action == "show" or setting is None or setting == "null":
                continue

            if action == "toggle":
                if setting == "stats":
                    prefs.show_stats = not prefs.show_stats
                    status = "enabled" if prefs.show_stats else "disabled"
                    messages.append(f"Stats {status}.")
                elif setting == "sources":
                    prefs.show_sources = not prefs.show_sources
                    status = "enabled" if prefs.show_sources else "disabled"
                    messages.append(f"Sources {status}.")
                elif setting == "verbosity":
                    prefs.verbosity = "concise" if prefs.verbosity == "detailed" else "detailed"
                    messages.append(f"Verbosity set to {prefs.verbosity}.")

            elif action == "set":
                if setting == "stats":
                    prefs.show_stats = value is True or value == "true"
                    status = "enabled" if prefs.show_stats else "disabled"
                    messages.append(f"Stats {status}.")
                elif setting == "sources":
                    prefs.show_sources = value is True or value == "true"
                    status = "enabled" if prefs.show_sources else "disabled"
                    messages.append(f"Sources {status}.")
                elif setting == "verbosity" and value in ("concise", "detailed"):
                    prefs.verbosity = value
                    messages.append(f"Verbosity set to {value}.")

        # Persist all changes at once
        if messages:
            self.state_manager.update_preferences(user_state.user_hash, prefs)
            return ModeResponse(text=f"{' '.join(messages)}\n\n{self._format_settings(prefs)}")
        else:
            return ModeResponse(text=self._format_settings(prefs))

    def _format_settings(self, prefs: "UserPreferences") -> str:
        """Format the current settings display."""
        stats_status = "ON" if prefs.show_stats else "OFF"
        sources_status = "ON" if prefs.show_sources else "OFF"
        verbosity_status = prefs.verbosity.upper()

        return f"""=== ZimBot Options ===

Stats in responses: [{stats_status}]
Source citations: [{sources_status}]
Verbosity: [{verbosity_status}]

Commands: /toggle <setting>, /set <setting> <value>, /show
Or just describe what you want in natural language.

Type /chat or /search to exit options."""
