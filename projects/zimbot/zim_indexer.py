"""
ZIM Indexer

Handles loading ZIM archives, extracting text content, chunking, embedding,
and storing in ChromaDB vector database.
"""

import os
import hashlib
import json
import re
from typing import List, Dict, Set, Tuple

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
import zimscan

from .config import ZimBotConfig


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
    """Main class for indexing ZIM archives into ChromaDB."""
    
    def __init__(self, config: ZimBotConfig):
        self.config = config
        self.chroma_client = None
        self.collection = None
        self.embedding_function = None
        self.archive_paths: List[str] = []
        self.archive_names: List[str] = []
        
    def initialize_chroma(self):
        """Initialize ChromaDB client and collection."""
        # Create directory if it doesn't exist
        os.makedirs(self.config.chromadb_path, exist_ok=True)
        
        # Initialize ChromaDB client
        self.chroma_client = chromadb.PersistentClient(
            path=self.config.chromadb_path,
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True
            )
        )
        
        # Set up embedding function
        self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=self.config.embedding_model
        )
        
        # Get or create collection
        self.collection = self.chroma_client.get_or_create_collection(
            name="zim_content",
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"}
        )
        
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

    def _load_stored_hashes(self) -> Dict[str, str]:
        """Load stored per-archive hashes from disk."""
        hash_file = os.path.join(self.config.chromadb_path, ".archive_hashes.json")
        old_hash_file = os.path.join(self.config.chromadb_path, ".archive_hash")

        if os.path.exists(hash_file):
            with open(hash_file, 'r') as f:
                return json.load(f)

        # Backward compatibility: if old single-hash file exists, return empty
        # to trigger full reindex and migration to new format
        if os.path.exists(old_hash_file):
            print("Migrating from single-hash to per-file hash format...")
            os.remove(old_hash_file)

        return {}

    def _store_archive_hashes(self):
        """Store per-archive hashes to disk."""
        current_hashes = self._get_archive_hashes()
        hash_file = os.path.join(self.config.chromadb_path, ".archive_hashes.json")
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
        if not self.collection:
            return
        try:
            self.collection.delete(where={"archive": archive_name})
            print(f"  Deleted documents for archive: {archive_name}")
        except Exception as e:
            print(f"  Failed to delete documents for {archive_name}: {e}")
    
    def index_archives(self, force_reindex: bool = False) -> bool:
        """Index all loaded archives into ChromaDB with selective reindexing."""
        if not self.archive_paths:
            print("No archives loaded to index.")
            return False

        # Determine which archives need reindexing
        new_archives, changed_archives, removed_archives = self._get_changed_archives()

        if force_reindex:
            # Force reindex all archives
            archives_to_index = set(self.archive_names)
            # Delete all documents
            if self.collection:
                try:
                    self.collection.delete(where={})
                    print("Force reindex: cleared entire collection.")
                except Exception as e:
                    print(f"Failed to clear collection: {e}")
        else:
            archives_to_index = new_archives | changed_archives

            if not archives_to_index and not removed_archives:
                print("All archives unchanged, skipping reindexing.")
                return False

            # Delete documents for changed and removed archives
            for archive_name in changed_archives | removed_archives:
                self._delete_archive_documents(archive_name)

        if new_archives:
            print(f"New archives: {', '.join(sorted(new_archives))}")
        if changed_archives:
            print(f"Changed archives: {', '.join(sorted(changed_archives))}")
        if removed_archives:
            print(f"Removed archives: {', '.join(sorted(removed_archives))}")

        if not archives_to_index:
            # Only removals, no indexing needed
            self._store_archive_hashes()
            print("Indexing complete (removals only).")
            return True

        print(f"Indexing {len(archives_to_index)} archive(s)...")

        total_chunks = 0

        for i, filepath in enumerate(self.archive_paths):
            archive_name = self.archive_names[i]

            if archive_name not in archives_to_index:
                continue  # Skip unchanged archives

            print(f"Processing: {archive_name}")

            entry_count = 0
            text_count = 0
            documents = []
            metadatas = []
            ids = []

            try:
                with zimscan.Reader(open(filepath, "rb"), skip_metadata=True) as reader:
                    for record in reader:
                        entry_count += 1

                        # Determine mime type (detect if None)
                        mime_type = record.mime_type
                        content = None

                        if mime_type and mime_type.startswith('text/'):
                            # Known text type
                            pass
                        elif mime_type is None:
                            # Try to detect from content
                            content = record.read()
                            mime_type = detect_mime_type(content, record.url or "")
                            if mime_type is None:
                                continue  # Not text content
                        else:
                            continue  # Non-text mime type

                        text_count += 1

                        try:
                            if content is None:
                                content = record.read()

                            # Decode and extract text
                            if mime_type == 'text/html':
                                text = self._extract_text_from_html(
                                    content.decode('UTF-8', errors='ignore'))
                            else:
                                text = content.decode('UTF-8', errors='ignore')

                            if len(text) <= 50:
                                continue

                            # Chunk and add to batch
                            chunks = self._chunk_text(text, self.config.chunk_size,
                                                      self.config.chunk_overlap)

                            for chunk_idx, chunk in enumerate(chunks):
                                doc_id = f"{archive_name}_{text_count}_{chunk_idx}"
                                documents.append(chunk)
                                metadatas.append({
                                    "archive": archive_name,
                                    "path": record.url or "",
                                    "title": record.title or "",
                                    "mimetype": mime_type,
                                    "chunk": chunk_idx,
                                    "total_chunks": len(chunks)
                                })
                                ids.append(doc_id)

                                # Batch insert to ChromaDB
                                if len(documents) >= 100:
                                    self.collection.add(
                                        documents=documents,
                                        metadatas=metadatas,
                                        ids=ids
                                    )
                                    total_chunks += len(documents)
                                    documents, metadatas, ids = [], [], []

                        except Exception as e:
                            continue  # Skip bad entries

                        # Progress every 10k entries
                        if entry_count % 10000 == 0:
                            print(f"  {entry_count} entries, {text_count} text...")

            except Exception as e:
                print(f"Failed to scan {archive_name}: {e}")
                raise

            # Add remaining documents
            if documents:
                self.collection.add(documents=documents, metadatas=metadatas, ids=ids)
                total_chunks += len(documents)

            print(f"  Done: {entry_count} entries, {text_count} text")

        self._store_archive_hashes()
        print(f"Indexing complete. Total chunks: {total_chunks}")
        return True
    
    def search(self, query: str, k: int = 5) -> List[Dict]:
        """Search the indexed content."""
        if not self.collection:
            raise RuntimeError("Collection not initialized. Call initialize_chroma() first.")
        
        results = self.collection.query(
            query_texts=[query],
            n_results=k
        )
        
        formatted_results = []
        
        for i in range(len(results['ids'][0])):
            formatted_results.append({
                'content': results['documents'][0][i],
                'metadata': results['metadatas'][0][i],
                'distance': results['distances'][0][i],
                'id': results['ids'][0][i]
            })
        
        return formatted_results
    
    def get_collection_info(self) -> Dict:
        """Get information about the current collection."""
        if not self.collection:
            return {"status": "not_initialized"}
        
        return {
            "count": self.collection.count(),
            "archives": self.archive_names,
            "config": {
                "chunk_size": self.config.chunk_size,
                "chunk_overlap": self.config.chunk_overlap,
                "retrieval_k": self.config.retrieval_k
            }
        }