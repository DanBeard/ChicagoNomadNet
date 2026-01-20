"""
Lazy Embedding Worker

Child process that handles background embedding tasks:
- Processes embedding queue by priority
- Connects to zim_host for full-text search
- Monitors CPU for idle-time drip embedding
- Extracts and queues internal links from embedded articles
"""

import os
import re
import time
import sqlite3
import struct
import traceback
from multiprocessing import Process, Queue, Event
from multiprocessing.connection import Client
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

import psutil
import sqlite_vec
from sentence_transformers import SentenceTransformer

from .config import ZimBotConfig
from .lazy_queue import LazyEmbeddingQueue, EmbeddingTask


@dataclass
class WorkerStats:
    """Statistics for the embedding worker."""
    articles_embedded: int = 0
    chunks_embedded: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    cpu_samples: List[float] = None

    def __post_init__(self):
        if self.cpu_samples is None:
            self.cpu_samples = []


class CPUMonitor:
    """Monitors CPU usage for idle-time detection."""

    def __init__(self, idle_threshold: float = 30.0, max_samples: int = 12):
        self.idle_threshold = idle_threshold
        self.max_samples = max_samples
        self.samples: List[float] = []

    def update(self) -> float:
        """Take a CPU sample and return current usage."""
        cpu_percent = psutil.cpu_percent(interval=0.5)
        self.samples.append(cpu_percent)
        if len(self.samples) > self.max_samples:
            self.samples.pop(0)
        return cpu_percent

    def is_idle(self) -> bool:
        """Check if system is idle enough for drip embedding."""
        if len(self.samples) < 3:
            return False
        avg_cpu = sum(self.samples[-3:]) / 3
        return avg_cpu < self.idle_threshold

    def get_average(self) -> float:
        """Get recent average CPU usage."""
        if not self.samples:
            return 100.0
        return sum(self.samples) / len(self.samples)


class ZimHostClient:
    """Client for communicating with zim_host.py."""

    def __init__(self, host: str = 'localhost', port: int = 6000):
        self.host = host
        self.port = port
        self.authkey = os.environ.get("ZIM_AUTHKEY", "").encode()

    def _connect(self):
        """Create connection to zim_host."""
        return Client((self.host, self.port), authkey=self.authkey)

    def search(self, keyword: str, archive_idx: int = 0,
               page: int = 0, page_size: int = 10) -> Optional[Dict]:
        """
        Search ZIM archive using full-text search.
        Returns search results or None on error.
        """
        try:
            conn = self._connect()
            try:
                conn.send({
                    "command": "search",
                    "archive": archive_idx,
                    "search": keyword,
                    "page": page
                })
                return conn.recv()
            finally:
                conn.close()
        except Exception as e:
            print(f"[Worker] zim_host search error: {e}")
            return None

    def get_article(self, archive_idx: int, path: str) -> Optional[Dict]:
        """
        Get full article content from ZIM archive.
        Returns article data or None on error.
        """
        try:
            conn = self._connect()
            try:
                conn.send({
                    "command": "request_path",
                    "archive": archive_idx,
                    "path": path,
                    "last_path": None,
                    "content_page": 0
                })
                resp = conn.recv()

                if resp.get("status") != "ok":
                    return None

                # If paginated, get all pages
                content = resp.get("content", "")
                pagination = resp.get("pagination", {})

                while pagination.get("has_next"):
                    next_page = pagination.get("page", 0) + 1
                    conn.send({
                        "command": "request_path",
                        "archive": archive_idx,
                        "path": path,
                        "last_path": None,
                        "content_page": next_page
                    })
                    next_resp = conn.recv()
                    if next_resp.get("status") == "ok":
                        content += next_resp.get("content", "")
                        pagination = next_resp.get("pagination", {})
                    else:
                        break

                return {
                    "title": resp.get("title", ""),
                    "content": content,
                    "path": path,
                    "archive_idx": archive_idx
                }
            finally:
                conn.close()
        except Exception as e:
            print(f"[Worker] zim_host get_article error: {e}")
            return None

    def list_archives(self) -> List[str]:
        """Get list of available archives."""
        try:
            conn = self._connect()
            try:
                conn.send({"command": "list_archives"})
                resp = conn.recv()
                if resp.get("status") == "ok":
                    return resp.get("archives", [])
                return []
            finally:
                conn.close()
        except Exception:
            return []


