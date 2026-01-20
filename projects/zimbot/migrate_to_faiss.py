#!/usr/bin/env python3
"""
Migrate sqlite-vec vectors to FAISS index.

One-time script to build a FAISS IVF index from existing vectors in sqlite-vec.
This enables fast approximate nearest neighbor search (~10-50ms vs 200+ seconds).

Usage:
    python -m projects.zimbot.migrate_to_faiss [--db-path PATH] [--nlist N]

Environment:
    ZIMBOT_SQLITE_PATH: Path to SQLite database (default: ./zimbot.db)
"""

import os
import sys
import struct
import time
import argparse
import sqlite3

import numpy as np
import sqlite_vec

from .faiss_index import FAISSIndex


# Configuration
DIM = 384  # Embedding dimension for all-MiniLM-L6-v2
BATCH_SIZE = 100000  # Vectors per batch when adding to index
TRAINING_SAMPLE = 500000  # Vectors to use for training (500k is plenty)
PQ_M = 48  # Number of subquantizers (must divide DIM evenly: 384/48=8)


def get_vector_count(conn: sqlite3.Connection) -> int:
    """Get total number of vectors in the database."""
    return conn.execute("SELECT COUNT(*) FROM vec_documents").fetchone()[0]


def extract_vectors_batch(conn: sqlite3.Connection, offset: int, limit: int) -> tuple:
    """
    Extract a batch of vectors from sqlite-vec.

    Returns:
        Tuple of (doc_ids, vectors) where vectors is np.ndarray of shape (n, DIM)
    """
    cursor = conn.execute(
        "SELECT doc_id, embedding FROM vec_documents LIMIT ? OFFSET ?",
        (limit, offset)
    )

    doc_ids = []
    vectors = []

    for row in cursor:
        doc_id = row[0]
        # Unpack binary embedding to float array
        embedding = np.array(struct.unpack(f'{DIM}f', row[1]), dtype=np.float32)
        doc_ids.append(doc_id)
        vectors.append(embedding)

    if vectors:
        return doc_ids, np.vstack(vectors)
    return [], np.array([], dtype=np.float32)


def main():
    parser = argparse.ArgumentParser(description="Migrate sqlite-vec to FAISS index")
    parser.add_argument("--db-path", default=os.getenv("ZIMBOT_SQLITE_PATH", "./zimbot.db"),
                        help="Path to SQLite database")
    parser.add_argument("--index-path", default="./zimbot_faiss.index",
                        help="Output path for FAISS index")
    parser.add_argument("--nlist", type=int, default=4096,
                        help="Number of IVF clusters (default: 4096)")
    parser.add_argument("--no-pq", action="store_true",
                        help="Disable Product Quantization (uses 66GB+ RAM)")
    parser.add_argument("--nprobe", type=int, default=64,
                        help="Number of clusters to search at query time (default: 64)")
    args = parser.parse_args()

    print("=" * 60)
    print("FAISS Migration Script")
    print("=" * 60)

    # Connect to SQLite with sqlite-vec
    if not os.path.exists(args.db_path):
        print(f"ERROR: Database not found: {args.db_path}")
        sys.exit(1)

    print(f"Connecting to: {args.db_path}")
    conn = sqlite3.connect(args.db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    # Count vectors
    total_vectors = get_vector_count(conn)
    print(f"Total vectors in database: {total_vectors:,}")

    if total_vectors == 0:
        print("ERROR: No vectors found in database.")
        sys.exit(1)

    # Derive paths
    index_path = args.index_path
    id_map_path = index_path.replace(".index", "_ids.json")

    # Create FAISS index
    faiss_idx = FAISSIndex(
        dim=DIM,
        index_path=index_path,
        id_map_path=id_map_path
    )
    use_pq = not args.no_pq
    faiss_idx.create(nlist=args.nlist, m=PQ_M, use_pq=use_pq)
    faiss_idx.nprobe = args.nprobe

    if use_pq:
        print(f"Using Product Quantization: ~2GB RAM for 43M vectors")
    else:
        print(f"WARNING: Not using PQ - will need ~66GB RAM for 43M vectors!")

    # Phase 1: Collect training vectors
    training_count = min(TRAINING_SAMPLE, total_vectors)
    print(f"\nPhase 1: Collecting {training_count:,} vectors for training...")

    start_time = time.time()
    training_vectors = []
    collected = 0
    offset = 0

    while collected < training_count:
        batch_size = min(BATCH_SIZE, training_count - collected)
        _, batch_vectors = extract_vectors_batch(conn, offset, batch_size)
        if len(batch_vectors) == 0:
            break
        training_vectors.append(batch_vectors)
        collected += len(batch_vectors)
        offset += batch_size
        print(f"  Collected {collected:,}/{training_count:,}", end="\r")

    training_vectors = np.vstack(training_vectors)
    print(f"\n  Collected {len(training_vectors):,} vectors in {time.time() - start_time:.1f}s")

    # Train the index
    print(f"\nPhase 2: Training IVF index with {args.nlist} clusters...")
    start_time = time.time()
    faiss_idx.train(training_vectors)
    print(f"  Training completed in {time.time() - start_time:.1f}s")

    # Free training vectors memory
    del training_vectors

    # Phase 3: Add all vectors
    print(f"\nPhase 3: Adding all {total_vectors:,} vectors to index...")
    start_time = time.time()
    offset = 0
    added = 0

    while offset < total_vectors:
        doc_ids, batch_vectors = extract_vectors_batch(conn, offset, BATCH_SIZE)
        if len(batch_vectors) == 0:
            break

        faiss_idx.add(batch_vectors, doc_ids)
        added += len(doc_ids)
        offset += BATCH_SIZE

        elapsed = time.time() - start_time
        rate = added / elapsed if elapsed > 0 else 0
        eta = (total_vectors - added) / rate if rate > 0 else 0
        print(f"  Added {added:,}/{total_vectors:,} ({100*added//total_vectors}%) "
              f"- {rate:.0f}/s, ETA: {eta/60:.1f}min", end="\r")

    print(f"\n  Added {added:,} vectors in {time.time() - start_time:.1f}s")

    # Phase 4: Save index
    print(f"\nPhase 4: Saving index...")
    start_time = time.time()
    faiss_idx.save()
    print(f"  Saved in {time.time() - start_time:.1f}s")

    # Summary
    print("\n" + "=" * 60)
    print("Migration Complete!")
    print("=" * 60)
    print(f"Index file: {index_path}")
    print(f"ID map file: {id_map_path}")
    print(f"Total vectors: {faiss_idx.index.ntotal:,}")
    print(f"Index size: {os.path.getsize(index_path) / (1024**3):.2f} GB")
    print(f"ID map size: {os.path.getsize(id_map_path) / (1024**2):.2f} MB")
    print(f"nprobe (search clusters): {faiss_idx.nprobe}")
    print("\nThe FAISS index is ready for use. Update zim_indexer.py to use it.")

    conn.close()


if __name__ == "__main__":
    main()
