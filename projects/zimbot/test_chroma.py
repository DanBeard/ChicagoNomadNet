#!/usr/bin/env python3
"""
Minimal ChromaDB test to isolate segfault.
"""

import sys
print("Starting ChromaDB test...", flush=True)

print("Step 1: Importing chromadb...", flush=True)
import chromadb
from chromadb.config import Settings

print("Step 2: Creating client...", flush=True)
client = chromadb.PersistentClient(
    path="./test_chromadb",
    settings=Settings(
        anonymized_telemetry=False,
        allow_reset=True
    )
)

print("Step 3: Creating/getting collection...", flush=True)
collection = client.get_or_create_collection(
    name="test_collection",
    metadata={"hnsw:space": "cosine"}
)

print("Step 4: Creating test embedding (384 dims for all-MiniLM-L6-v2)...", flush=True)
# Fake embedding - 384 dimensions like all-MiniLM-L6-v2
test_embedding = [0.1] * 384

print("Step 5: Adding document with embedding...", flush=True)
collection.add(
    documents=["This is a test document with some text content."],
    embeddings=[test_embedding],
    metadatas=[{"source": "test"}],
    ids=["test_doc_1"]
)

print("Step 6: Document added successfully!", flush=True)

print("Step 7: Querying...", flush=True)
results = collection.query(
    query_embeddings=[test_embedding],
    n_results=1
)
print(f"Query results: {results}", flush=True)

print("\n=== ALL TESTS PASSED ===", flush=True)
