"""
ZIM Indexer

Handles loading ZIM archives, extracting text content, chunking, embedding,
and storing in ChromaDB vector database.
"""

import os
import hashlib
import re
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from urllib.parse import unquote

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from libzim.reader import Archive
from libzim.search import Query, Searcher

from .config import ZimBotConfig


class ZIMIndexer:
    """Main class for indexing ZIM archives into ChromaDB."""
    
    def __init__(self, config: ZimBotConfig):
        self.config = config
        self.chroma_client = None
        self.collection = None
        self.embedding_function = None
        self.archives: List[Archive] = []
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
            
            try:
                archive = Archive(filepath)
                self.archives.append(archive)
                self.archive_names.append(name)
                print(f"Loaded ZIM archive: {name}")
            except Exception as e:
                print(f"Failed to load {filename}: {e}")
        
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
    
    def _get_archive_hash(self) -> str:
        """Get a hash representing the current state of all archives."""
        hash_obj = hashlib.sha256()
        
        for archive_name in sorted(self.archive_names):
            filepath = os.path.join(self.config.zim_path, archive_name + ".zim")
            if os.path.exists(filepath):
                # Hash the file modification time and size
                stat = os.stat(filepath)
                hash_obj.update(f"{archive_name}:{stat.st_mtime}:{stat.st_size}".encode())
        
        return hash_obj.hexdigest()
    
    def _should_reindex(self) -> bool:
        """Check if reindexing is needed based on archive changes."""
        current_hash = self._get_archive_hash()
        
        # Check if we have a stored hash
        hash_file = os.path.join(self.config.chromadb_path, ".archive_hash")
        
        if os.path.exists(hash_file):
            with open(hash_file, 'r') as f:
                stored_hash = f.read().strip()
            
            return current_hash != stored_hash
        
        return True  # No hash file means first run
    
    def _store_archive_hash(self):
        """Store the current archive hash to avoid unnecessary reindexing."""
        current_hash = self._get_archive_hash()
        hash_file = os.path.join(self.config.chromadb_path, ".archive_hash")
        
        with open(hash_file, 'w') as f:
            f.write(current_hash)
    
    def index_archives(self, force_reindex: bool = False) -> bool:
        """Index all loaded archives into ChromaDB."""
        if not self.archives:
            print("No archives loaded to index.")
            return False
        
        # Check if reindexing is needed
        if not force_reindex and not self._should_reindex():
            print("Archives unchanged, skipping reindexing.")
            return False
        
        print(f"Starting indexing of {len(self.archives)} archives...")
        
        # Clear existing collection if reindexing
        if self.collection:
            self.collection.delete(where={})
            print("Cleared existing collection.")
        
        total_documents = 0
        
        for i, archive in enumerate(self.archives):
            archive_name = self.archive_names[i]
            print(f"Processing archive {i+1}/{len(self.archives)}: {archive_name}")
            
            # Get all entries (this can be memory intensive for large archives)
            # We'll process them in batches
            entry_count = 0
            
            # Process entries in batches to manage memory
            batch_size = 1000
            processed_entries = 0
            
            # Get all paths from the archive
            all_paths = []
            try:
                # Try to get all paths (this might be memory intensive)
                all_paths = list(archive.iter_by_path())
            except:
                # Fallback: use search to get paths
                query = Query().set_query("")  # Empty query returns all
                searcher = Searcher(archive)
                search = searcher.search(query)
                all_paths = list(search.getResults(0, search.getEstimatedMatches()))
            
            print(f"Found {len(all_paths)} entries in {archive_name}")
            
            # Process entries in batches
            for batch_start in range(0, len(all_paths), batch_size):
                batch_end = min(batch_start + batch_size, len(all_paths))
                batch_paths = all_paths[batch_start:batch_end]
                
                documents = []
                metadatas = []
                ids = []
                
                for path_idx, path in enumerate(batch_paths):
                    try:
                        entry = archive.get_entry_by_path(path)
                        item = entry.get_item()
                        
                        # Only process text/html content
                        if item.mimetype == "text/html":
                            content = bytes(item.content)
                            html = content.decode("UTF-8", errors='ignore')
                            
                            # Extract clean text
                            text = self._extract_text_from_html(html)
                            
                            if len(text) > 50:  # Minimum length threshold
                                # Chunk the text
                                chunks = self._chunk_text(text, self.config.chunk_size, self.config.chunk_overlap)
                                
                                for chunk_idx, chunk in enumerate(chunks):
                                    doc_id = f"{archive_name}_{path_idx}_{chunk_idx}"
                                    
                                    documents.append(chunk)
                                    metadatas.append({
                                        "archive": archive_name,
                                        "path": path,
                                        "title": item.title,
                                        "chunk": chunk_idx,
                                        "total_chunks": len(chunks)
                                    })
                                    ids.append(doc_id)
                                    
                                    if len(documents) >= 100:  # Batch size for ChromaDB
                                        self.collection.add(
                                            documents=documents,
                                            metadatas=metadatas,
                                            ids=ids
                                        )
                                        documents = []
                                        metadatas = []
                                        ids = []
                        
                        entry_count += 1
                        
                        if entry_count % 100 == 0:
                            print(f"  Processed {entry_count} entries...")
                            
                    except Exception as e:
                        print(f"Error processing {path}: {e}")
                        continue
                
                # Add any remaining documents
                if documents:
                    self.collection.add(
                        documents=documents,
                        metadatas=metadatas,
                        ids=ids
                    )
                
                processed_entries += len(batch_paths)
                print(f"  Batch completed: {processed_entries}/{len(all_paths)} entries")
            
            total_documents += entry_count
            print(f"Completed {archive_name}: {entry_count} entries processed")
        
        # Store the current archive hash
        self._store_archive_hash()
        
        print(f"Indexing complete. Total documents indexed: {total_documents}")
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