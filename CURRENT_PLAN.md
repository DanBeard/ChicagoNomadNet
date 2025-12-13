# LXMF RAG Chatbot with ZIM Files

## Overview

Create an LXMF chatbot (`zimbot`) that uses a local LLM with Retrieval Augmented Generation (RAG) to answer questions using content from ZIM archives (Wikipedia, StackOverflow, etc.).

---

## Recommended Libraries & Models

### LLM Inference: `llama-cpp-python`
- Python bindings for llama.cpp - highly optimized CPU inference
- Simple API: `Llama(model_path=...)` then `llm(prompt)`
- Supports GGUF quantized models

### Vector Database: `ChromaDB`
- Lightweight, embedded (no server needed)
- Persistent storage to disk
- Simple API: `collection.add()`, `collection.query()`

### Embedding Model: `all-MiniLM-L6-v2`
- 22M parameters, very fast on CPU
- Auto-downloaded via sentence-transformers (~90MB)

### LLM Model Options (ranked):

| Model | Size (Q4) | RAM Needed | Quality | Speed |
|-------|-----------|------------|---------|-------|
| **Qwen2.5-1.5B-Instruct** | ~1GB | 4GB | Good | Medium |
| Qwen2.5-0.5B-Instruct | ~500MB | 2GB | Decent | Fast |
| TinyLlama-1.1B-Chat | ~700MB | 3GB | Decent | Medium |
| Llama-3.2-1B-Instruct | ~700MB | 3GB | Good | Medium |

**Recommendation:** Qwen2.5-1.5B for quality, Qwen2.5-0.5B for speed/low resources.

---

## Architecture

```
User (LXMF message)
        │
        v
┌───────────────────┐
│  ZimBot (LXMF)    │  ← LXMRouter, identity, message handling
└───────────────────┘
        │
        v
┌───────────────────┐
│   RAG Engine      │  ← Prompt building + LLM inference
└───────────────────┘
        │
        v
┌───────────────────┐
│  ZIM Indexer      │  ← ChromaDB vector search
└───────────────────┘
        │
        v
┌───────────────────┐
│  ChromaDB         │  ← Embedded vectors from ZIM content
└───────────────────┘
```

---

## File Structure

```
ChicagoNomadNet/
├── projects/
│   └── zimbot/
│       ├── __init__.py
│       ├── zimbot.py         # Main bot + LXMF handling
│       ├── rag_engine.py     # RAG pipeline
│       ├── zim_indexer.py    # ZIM extraction + ChromaDB
│       └── config.py         # Configuration dataclass
├── models/                   # GGUF model files (gitignored)
├── Dockerfile.zimbot         # Docker build for bot
└── docker-compose.yml        # Updated with zimbot service
```

---

## Implementation Plan

### Phase 1: Core Infrastructure
1. Create `projects/zimbot/` directory structure
2. Implement `config.py` - dataclass with env var configuration
3. Implement `zim_indexer.py`:
   - Load ZIM archives (pattern from zim_host.py)
   - Extract plain text from HTML entries
   - Chunk text (~800 chars with 100 char overlap)
   - Embed chunks with sentence-transformers
   - Store in ChromaDB with metadata (archive, path, title)
   - Hash-based change detection to skip re-indexing

### Phase 2: RAG Pipeline
4. Implement `rag_engine.py`:
   - Load GGUF model via llama-cpp-python
   - Query retrieval (top-k chunks from ChromaDB)
   - Prompt building (system + context + question)
   - Response generation with source attribution

### Phase 3: LXMF Bot
5. Implement `zimbot.py`:
   - Identity management (pattern from qr_rns.py)
   - LXMRouter setup with delivery callback
   - Message queue for async processing
   - Command handling: /help, /sources, /status
   - Response sending with retry logic
   - Periodic announce on network

### Phase 4: Docker Integration
6. Create `Dockerfile.zimbot`
7. Update `docker-compose.yml` with zimbot service
8. Add model download script
9. Update README with zimbot docs

---

## Key Design Decisions

### ZIM Indexing Strategy
- Index on first boot, skip if unchanged (hash check)
- Process one archive at a time (memory management)
- Filter to text/html entries only
- Chunk at sentence boundaries when possible

### RAG Pipeline
- Retrieve top 5 chunks per query
- Context window: 2048 tokens
- Max response: 512 tokens (LoRa bandwidth friendly)
- Include abbreviated source attribution

### Bot Behavior
- Announce every 30 minutes
- Commands: /help, /sources, /status
- "Warming up" message while indexing
- Concise responses for low bandwidth

---

## Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `ZIM_PATH` | `/zim/` | Path to ZIM files |
| `ZIMBOT_MODEL_PATH` | `./models/*.gguf` | GGUF model path |
| `ZIMBOT_CHROMADB_PATH` | `./chromadb_data` | Vector DB storage |
| `ZIMBOT_N_THREADS` | `4` | CPU threads |
| `ZIMBOT_MAX_TOKENS` | `512` | Max response length |
| `ZIMBOT_RETRIEVAL_K` | `5` | Chunks to retrieve |

---

## New Dependencies

```
llama-cpp-python>=0.2.50
chromadb>=0.4.0
sentence-transformers>=2.2.0
```

---

## Files to Create/Modify

**Create:**
- `projects/zimbot/__init__.py`
- `projects/zimbot/config.py`
- `projects/zimbot/zim_indexer.py`
- `projects/zimbot/rag_engine.py`
- `projects/zimbot/zimbot.py`
- `Dockerfile.zimbot`

**Modify:**
- `docker-compose.yml` - add zimbot service
- `.gitignore` - add models/, chromadb_data/
- `README.md` - add zimbot documentation

---

## Reference Files (patterns to follow)
- `projects/qr_rns.py` - LXMF bot pattern (identity, LXMRouter, message handling)
- `zim_host.py` - ZIM loading and content extraction with libzim

---

## User Choices (Confirmed)
- **Model:** Qwen2.5-1.5B-Instruct (Q4_K_M quantization, ~1GB)
- **Indexing:** Full indexing of all ZIM content
- **Deployment:** Separate Docker container