def extract_internal_links(content: str, max_links: int = 20) -> List[str]:
    """
    Extract internal wiki links from content.
    Returns list of article paths.
    """
    # Common patterns for wiki-style internal links
    patterns = [
        r'href="\.?/?([^"#]+)"',  # Relative links
        r'href="/A/([^"#]+)"',     # Wikipedia article links
        r'\[\[([^\]|]+)',          # Wiki markup links
    ]

    links = set()
    for pattern in patterns:
        matches = re.findall(pattern, content)
        links.update(matches)

    # Filter out non-article paths
    filtered = []
    for link in links:
        # Skip media files
        if link.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg',
                                   '.css', '.js', '.ico', '.webp')):
            continue
        # Skip special pages
        if link.startswith(('Special:', 'File:', 'Category:', 'Template:',
                           'Help:', 'Wikipedia:', 'Portal:')):
            continue
        # Skip external-looking links
        if link.startswith(('http:', 'https:', '//', 'mailto:')):
            continue

        filtered.append(link)

    return filtered[:max_links]


def embedding_worker_main(request_queue: Queue, response_queue: Queue,
                          shutdown_event: Event, config: ZimBotConfig):
    """
    Main function for the embedding worker process.
    """
    print("[Worker] Starting lazy embedding worker...")

    # Initialize components
    stats = WorkerStats()
    cpu_monitor = CPUMonitor(idle_threshold=config.lazy_idle_cpu_threshold)
    zim_client = ZimHostClient()

    # Initialize SQLite connection (separate from parent)
    conn = sqlite3.connect(config.sqlite_db_path, check_same_thread=False)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    queue_manager = LazyEmbeddingQueue(conn)

    # Load embedding model
    print(f"[Worker] Loading embedding model: {config.embedding_model}")
    model = SentenceTransformer(config.embedding_model)
    print("[Worker] Model loaded.")

    # Reset any stale in_progress tasks from previous runs
    queue_manager.reset_stale_tasks()

    last_drip_time = time.time()
    last_status_time = time.time()

    print("[Worker] Ready and listening for tasks...")

    while not shutdown_event.is_set():
        try:
            # Check for requests from parent
            while not request_queue.empty():
                try:
                    request = request_queue.get_nowait()
                    handle_parent_request(request, queue_manager, response_queue, stats)
                except Exception as e:
                    print(f"[Worker] Error handling request: {e}")

            # Update CPU monitor
            cpu_monitor.update()

            # Process next task from queue
            task = queue_manager.get_next_task()
            if task:
                process_task(task, queue_manager, conn, model, config,
                            zim_client, stats)

            # Idle-time drip embedding (only if enabled)
            elif config.lazy_drip_enabled and cpu_monitor.is_idle():
                now = time.time()
                if now - last_drip_time >= config.lazy_drip_interval:
                    do_drip_embedding(queue_manager, conn, model, config,
                                     zim_client, stats)
                    last_drip_time = now

            # Periodic status update
            now = time.time()
            if now - last_status_time >= 30:
                send_status(response_queue, stats, queue_manager, cpu_monitor)
                last_status_time = now

            # Small sleep to prevent busy-waiting
            time.sleep(1.0)

        except Exception as e:
            print(f"[Worker] Error in main loop: {e}")
            traceback.print_exc()
            time.sleep(5.0)

    print("[Worker] Shutting down...")
    conn.close()


def handle_parent_request(request: Dict, queue_manager: LazyEmbeddingQueue,
                          response_queue: Queue, stats: WorkerStats):
    """Handle a request from the parent process."""
    req_type = request.get("type")

    if req_type == "embed_keywords":
        keywords = request.get("keywords", [])
        priority = request.get("priority", 10)
        archive_idx = request.get("archive_idx")

        added = queue_manager.queue_keywords(
            keywords,
            source_type='conversation',
            priority=priority,
            archive_idx=archive_idx
        )
        print(f"[Worker] Queued {added} new keywords: {keywords}")

        response_queue.put({
            "type": "keywords_queued",
            "keywords": keywords,
            "added": added
        })

    elif req_type == "embed_article":
        archive_idx = request.get("archive_idx", 0)
        path = request.get("path")
        priority = request.get("priority", 5)

        added = queue_manager.queue_article(
            archive_idx, path,
            source_type='link',
            priority=priority
        )
        print(f"[Worker] Queued article: {path} (added={added})")

    elif req_type == "status_request":
        # Will be sent on next status cycle
        pass

    elif req_type == "shutdown":
        print("[Worker] Received shutdown request")


