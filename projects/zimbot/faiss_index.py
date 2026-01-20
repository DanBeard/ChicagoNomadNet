"""
FAISS Index Wrapper

Provides fast approximate nearest neighbor search using Facebook's FAISS library.
Uses IVF (Inverted File Index) for efficient search on large vector collections.
"""

import os
import json
from typing import List, Tuple, Optional

import faiss
import numpy as np


class FAISSIndex:
    """
    FAISS index wrapper for fast vector similarity search.

    Uses IndexIVFFlat with inner product (for normalized vectors = cosine similarity).
    """

    def __init__(self, dim: int = 384,
                 index_path: str = "./zimbot_faiss.index",
                 id_map_path: str = "./zimbot_faiss_ids.json"):
        """
        Initialize FAISS index.

        Args:
            dim: Embedding dimension (384 for all-MiniLM-L6-v2)
            index_path: Path to save/load FAISS index
            id_map_path: Path to save/load ID mapping (int index -> doc_id string)
        """
        self.dim = dim
        self.index_path = index_path
        self.id_map_path = id_map_path
        self.index: Optional[faiss.Index] = None
        self.id_map: List[str] = []  # Maps FAISS internal int ID -> doc_id string
        self.is_trained = False
        self.nprobe = 64  # Number of clusters to search (accuracy vs speed tradeoff)

    def load(self) -> bool:
        """
        Load existing index from disk.

        Returns:
            True if index was loaded successfully, False if files don't exist
        """
        if os.path.exists(self.index_path) and os.path.exists(self.id_map_path):
            print(f"Loading FAISS index from {self.index_path}...")
            self.index = faiss.read_index(self.index_path)

            with open(self.id_map_path, 'r') as f:
                self.id_map = json.load(f)

            self.is_trained = self.index.is_trained
            print(f"FAISS index loaded: {self.index.ntotal} vectors, {len(self.id_map)} IDs")
            return True
        return False

    def create(self, nlist: int = 4096, m: int = 48, use_pq: bool = True):
        """
        Create a new IVF index (requires training before use).

        Args:
            nlist: Number of clusters. Rule of thumb: sqrt(N) to 4*sqrt(N)
            m: Number of subquantizers for PQ (must divide dim evenly).
               Higher = more accurate but more memory. 48 is good for dim=384.
            use_pq: If True, use Product Quantization (compressed, ~2GB for 43M vectors).
                    If False, use Flat (uncompressed, ~66GB for 43M vectors).
        """
        # Use inner product metric - for normalized vectors this equals cosine similarity
        quantizer = faiss.IndexFlatIP(self.dim)

        if use_pq:
            # IndexIVFPQ: compressed index, much lower memory
            # m=48 means 48 subquantizers, each 8 bits = 48 bytes per vector
            # For dim=384, m must divide evenly: 384/48 = 8 dims per subquantizer
            self.index = faiss.IndexIVFPQ(quantizer, self.dim, nlist, m, 8, faiss.METRIC_INNER_PRODUCT)
            print(f"Created FAISS IVF-PQ index: dim={self.dim}, nlist={nlist}, m={m} (compressed)")
        else:
            # IndexIVFFlat: uncompressed, needs ~66GB RAM for 43M vectors
            self.index = faiss.IndexIVFFlat(quantizer, self.dim, nlist, faiss.METRIC_INNER_PRODUCT)
            print(f"Created FAISS IVF-Flat index: dim={self.dim}, nlist={nlist} (uncompressed)")

        self.id_map = []
        self.is_trained = False

    def train(self, vectors: np.ndarray):
        """
        Train the IVF index with sample vectors.

        Must be called before adding vectors. Use a representative sample
        (50k-500k vectors is usually enough).

        Args:
            vectors: Training vectors, shape (n_samples, dim), dtype float32
        """
        if self.index is None:
            raise RuntimeError("Index not created. Call create() first.")

        if self.index.is_trained:
            print("Index already trained, skipping.")
            return

        # Ensure correct dtype and shape
        vectors = np.ascontiguousarray(vectors.astype(np.float32))

        print(f"Training FAISS index with {len(vectors)} vectors...")
        self.index.train(vectors)
        self.is_trained = True
        print("Training complete.")

    def add(self, vectors: np.ndarray, doc_ids: List[str]):
        """
        Add vectors with their corresponding document IDs.

        Args:
            vectors: Vectors to add, shape (n, dim), dtype float32
            doc_ids: List of document ID strings, same length as vectors
        """
        if self.index is None:
            raise RuntimeError("Index not created. Call create() first.")

        if not self.index.is_trained:
            raise RuntimeError("Index not trained. Call train() first.")

        if len(vectors) != len(doc_ids):
            raise ValueError(f"vectors ({len(vectors)}) and doc_ids ({len(doc_ids)}) must have same length")

        # Ensure correct dtype and contiguous array
        vectors = np.ascontiguousarray(vectors.astype(np.float32))

        # Add to index
        self.index.add(vectors)

        # Add to ID map
        self.id_map.extend(doc_ids)

    def search(self, query_vector: np.ndarray, k: int = 5) -> Tuple[List[str], np.ndarray]:
        """
        Search for k nearest neighbors.

        Args:
            query_vector: Query embedding, shape (dim,) or (1, dim), dtype float32
            k: Number of neighbors to return

        Returns:
            Tuple of (doc_ids, similarities):
            - doc_ids: List of document ID strings
            - similarities: Numpy array of similarity scores (higher = more similar)
        """
        if self.index is None:
            raise RuntimeError("Index not loaded/created.")

        # Set search parameters
        self.index.nprobe = self.nprobe

        # Ensure correct shape and dtype
        query_vector = np.ascontiguousarray(query_vector.astype(np.float32))
        if query_vector.ndim == 1:
            query_vector = query_vector.reshape(1, -1)

        # Search
        similarities, indices = self.index.search(query_vector, k)

        # Map indices to doc_ids, filter out -1 (no result)
        doc_ids = []
        valid_similarities = []
        for idx, sim in zip(indices[0], similarities[0]):
            if idx >= 0 and idx < len(self.id_map):
                doc_ids.append(self.id_map[idx])
                valid_similarities.append(sim)

        return doc_ids, np.array(valid_similarities)

    def save(self):
        """Save index and ID map to disk."""
        if self.index is None:
            raise RuntimeError("No index to save.")

        print(f"Saving FAISS index to {self.index_path}...")
        faiss.write_index(self.index, self.index_path)

        print(f"Saving ID map to {self.id_map_path}...")
        with open(self.id_map_path, 'w') as f:
            json.dump(self.id_map, f)

        print(f"Saved: {self.index.ntotal} vectors, {len(self.id_map)} IDs")

    def get_stats(self) -> dict:
        """Get index statistics."""
        if self.index is None:
            return {"status": "not_initialized"}

        return {
            "status": "ready" if self.is_trained else "not_trained",
            "total_vectors": self.index.ntotal,
            "id_map_size": len(self.id_map),
            "dimension": self.dim,
            "nprobe": self.nprobe,
            "index_path": self.index_path,
        }
