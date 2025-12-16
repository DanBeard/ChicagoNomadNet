"""
ZIM Indexer

Handles loading ZIM archives, extracting text content, chunking, embedding,
and storing in SQLite with sqlite-vec for vector similarity search.
"""

import os
import hashlib
import json
import re
import struct
import sqlite3
import threading
import queue
import time
from dataclasses import dataclass
from typing import List, Dict, Set, Tuple, Optional

import sqlite_vec
from sentence_transformers import SentenceTransformer

# zimfast is required - no fallback to slow zimscan
# Installed as standalone module via: pip install -e projects/zimbot/zimfast/
from zimfast import ZimReader as ZimFastReader

from .config import ZimBotConfig


@dataclass
class RawEntry:
    """Raw ZIM entry ready for text extraction."""
    content: bytes
    mime_type: Optional[str]
    url: str
    title: str
    archive_name: str
    entry_idx: int


@dataclass
class ProcessedChunk:
    """Processed chunk ready for embedding."""
    text: str
    metadata: dict
    doc_id: str


class ProgressTracker:
    """Thread-safe progress tracking for indexing."""

    def __init__(self):
        self.lock = threading.Lock()
        self.entries_read = 0
        self.entries_processed = 0
        self.chunks_created = 0
        self.chunks_embedded = 0
        self.start_time = time.time()
        self.last_report = 0

    def report(self, force: bool = False):
        """Print progress report (thread-safe)."""
        with self.lock:
            now = time.time()
            if force or (now - self.last_report) >= 10:  # Every 10 seconds
                elapsed = now - self.start_time
                rate = self.chunks_embedded / elapsed if elapsed > 0 else 0
                print(f"[{elapsed:.0f}s] Read: {self.entries_read}, "
                      f"Processed: {self.entries_processed}, "
                      f"Chunks: {self.chunks_created}, "
                      f"Embedded: {self.chunks_embedded} ({rate:.1f}/s)")
                self.last_report = now


def looks_like_text(content: bytes, sample_size: int = 1024) -> bool:
    """
    Heuristically detect if content is likely text.
    Checks for: valid UTF-8, no null bytes, mostly printable chars.
    """
    sample = content[:sample_size]

    # Null bytes are a strong indicator of binary content
    if b'\x00' in sample:
        return False

    # Try to decode as UTF-8
    try:
        decoded = sample.decode('UTF-8')
        # Check that most characters are printable or whitespace
        printable_count = sum(1 for c in decoded if c.isprintable() or c.isspace())
        ratio = printable_count / len(decoded) if decoded else 0
        return ratio > 0.85  # 85% printable threshold
    except UnicodeDecodeError:
        return False


def detect_mime_type(content: bytes, path: str = "") -> str | None:
    """
    Detect mime type from content and path when mime_type is None.
    Returns detected mime type or None if not text.
    """
    path_lower = path.lower()

    # Check file extension first
    if path_lower.endswith(('.html', '.htm')):
        return "text/html"
    elif path_lower.endswith(('.txt', '.md', '.rst', '.css', '.js', '.json', '.xml', '.csv')):
        return "text/plain"

    # Content-based detection
    if looks_like_text(content):
        stripped = content.lstrip()
        if stripped.startswith((b'<!DOCTYPE', b'<html', b'<HTML', b'<?xml', b'<head', b'<body')):
            return "text/html"
        return "text/plain"

    return None