def send_status(response_queue: Queue, stats: WorkerStats,
                queue_manager: LazyEmbeddingQueue, cpu_monitor: CPUMonitor):
    """Send status update to parent."""
    queue_stats = queue_manager.get_stats()
    response_queue.put({
        "type": "status",
        "articles_embedded": stats.articles_embedded,
        "chunks_embedded": stats.chunks_embedded,
        "tasks_completed": stats.tasks_completed,
        "tasks_failed": stats.tasks_failed,
        "queue_depth": queue_stats.get('pending', 0),
        "cpu_avg": cpu_monitor.get_average()
    })


def process_task(task: EmbeddingTask, queue_manager: LazyEmbeddingQueue,
                 conn: sqlite3.Connection, model: SentenceTransformer,
                 config: ZimBotConfig, zim_client: ZimHostClient,
                 stats: WorkerStats):
    """Process a single embedding task."""
    print(f"[Worker] Processing task {task.id}: keyword='{task.keyword}', "
          f"path='{task.article_path}', type={task.source_type}")

    try:
        articles_found = 0
        chunks_embedded = 0

        if task.keyword:
            # Keyword-based task: search and embed results
            archive_idx = task.archive_idx if task.archive_idx is not None else 0

            # Search zim_host
            result = zim_client.search(task.keyword, archive_idx)
            if not result or result.get("status") != "ok":
                queue_manager.fail_task(task.id, "Search failed")
                stats.tasks_failed += 1
                return

            articles = result.get("results", [])
            articles_found = len(articles)

            # Embed each article
            for article in articles[:10]:  # Limit to top 10 results
                path = article.get("path")
                if not path:
                    continue

                # Check if already embedded
                if is_article_embedded(conn, archive_idx, path):
                    continue

                # Get full content
                full_article = zim_client.get_article(archive_idx, path)
                if not full_article:
                    continue

                # Embed it
                chunk_count = embed_article(
                    conn, model, config,
                    archive_idx, path,
                    full_article.get("content", ""),
                    full_article.get("title", ""),
                    task.keyword
                )
                chunks_embedded += chunk_count
                stats.articles_embedded += 1
                stats.chunks_embedded += chunk_count

                # Extract and queue internal links
                if config.lazy_link_follow_limit > 0:
                    links = extract_internal_links(
                        full_article.get("content", ""),
                        config.lazy_link_follow_limit
                    )
                    for link in links:
                        if not is_article_embedded(conn, archive_idx, link):
                            queue_manager.queue_article(
                                archive_idx, link,
                                source_type='link',
                                priority=LazyEmbeddingQueue.PRIORITY_LINK
                            )

        elif task.article_path:
            # Direct article embedding
            archive_idx = task.archive_idx if task.archive_idx is not None else 0

            # Check if already embedded
            if is_article_embedded(conn, archive_idx, task.article_path):
                queue_manager.complete_task(task.id, 0, 0)
                stats.tasks_completed += 1
                return

            # Get article
            article = zim_client.get_article(archive_idx, task.article_path)
            if not article:
                queue_manager.fail_task(task.id, "Article not found")
                stats.tasks_failed += 1
                return

            # Embed it
            chunks_embedded = embed_article(
                conn, model, config,
                archive_idx, task.article_path,
                article.get("content", ""),
                article.get("title", ""),
                None
            )
            articles_found = 1 if chunks_embedded > 0 else 0
            stats.articles_embedded += 1
            stats.chunks_embedded += chunks_embedded

        queue_manager.complete_task(task.id, articles_found, chunks_embedded)
        stats.tasks_completed += 1
        print(f"[Worker] Task {task.id} complete: {articles_found} articles, "
              f"{chunks_embedded} chunks")

    except Exception as e:
        print(f"[Worker] Task {task.id} failed: {e}")
        traceback.print_exc()
        queue_manager.fail_task(task.id, str(e))
        stats.tasks_failed += 1


