# ZimBot Improvements - Next Steps

## Task 1: Fix SmolLM3-3B Stop Token

### Problem
SmolLM3-3B uses **ChatML format** with `<|im_start|>` and `<|im_end|>` tokens. Current code uses custom "-End-" which the model doesn't recognize.

### Solution
Update `rag_engine.py` to use proper ChatML format:

```python
def _build_prompt(self, question: str, context_chunks: List[str], sources: List[Dict],
                  conversation_history: str = "") -> str:
    """Build ChatML-formatted prompt for SmolLM3."""

    sources_text = "\n".join(f"[{i}] {s['metadata'].get('path', '?')}"
                             for i, s in enumerate(sources, 1))
    context_content = "\n\n".join(context_chunks)

    # ChatML format for SmolLM3
    prompt = f"""<|im_start|>system
You are ZimBot, an AI assistant answering from ZIM archives (offline Wikipedia, etc.).
Answer based only on the provided context. Be concise for low-bandwidth.
{conversation_history}<|im_end|>
<|im_start|>user
Context:
{context_content}

Sources: {sources_text}

Question: {question}<|im_end|>
<|im_start|>assistant
"""
    return prompt
```

**Stop tokens to use:**
```python
stop=["<|im_end|>", "<|im_start|>"]
```

---

## Task 2: Shorter Source Citations

### Problem
Current: `[1] wikipedia_en: Quantum Computing` (too verbose)
Wanted: `[1] A/Quantum_Computing` (just path)

### Solution
In `_format_response()`:

```python
def _format_response(self, llm_response: str, sources: List[Dict]) -> str:
    response = llm_response.strip()

    if sources:
        # Compact format: just path, no title
        paths = [f"[{i}] {s['metadata'].get('path', '?')[:40]}"
                 for i, s in enumerate(sources, 1)]
        response += "\nSrc: " + " ".join(paths)

    return response
```

Example output: `Src: [1] A/Quantum [2] A/Qubit [3] A/Superpos`

---

## Task 3: Per-User Conversation History

### Design

#### Storage (SQLite table in zimbot.db)
```sql
CREATE TABLE IF NOT EXISTS conversations (
    user_hash TEXT PRIMARY KEY,
    history TEXT NOT NULL,  -- JSON array of {role, content, timestamp}
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER DEFAULT 0
);
```

#### Compaction Strategy
- Keep last N messages (e.g., 6)
- When > N messages, summarize older ones into a single "context" message
- Max history size: ~500 tokens to keep responses snappy

#### Flow
1. On message received: load user history from DB
2. Pass history to `generate_response()`
3. Add user question + bot answer to history
4. If history > threshold, compact it
5. Save updated history to DB

#### Implementation in `zimbot.py`

```python
class ConversationManager:
    def __init__(self, db_path: str, max_messages: int = 6, max_history_chars: int = 1500):
        self.db_path = db_path
        self.max_messages = max_messages
        self.max_history_chars = max_history_chars
        self._init_db()

    def _init_db(self):
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
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT history FROM conversations WHERE user_hash = ?",
            (user_hash,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row else []

    def add_exchange(self, user_hash: str, question: str, answer: str):
        history = self.get_history(user_hash)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer[:500]})  # Truncate long answers

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
        # Keep only last N messages, summarize the rest
        if len(history) <= self.max_messages:
            return history

        # Simple compaction: just keep recent messages
        # Could add LLM summarization later if needed
        return history[-self.max_messages:]

    def format_for_prompt(self, user_hash: str) -> str:
        history = self.get_history(user_hash)
        if not history:
            return ""

        lines = ["Previous conversation:"]
        for msg in history[-4:]:  # Last 4 messages only for prompt
            role = "User" if msg["role"] == "user" else "Bot"
            content = msg["content"][:200]  # Truncate for prompt
            lines.append(f"{role}: {content}")

        return "\n".join(lines) + "\n"
```

---

## Files to Modify

1. **`projects/zimbot/rag_engine.py`**
   - Update `_build_prompt()` to use ChatML format
   - Update stop tokens to `["<|im_end|>", "<|im_start|>"]`
   - Update `_format_response()` for shorter sources
   - Add `conversation_history` parameter

2. **`projects/zimbot/zimbot.py`**
   - Add `ConversationManager` class
   - Initialize in `__init__`
   - Load history before processing question
   - Save history after response

3. **`projects/zimbot/config.py`**
   - Add `max_conversation_messages` config option

---

## Testing Checklist

- [ ] Model stops properly at `<|im_end|>`
- [ ] Sources show paths only, not titles
- [ ] Second message from same user includes context from first
- [ ] History doesn't grow unbounded
- [ ] Old conversations get compacted