class ZIMIndexer:
    """Main class for indexing ZIM archives into SQLite with sqlite-vec."""

    # Embedding dimension for all-MiniLM-L6-v2
    EMBEDDING_DIM = 384

    def __init__(self, config: ZimBotConfig):
        self.config = config
        self.conn: Optional[sqlite3.Connection] = None
        self.archive_paths: List[str] = []
        self.archive_names: List[str] = []

        # Threading state
        self.model: Optional[SentenceTransformer] = None
        self.raw_queue: Optional[queue.Queue] = None
        self.chunk_queue: Optional[queue.Queue] = None
        self.shutdown_event = threading.Event()
        self.reader_done = threading.Event()
        self.workers_done = threading.Event()
        self.progress: Optional[ProgressTracker] = None
        self._db_lock = threading.Lock()  # For thread-safe DB writes
        
    def initialize_chroma(self):
        """Initialize SQLite database with sqlite-vec extension.

        Note: Method name kept for backward compatibility, but now uses SQLite.
        """
        # Ensure parent directory exists
        db_dir = os.path.dirname(self.config.sqlite_db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # Initialize SQLite connection
        self.conn = sqlite3.connect(self.config.sqlite_db_path, check_same_thread=False)
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)

        # Create tables
        self.conn.executescript(f'''
            -- Main documents table
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                archive TEXT NOT NULL,
                path TEXT,
                title TEXT,
                mimetype TEXT,
                chunk INTEGER,
                total_chunks INTEGER
            );

            -- Index for archive-based operations
            CREATE INDEX IF NOT EXISTS idx_documents_archive ON documents(archive);

            -- Virtual table for vector similarity search
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(
                doc_id TEXT PRIMARY KEY,
                embedding float[{self.EMBEDDING_DIM}]
            );
        ''')
        self.conn.commit()
        print(f"SQLite database initialized: {self.config.sqlite_db_path}")
        
    def load_archives(self) -> List[str]:
        """Load ZIM archives from the configured path."""
        if not os.path.exists(self.config.zim_path):
            raise FileNotFoundError(f"ZIM path not found: {self.config.zim_path}")

        filenames = [f for f in os.listdir(self.config.zim_path) if f.endswith(".zim")]

        for filename in sorted(filenames):
            filepath = os.path.join(self.config.zim_path, filename)
            name = filename[:-4]  # Remove .zim extension

            if os.path.isfile(filepath):
                self.archive_paths.append(filepath)
                self.archive_names.append(name)
                print(f"Found ZIM archive: {name}")

        return self.archive_names
    
    def _extract_text_from_html(self, html_content: str) -> str:
        """Extract clean text from HTML content."""
        # Remove script and style tags
        text = re.sub(r'<(script|style).*?>.*?</\1>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove all HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Remove excessive newlines and special characters
        text = re.sub(r'[\n\r\t]+', ' ', text)
        
        return text
    
    def _chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
        """Split text into chunks with overlap."""
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            # Try to find a clean break point (sentence boundary)
            if end < len(text):
                # Look for sentence boundaries in the last 20% of the chunk
                search_start = max(start, end - (chunk_size // 5))
                search_region = text[search_start:end]
                
                # Priority: period+space > newline > any whitespace
                break_points = [
                    search_region.rfind('. '),
                    search_region.rfind('\n'),
                    search_region.rfind(' ')
                ]
                
                for bp in break_points:
                    if bp > 0:
                        end = search_start + bp + 1
                        break
            
            chunk = text[start:end]
            chunks.append(chunk)
            
            # Move start forward by chunk_size - overlap
            start = end - overlap
            
            # Ensure we make progress even if overlap is larger than chunk
            if start <= 0:
                start = end
        
        return chunks
    
    def _get_archive_hashes(self) -> Dict[str, str]:
        """Get a hash for each archive individually."""
        hashes = {}
        for i, filepath in enumerate(self.archive_paths):
            if os.path.exists(filepath):
                stat = os.stat(filepath)
                hash_obj = hashlib.sha256()
                hash_obj.update(f"{filepath}:{stat.st_mtime}:{stat.st_size}".encode())
                hashes[self.archive_names[i]] = hash_obj.hexdigest()
        return hashes

    def _get_hash_file_path(self) -> str:
        """Get path to store archive hashes (next to the SQLite DB)."""
        db_path = self.config.sqlite_db_path
        return db_path.replace('.db', '_hashes.json') if db_path.endswith('.db') else db_path + '_hashes.json'

    def _load_stored_hashes(self) -> Dict[str, str]:
        """Load stored per-archive hashes from disk."""
        hash_file = self._get_hash_file_path()

        if os.path.exists(hash_file):
            with open(hash_file, 'r') as f:
                return json.load(f)

        # Check for old ChromaDB hash files and migrate
        old_chromadb_path = "./chromadb_data"
        old_hash_file = os.path.join(old_chromadb_path, ".archive_hashes.json")
        if os.path.exists(old_hash_file):
            print("Migrating hash file from old ChromaDB location...")
            with open(old_hash_file, 'r') as f:
                return json.load(f)

        return {}

    def _store_archive_hashes(self):
        """Store per-archive hashes to disk."""
        current_hashes = self._get_archive_hashes()
        hash_file = self._get_hash_file_path()
        with open(hash_file, 'w') as f:
            json.dump(current_hashes, f, indent=2)

    def _get_changed_archives(self) -> Tuple[Set[str], Set[str], Set[str]]:
        """
        Compare stored hashes with current hashes.
        Returns: (new_archives, changed_archives, removed_archives)
        """
        current_hashes = self._get_archive_hashes()
        stored_hashes = self._load_stored_hashes()

        current_names = set(current_hashes.keys())
        stored_names = set(stored_hashes.keys())

        new_archives = current_names - stored_names
        removed_archives = stored_names - current_names

        # Check for changed archives (same name but different hash)
        changed_archives = set()
        for name in current_names & stored_names:
            if current_hashes[name] != stored_hashes[name]:
                changed_archives.add(name)

        return new_archives, changed_archives, removed_archives

    def _delete_archive_documents(self, archive_name: str):
        """Delete all documents belonging to a specific archive."""
        if not self.conn:
            return
        try:
            with self._db_lock:
                # Get doc IDs to delete from vector table
                cursor = self.conn.execute(
                    "SELECT id FROM documents WHERE archive = ?",
                    (archive_name,)
                )
                doc_ids = [row[0] for row in cursor.fetchall()]

                # Delete from vector table
                if doc_ids:
                    placeholders = ','.join('?' * len(doc_ids))
                    self.conn.execute(
                        f"DELETE FROM vec_documents WHERE doc_id IN ({placeholders})",
                        doc_ids
                    )

                # Delete from documents table
                self.conn.execute(
                    "DELETE FROM documents WHERE archive = ?",
                    (archive_name,)
                )
                self.conn.commit()

            print(f"  Deleted {len(doc_ids)} documents for archive: {archive_name}")
        except Exception as e:
            print(f"  Failed to delete documents for {archive_name}: {e}")

    def index_archives_simple(self, force_reindex: bool = False) -> bool:
        """Simple single-threaded indexing using zimfast. No threading complexity."""
        if not self.archive_paths:
            print("No archives loaded to index.")
            return False

        # Determine which archives need reindexing
        new_archives, changed_archives, removed_archives = self._get_changed_archives()

        if force_reindex:
            archives_to_index = set(self.archive_names)
            if self.conn:
                try:
                    with self._db_lock:
                        self.conn.execute("DELETE FROM vec_documents")
                        self.conn.execute("DELETE FROM documents")
                        self.conn.commit()
                    print("Force reindex: cleared entire database.")
                except Exception as e:
                    print(f"Failed to clear database: {e}")
        else:
            archives_to_index = new_archives | changed_archives

            if not archives_to_index and not removed_archives:
                print("All archives unchanged, skipping reindexing.")
                return False

            for archive_name in changed_archives | removed_archives:
                self._delete_archive_documents(archive_name)

        if new_archives:
            print(f"New archives: {', '.join(sorted(new_archives))}")
        if changed_archives:
            print(f"Changed archives: {', '.join(sorted(changed_archives))}")
        if removed_archives:
            print(f"Removed archives: {', '.join(sorted(removed_archives))}")

        if not archives_to_index:
            self._store_archive_hashes()
            print("Indexing complete (removals only).")
            return True

        # Load embedding model
        if self.model is None:
            print(f"Loading embedding model: {self.config.embedding_model}")
            self.model = SentenceTransformer(self.config.embedding_model)
            print("Model loaded.")

        print(f"Indexing {len(archives_to_index)} archive(s)...")

        for i, filepath in enumerate(self.archive_paths):
            archive_name = self.archive_names[i]
            if archive_name not in archives_to_index:
                continue

            self._index_single_archive_simple(filepath, archive_name)

        self._store_archive_hashes()
        print("All indexing complete.")
        return True

    def _index_single_archive_simple(self, filepath: str, archive_name: str):
        """Index a single archive - simple sequential loop."""
        import sys
        import os
        debug = os.environ.get('ZIMBOT_DEBUG', '0') == '1'

        def dbg(msg):
            if debug:
                print(f"  [DBG] {msg}", flush=True)

        print(f"Processing: {archive_name}", flush=True)

        reader = ZimFastReader(filepath)
        total_entries = reader.entry_count()
        print(f"  Total entries: {total_entries}", flush=True)

        documents = []
        metadatas = []
        ids = []
        total_chunks = 0
        text_count = 0
        last_report = time.time()

        for idx in range(total_entries):
            # Progress every 10 seconds
            now = time.time()
            if now - last_report >= 10:
                pct = idx / total_entries * 100
                print(f"  [{pct:.1f}%] Entry {idx}/{total_entries}, "
                      f"{text_count} text, {total_chunks} chunks", flush=True)
                last_report = now

            # Get entry
            dbg(f"idx={idx}: get_entry")
            result = reader.get_entry(idx)
            if result is None:  # Redirect or error
                continue

            dbg(f"idx={idx}: unpack tuple")
            path, title, content, mime_type = result

            # Skip non-text
            if mime_type and not mime_type.startswith('text/'):
                continue

            # Detect mime type if not set
            if not mime_type:
                mime_type = detect_mime_type(content, path or "")
                if mime_type is None:
                    continue

            text_count += 1
            dbg(f"idx={idx}: text entry #{text_count}, path={path[:50]}, size={len(content)}, mime={mime_type}")

            # Extract text
            try:
                dbg(f"idx={idx}: decode content")
                if mime_type == 'text/html':
                    text = self._extract_text_from_html(
                        content.decode('UTF-8', errors='ignore'))
                else:
                    text = content.decode('UTF-8', errors='ignore')

                dbg(f"idx={idx}: decoded text len={len(text)}")

                if len(text) <= 50:
                    continue

                # Chunk
                dbg(f"idx={idx}: chunking")
                chunks = self._chunk_text(text, self.config.chunk_size,
                                          self.config.chunk_overlap)
                dbg(f"idx={idx}: got {len(chunks)} chunks")

                for chunk_idx, chunk_text in enumerate(chunks):
                    doc_id = f"{archive_name}_{idx}_{chunk_idx}"
                    documents.append(chunk_text)
                    metadatas.append({
                        "archive": archive_name,
                        "path": path or "",
                        "title": title or "",
                        "mimetype": mime_type,
                        "chunk": chunk_idx,
                        "total_chunks": len(chunks)
                    })
                    ids.append(doc_id)

                dbg(f"idx={idx}: batch size now {len(documents)}")

                # Batch insert when we have enough
                if len(documents) >= self.config.embed_batch_size:
                    dbg(f"idx={idx}: ADDING BATCH TO CHROMA ({len(documents)} docs)")
                    self._add_batch_to_chroma(documents, metadatas, ids)
                    dbg(f"idx={idx}: batch added successfully")
                    total_chunks += len(documents)
                    documents, metadatas, ids = [], [], []

            except Exception as e:
                dbg(f"idx={idx}: EXCEPTION: {e}")
                continue  # Skip bad entries

        # Final batch
        if documents:
            self._add_batch_to_chroma(documents, metadatas, ids)
            total_chunks += len(documents)

        print(f"  Done: {text_count} text entries, {total_chunks} chunks indexed")

    def _add_batch_to_chroma(self, documents: List[str], metadatas: List[dict], ids: List[str]):
        """Add a batch of documents to SQLite with pre-computed embeddings.

        Note: Method name kept for backward compatibility, but now uses SQLite.
        """
        debug = os.environ.get('ZIMBOT_DEBUG', '0') == '1'

        if debug:
            print(f"    [BATCH] Starting batch of {len(documents)} docs", flush=True)
            print(f"    [BATCH] Doc lengths: min={min(len(d) for d in documents)}, max={max(len(d) for d in documents)}, avg={sum(len(d) for d in documents)//len(documents)}", flush=True)

        # Pre-compute embeddings
        if debug:
            print(f"    [BATCH] Calling model.encode()...", flush=True)

        embeddings = self.model.encode(
            documents,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True
        )

        if debug:
            print(f"    [BATCH] model.encode() done, shape={embeddings.shape}", flush=True)
            print(f"    [BATCH] Inserting into SQLite...", flush=True)

        # Insert into SQLite with thread safety - use executemany for performance
        with self._db_lock:
            # Prepare batch data for documents table
            doc_rows = [
                (
                    ids[i],
                    documents[i],
                    metadatas[i].get('archive', ''),
                    metadatas[i].get('path', ''),
                    metadatas[i].get('title', ''),
                    metadatas[i].get('mimetype', ''),
                    metadatas[i].get('chunk', 0),
                    metadatas[i].get('total_chunks', 1)
                )
                for i in range(len(ids))
            ]

            # Prepare batch data for vector table
            vec_rows = [
                (ids[i], struct.pack(f'{len(embeddings[i])}f', *embeddings[i]))
                for i in range(len(ids))
            ]

            # Batch insert documents
            self.conn.executemany('''
                INSERT OR REPLACE INTO documents
                (id, content, archive, path, title, mimetype, chunk, total_chunks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', doc_rows)

            # Batch insert vectors
            self.conn.executemany('''
                INSERT OR REPLACE INTO vec_documents (doc_id, embedding)
                VALUES (?, ?)
            ''', vec_rows)

            self.conn.commit()

        if debug:
            print(f"    [BATCH] SQLite insert complete ({len(documents)} docs)", flush=True)

    def search(self, query: str, k: int = 5) -> List[Dict]:
        """Search the indexed content using vector similarity."""
        if not self.conn:
            raise RuntimeError("Database not initialized. Call initialize_chroma() first.")

        # Ensure model is loaded for query embedding
        if self.model is None:
            print(f"Loading embedding model for search: {self.config.embedding_model}")
            self.model = SentenceTransformer(self.config.embedding_model)

        # Generate query embedding
        query_embedding = self.model.encode(
            [query],
            normalize_embeddings=True
        )[0]

        # Convert to binary format
        query_bytes = struct.pack(f'{len(query_embedding)}f', *query_embedding)

        # Vector similarity search with sqlite-vec
        results = self.conn.execute('''
            SELECT
                d.id,
                d.content,
                d.archive,
                d.path,
                d.title,
                d.mimetype,
                d.chunk,
                d.total_chunks,
                v.distance
            FROM vec_documents v
            JOIN documents d ON d.id = v.doc_id
            WHERE v.embedding MATCH ? AND v.k = ?
            ORDER BY v.distance
        ''', (query_bytes, k)).fetchall()

        formatted_results = []
        for row in results:
            formatted_results.append({
                'content': row[1],
                'metadata': {
                    'archive': row[2],
                    'path': row[3],
                    'title': row[4],
                    'mimetype': row[5],
                    'chunk': row[6],
                    'total_chunks': row[7]
                },
                'distance': row[8],
                'id': row[0]
            })

        return formatted_results
    
    def get_collection_info(self) -> Dict:
        """Get information about the current database."""
        if not self.conn:
            return {"status": "not_initialized"}

        count = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        return {
            "count": count,
            "archives": self.archive_names,
            "config": {
                "chunk_size": self.config.chunk_size,
                "chunk_overlap": self.config.chunk_overlap,
                "retrieval_k": self.config.retrieval_k
            }
        }

    # ========== Threaded Indexing Methods ==========

    def _init_model(self):
        """Initialize sentence-transformer model (lazy loading)."""
        if self.model is None:
            print(f"Loading embedding model: {self.config.embedding_model}")
            self.model = SentenceTransformer(self.config.embedding_model)
            print("Model loaded.")

    def _reader_thread_zimfast(self, filepath: str, archive_name: str,
                                 start_idx: int, end_idx: int, reader_id: int):
        """Reader thread using zimfast - reads a range of entries by cluster index."""
        text_entries = 0
        skipped_non_text = 0
        skipped_redirect = 0
        last_status = time.time()
        current_idx = start_idx

        try:
            print(f"  [Reader-{reader_id}] Opening ZIM file...", flush=True)
            reader = ZimFastReader(filepath)
            print(f"  [Reader-{reader_id}] Started: indices {start_idx}-{end_idx} ({end_idx-start_idx} entries)", flush=True)

            for idx in range(start_idx, end_idx):
                if self.shutdown_event.is_set():
                    break

                current_idx = idx
                result = reader.get_entry(idx)

                if result is None:  # Redirect or error
                    skipped_redirect += 1
                    continue

                path, title, content, mime_type = result

                # Skip non-text content
                if mime_type and not mime_type.startswith('text/'):
                    skipped_non_text += 1
                    continue

                # Detect mime type if not set
                if not mime_type:
                    mime_type = detect_mime_type(content, path or "")
                    if mime_type is None:
                        continue

                # Queue for processing
                entry = RawEntry(
                    content=content,
                    mime_type=mime_type,
                    url=path or "",
                    title=title or "",
                    archive_name=archive_name,
                    entry_idx=idx
                )

                # Blocks if queue is full (backpressure)
                self.raw_queue.put(entry)
                text_entries += 1

                if self.progress:
                    with self.progress.lock:
                        self.progress.entries_read += 1

                # Status every 5 seconds
                now = time.time()
                if now - last_status >= 5:
                    progress_pct = (idx - start_idx) / (end_idx - start_idx) * 100
                    qsize = self.raw_queue.qsize() if self.raw_queue else 0
                    print(f"  [Reader-{reader_id}] {progress_pct:.1f}% idx={idx}, "
                          f"text={text_entries}, skip={skipped_redirect+skipped_non_text}, "
                          f"queue={qsize}", flush=True)
                    last_status = now

        except Exception as e:
            print(f"  [Reader-{reader_id}] Error at idx {current_idx}: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            print(f"  [Reader-{reader_id}] Done: {text_entries} text, "
                  f"{skipped_redirect} redirects, {skipped_non_text} non-text skipped", flush=True)

    def _worker_thread(self, worker_id: int):
        """Worker thread - text extraction and chunking."""
        processed_count = 0
        chunks_count = 0
        last_status = time.time()

        print(f"  [Worker-{worker_id}] Started", flush=True)

        while not self.shutdown_event.is_set():
            try:
                entry = self.raw_queue.get(timeout=1.0)
            except queue.Empty:
                if self.reader_done.is_set() and self.raw_queue.empty():
                    break
                continue

            if entry is None:  # Sentinel
                break

            # Status every 5 seconds
            now = time.time()
            if now - last_status >= 5:
                cqsize = self.chunk_queue.qsize() if self.chunk_queue else 0
                print(f"  [Worker-{worker_id}] processed={processed_count}, chunks={chunks_count}, chunk_queue={cqsize}", flush=True)
                last_status = now

            try:
                # Extract text
                if entry.mime_type == 'text/html':
                    text = self._extract_text_from_html(
                        entry.content.decode('UTF-8', errors='ignore')
                    )
                else:
                    text = entry.content.decode('UTF-8', errors='ignore')

                if len(text) <= 50:
                    continue

                # Chunk text
                chunks = self._chunk_text(
                    text,
                    self.config.chunk_size,
                    self.config.chunk_overlap
                )

                # Queue chunks for embedding
                for chunk_idx, chunk_text in enumerate(chunks):
                    doc_id = f"{entry.archive_name}_{entry.entry_idx}_{chunk_idx}"
                    processed = ProcessedChunk(
                        text=chunk_text,
                        metadata={
                            "archive": entry.archive_name,
                            "path": entry.url,
                            "title": entry.title,
                            "mimetype": entry.mime_type,
                            "chunk": chunk_idx,
                            "total_chunks": len(chunks)
                        },
                        doc_id=doc_id
                    )
                    self.chunk_queue.put(processed)  # Blocks if full

                processed_count += 1
                chunks_count += len(chunks)

                if self.progress:
                    with self.progress.lock:
                        self.progress.entries_processed += 1
                        self.progress.chunks_created += len(chunks)

                # Worker status every 30 seconds (only worker 0 reports)
                if worker_id == 0:
                    now = time.time()
                    if now - last_status >= 30:
                        print(f"  [Workers] processed: {self.progress.entries_processed}, "
                              f"chunks: {self.progress.chunks_created}, "
                              f"chunk_queue: {self.chunk_queue.qsize()}/{self.config.chunk_queue_size}")
                        last_status = now

            except Exception as e:
                # Skip bad entries silently
                pass

        print(f"  [Worker-{worker_id}] Done: {processed_count} entries, {chunks_count} chunks", flush=True)

    def _flush_batch(self, batch: List[ProcessedChunk]):
        """Embed and store a batch of chunks to SQLite."""
        if not batch:
            return

        texts = [chunk.text for chunk in batch]
        metadatas = [chunk.metadata for chunk in batch]
        ids = [chunk.doc_id for chunk in batch]

        # Pre-compute embeddings with sentence-transformers
        print(f"    [flush] Encoding {len(texts)} texts...", flush=True)
        embeddings = self.model.encode(
            texts,
            batch_size=64,  # Internal batch for model
            show_progress_bar=False,
            normalize_embeddings=True  # For cosine similarity
        )
        print(f"    [flush] Encoding done, shape={embeddings.shape}", flush=True)

        # Add to SQLite with pre-computed embeddings - use executemany for performance
        print(f"    [flush] Adding to SQLite...", flush=True)

        with self._db_lock:
            # Prepare batch data for documents table
            doc_rows = [
                (
                    ids[i],
                    texts[i],
                    metadatas[i].get('archive', ''),
                    metadatas[i].get('path', ''),
                    metadatas[i].get('title', ''),
                    metadatas[i].get('mimetype', ''),
                    metadatas[i].get('chunk', 0),
                    metadatas[i].get('total_chunks', 1)
                )
                for i in range(len(ids))
            ]

            # Prepare batch data for vector table
            vec_rows = [
                (ids[i], struct.pack(f'{len(embeddings[i])}f', *embeddings[i]))
                for i in range(len(ids))
            ]

            # Batch insert documents
            self.conn.executemany('''
                INSERT OR REPLACE INTO documents
                (id, content, archive, path, title, mimetype, chunk, total_chunks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', doc_rows)

            # Batch insert vectors
            self.conn.executemany('''
                INSERT OR REPLACE INTO vec_documents (doc_id, embedding)
                VALUES (?, ?)
            ''', vec_rows)

            self.conn.commit()

        print(f"    [flush] SQLite add done", flush=True)

        if self.progress:
            print(f"    [flush] Updating progress...", flush=True)
            with self.progress.lock:
                self.progress.chunks_embedded += len(batch)
                self.progress.report()
            print(f"    [flush] Progress updated, returning to embedder loop", flush=True)

    def _embedder_thread(self):
        """Embedder thread - batch embed and store to SQLite."""
        batch: List[ProcessedChunk] = []
        last_flush = time.time()
        last_status = time.time()
        batches_flushed = 0
        first_chunk_received = False

        print("  [Embedder] Started, waiting for chunks...", flush=True)

        while not self.shutdown_event.is_set():
            try:
                chunk = self.chunk_queue.get(timeout=0.5)
            except queue.Empty:
                # Flush on timeout if we have pending chunks
                if batch and (time.time() - last_flush) >= self.config.embed_timeout:
                    print(f"  [Embedder] Timeout flush: {len(batch)} chunks", flush=True)
                    self._flush_batch(batch)
                    batches_flushed += 1
                    batch = []
                    last_flush = time.time()

                # Status update every 5 seconds
                now = time.time()
                if now - last_status >= 5:
                    embedded = self.progress.chunks_embedded if self.progress else 0
                    print(f"  [Embedder] batches={batches_flushed}, pending={len(batch)}, "
                          f"embedded={embedded}, queue={self.chunk_queue.qsize()}", flush=True)
                    last_status = now

                # Check if all workers are done
                if self.workers_done.is_set() and self.chunk_queue.empty():
                    break
                continue

            if chunk is None:  # Sentinel
                break

            if not first_chunk_received:
                print(f"  [Embedder] First chunk received!", flush=True)
                first_chunk_received = True

            batch.append(chunk)

            # Flush when batch is full
            if len(batch) >= self.config.embed_batch_size:
                print(f"  [Embedder] Flushing batch of {len(batch)} chunks...", flush=True)
                self._flush_batch(batch)
                batches_flushed += 1
                batch = []
                last_flush = time.time()
                print(f"  [Embedder] Batch flushed, continuing loop (queue={self.chunk_queue.qsize()})...", flush=True)

        # Final flush
        if batch:
            print(f"  [Embedder] Final flush: {len(batch)} chunks")
            self._flush_batch(batch)
            batches_flushed += 1

        print(f"  [Embedder] Done: {batches_flushed} batches flushed")

    def index_archive_threaded(self, filepath: str, archive_name: str):
        """Index a single archive using threaded pipeline."""
        # Initialize model if needed
        self._init_model()

        # Reset threading state
        self.shutdown_event.clear()
        self.reader_done.clear()
        self.workers_done.clear()
        self.raw_queue = queue.Queue(maxsize=self.config.raw_queue_size)
        self.chunk_queue = queue.Queue(maxsize=self.config.chunk_queue_size)
        self.progress = ProgressTracker()

        print(f"Starting threaded indexing: {archive_name}")
        print(f"  Readers: {self.config.indexer_readers}, "
              f"Workers: {self.config.indexer_workers}, "
              f"Batch size: {self.config.embed_batch_size}")

        # Use zimfast with configurable number of readers
        temp_reader = ZimFastReader(filepath)
        total_entries = temp_reader.entry_count()
        num_readers = self.config.indexer_readers
        entries_per_reader = (total_entries + num_readers - 1) // num_readers

        print(f"  Entries: {total_entries}, {num_readers} reader(s)")

        readers = []
        for i in range(num_readers):
            start_idx = i * entries_per_reader
            end_idx = min(start_idx + entries_per_reader, total_entries)
            if start_idx >= total_entries:
                break

            r = threading.Thread(
                target=self._reader_thread_zimfast,
                args=(filepath, archive_name, start_idx, end_idx, i),
                daemon=True
            )
            r.start()
            readers.append(r)

        # Start worker pool (text extraction + chunking)
        workers = []
        for i in range(self.config.indexer_workers):
            w = threading.Thread(
                target=self._worker_thread,
                args=(i,),
                daemon=True
            )
            w.start()
            workers.append(w)

        # Start embedder thread
        embedder = threading.Thread(
            target=self._embedder_thread,
            daemon=True
        )
        embedder.start()

        # Wait for all readers to finish
        for r in readers:
            r.join()

        self.reader_done.set()

        # Signal workers to finish (send sentinels)
        for _ in range(self.config.indexer_workers):
            self.raw_queue.put(None)

        # Wait for all workers
        for w in workers:
            w.join()

        self.workers_done.set()

        # Signal embedder to finish
        self.chunk_queue.put(None)
        embedder.join()

        # Final progress report
        self.progress.report(force=True)
        print(f"Threaded indexing complete for {archive_name}")

    def index_archives_threaded(self, force_reindex: bool = False) -> bool:
        """Index all loaded archives using threaded pipeline."""
        if not self.archive_paths:
            print("No archives loaded to index.")
            return False

        # Determine which archives need reindexing
        new_archives, changed_archives, removed_archives = self._get_changed_archives()

        if force_reindex:
            archives_to_index = set(self.archive_names)
            if self.conn:
                try:
                    with self._db_lock:
                        count = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                        if count > 0:
                            self.conn.execute("DELETE FROM vec_documents")
                            self.conn.execute("DELETE FROM documents")
                            self.conn.commit()
                            print("Force reindex: cleared entire database.")
                except Exception as e:
                    print(f"Failed to clear database: {e}")
        else:
            archives_to_index = new_archives | changed_archives

            if not archives_to_index and not removed_archives:
                print("All archives unchanged, skipping reindexing.")
                return False

            # Delete documents for changed/removed archives
            for archive_name in changed_archives | removed_archives:
                self._delete_archive_documents(archive_name)

        if new_archives:
            print(f"New archives: {', '.join(sorted(new_archives))}")
        if changed_archives:
            print(f"Changed archives: {', '.join(sorted(changed_archives))}")
        if removed_archives:
            print(f"Removed archives: {', '.join(sorted(removed_archives))}")

        if not archives_to_index:
            self._store_archive_hashes()
            print("Indexing complete (removals only).")
            return True

        print(f"Indexing {len(archives_to_index)} archive(s) using threaded pipeline...")

        # Process each archive with threaded pipeline
        for i, filepath in enumerate(self.archive_paths):
            archive_name = self.archive_names[i]
            if archive_name not in archives_to_index:
                continue

            self.index_archive_threaded(filepath, archive_name)

        self._store_archive_hashes()
        print("All archives indexed.")
        return True