def do_drip_embedding(queue_manager: LazyEmbeddingQueue,
                      conn: sqlite3.Connection, model: SentenceTransformer,
                      config: ZimBotConfig, zim_client: ZimHostClient,
                      stats: WorkerStats):
    """
    Perform idle-time drip embedding.
    Embeds random articles to slowly build coverage.
    """
    print("[Worker] Drip embedding (idle time detected)...")

    # Get available archives
    archives = zim_client.list_archives()
    if not archives:
        return

    # For now, just queue some common topics as keywords
    # A more sophisticated approach would track what's been embedded
    # and select diverse/popular topics
    drip_topics = [
        "science", "history", "technology", "mathematics", "physics",
        "chemistry", "biology", "geography", "philosophy", "literature",
        "art", "music", "economics", "engineering", "medicine"
    ]

    import random
    selected = random.sample(drip_topics, min(config.lazy_drip_count, len(drip_topics)))

    added = queue_manager.queue_keywords(
        selected,
        source_type='drip',
        priority=LazyEmbeddingQueue.PRIORITY_DRIP
    )

    print(f"[Worker] Drip: queued {added} topics: {selected}")


def is_article_embedded(conn: sqlite3.Connection, archive_idx: int,
                        article_path: str) -> bool:
    """Check if an article has already been embedded."""
    cursor = conn.execute(
        "SELECT 1 FROM embedded_articles WHERE archive_idx = ? AND article_path = ?",
        (archive_idx, article_path)
    )
    return cursor.fetchone() is not None


def embed_article(conn: sqlite3.Connection, model: SentenceTransformer,
                  config: ZimBotConfig, archive_idx: int, article_path: str,
                  content: str, title: str,
                  source_keyword: Optional[str]) -> int:
    """
    Embed a single article into the database.
    Returns number of chunks embedded.
    """
    # Extract text if HTML
    if '<html' in content.lower() or '<body' in content.lower():
        text = extract_text_from_html(content)
    else:
        text = content

    if len(text) <= 50:
        return 0

    # Chunk the text
    chunks = chunk_text(text, config.chunk_size, config.chunk_overlap)
    if not chunks:
        return 0

    # Generate IDs
    safe_path = article_path.replace('/', '_').replace('\\', '_')[:50]
    archive_name = f"archive_{archive_idx}"

    documents = []
    metadatas = []
    ids = []

    for chunk_idx, chunk_text in enumerate(chunks):
        doc_id = f"lazy_{archive_name}_{safe_path}_{chunk_idx}"
        documents.append(chunk_text)
        metadatas.append({
            "archive": archive_name,
            "path": article_path,
            "title": title,
            "mimetype": "text/html",
            "chunk": chunk_idx,
            "total_chunks": len(chunks)
        })
        ids.append(doc_id)

    # Embed
    embeddings = model.encode(
        documents,
        batch_size=64,
        show_progress_bar=False,
        normalize_embeddings=True
    )

    # Insert into DB
    doc_rows = [
        (ids[i], documents[i], metadatas[i].get('archive', ''),
         metadatas[i].get('path', ''), metadatas[i].get('title', ''),
         metadatas[i].get('mimetype', ''), metadatas[i].get('chunk', 0),
         metadatas[i].get('total_chunks', 1))
        for i in range(len(ids))
    ]

    vec_rows = [
        (ids[i], struct.pack(f'{len(embeddings[i])}f', *embeddings[i]))
        for i in range(len(ids))
    ]

    conn.executemany('''
        INSERT OR REPLACE INTO documents
        (id, content, archive, path, title, mimetype, chunk, total_chunks)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', doc_rows)

    # Delete existing vectors first (sqlite-vec doesn't support INSERT OR REPLACE)
    placeholders = ','.join('?' * len(ids))
    conn.execute(f'DELETE FROM vec_documents WHERE doc_id IN ({placeholders})', ids)

    conn.executemany('''
        INSERT INTO vec_documents (doc_id, embedding)
        VALUES (?, ?)
    ''', vec_rows)

    # Mark as embedded
    conn.execute('''
        INSERT OR REPLACE INTO embedded_articles
        (archive_idx, article_path, chunk_count, source_keyword)
        VALUES (?, ?, ?, ?)
    ''', (archive_idx, article_path, len(chunks), source_keyword))

    conn.commit()

    return len(chunks)


def extract_text_from_html(html_content: str) -> str:
    """Extract clean text from HTML content."""
    # Remove script and style tags
    text = re.sub(r'<(script|style).*?>.*?</\1>', '', html_content,
                  flags=re.DOTALL | re.IGNORECASE)
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> List[str]:
    """Split text into chunks with overlap."""
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # Try to find clean break point
        if end < len(text):
            search_start = max(start, end - (chunk_size // 5))
            search_region = text[search_start:end]

            for sep in ['. ', '\n', ' ']:
                bp = search_region.rfind(sep)
                if bp > 0:
                    end = search_start + bp + 1
                    break

        chunk = text[start:end]
        chunks.append(chunk)

        start = end - overlap
        if start <= 0:
            start = end

    return chunks


class EmbeddingWorkerManager:
    """
    Manager for the embedding worker process.
    Handles spawning, communication, and lifecycle.
    """

    def __init__(self, config: ZimBotConfig):
        self.config = config
        self.process: Optional[Process] = None
        self.request_queue: Optional[Queue] = None
        self.response_queue: Optional[Queue] = None
        self.shutdown_event: Optional[Event] = None
        self.last_status: Optional[Dict] = None

    def start(self):
        """Start the embedding worker process."""
        if self.process and self.process.is_alive():
            return  # Already running

        self.request_queue = Queue()
        self.response_queue = Queue()
        self.shutdown_event = Event()

        self.process = Process(
            target=embedding_worker_main,
            args=(
                self.request_queue,
                self.response_queue,
                self.shutdown_event,
                self.config
            ),
            daemon=True
        )
        self.process.start()
        print(f"[Manager] Embedding worker started (PID: {self.process.pid})")

    def stop(self, timeout: float = 10.0):
        """Stop the embedding worker gracefully."""
        if not self.process:
            return

        print("[Manager] Stopping embedding worker...")

        # Signal shutdown
        if self.shutdown_event:
            self.shutdown_event.set()

        # Send shutdown request
        if self.request_queue:
            try:
                self.request_queue.put({"type": "shutdown"})
            except Exception:
                pass

        # Wait for process to finish
        if self.process.is_alive():
            self.process.join(timeout=timeout)
            if self.process.is_alive():
                print("[Manager] Worker didn't stop gracefully, terminating...")
                self.process.terminate()

        self.process = None

    def is_alive(self) -> bool:
        """Check if worker is running."""
        return self.process is not None and self.process.is_alive()

    def restart_if_dead(self):
        """Restart worker if it has died."""
        if not self.is_alive():
            print("[Manager] Worker died, restarting...")
            self.start()

    def queue_keywords(self, keywords: List[str], priority: int = 10,
                       archive_idx: Optional[int] = None):
        """Queue keywords for embedding."""
        if not self.is_alive():
            self.start()

        self.request_queue.put({
            "type": "embed_keywords",
            "keywords": keywords,
            "priority": priority,
            "archive_idx": archive_idx
        })

    def queue_article(self, archive_idx: int, path: str, priority: int = 5):
        """Queue a specific article for embedding."""
        if not self.is_alive():
            self.start()

        self.request_queue.put({
            "type": "embed_article",
            "archive_idx": archive_idx,
            "path": path,
            "priority": priority
        })

    def get_status(self) -> Optional[Dict]:
        """Get latest status from worker."""
        # Drain response queue and get latest status
        while not self.response_queue.empty():
            try:
                msg = self.response_queue.get_nowait()
                if msg.get("type") == "status":
                    self.last_status = msg
            except Exception:
                break

        return self.last_status

    def request_status(self):
        """Request a status update from worker."""
        if self.is_alive():
            self.request_queue.put({"type": "status_request"})
