"""
Security Now! backend service for NomadNet.

Listens on localhost for requests from the NomadNet page and serves digest content.
Follows the same pattern as zim_host.py.
"""
import os
import sys
import signal
from multiprocessing.connection import Listener
import logging
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security_now.config import config
from security_now.news import news_db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Auth key from environment
authkey = config.authkey.encode()

# Global flag for graceful shutdown
running = True


def handle_signal(signum, frame):
    """Handle shutdown signals."""
    global running
    logger.info("Shutdown signal received")
    running = False


def get_latest_digest() -> dict:
    """Get the most recent digest."""
    digest = news_db.get_latest_digest()

    if digest:
        return {
            "status": "ok",
            "date": str(digest['digest_date']),
            "content": digest['content'],
            "generated_at": str(digest['generated_at']),
            "model_version": digest['model_version']
        }

    return {
        "status": "error",
        "message": "No digest available. The digest generator may not have run yet."
    }


def get_archive(date_str: str) -> dict:
    """Get digest for a specific date."""
    digest = news_db.get_digest_by_date(date_str)

    if digest:
        return {
            "status": "ok",
            "date": str(digest['digest_date']),
            "content": digest['content'],
            "generated_at": str(digest['generated_at']),
            "model_version": digest['model_version']
        }

    return {
        "status": "error",
        "message": f"No digest found for {date_str}"
    }


def list_archives(limit: int = 30) -> dict:
    """List available digest dates."""
    dates = news_db.list_digest_dates(limit)

    return {
        "status": "ok",
        "dates": dates,
        "count": len(dates)
    }


def get_stats() -> dict:
    """Get service statistics."""
    stats = news_db.get_stats()

    return {
        "status": "ok",
        "stats": stats
    }


def handle_request(msg: dict) -> dict:
    """Route and handle incoming requests."""
    command = msg.get("command")

    if command == "get_latest":
        return get_latest_digest()

    elif command == "get_archive":
        date_str = msg.get("date")
        if not date_str:
            return {"status": "error", "message": "Missing 'date' parameter"}
        return get_archive(date_str)

    elif command == "list_archives":
        limit = msg.get("limit", 30)
        return list_archives(limit)

    elif command == "stats":
        return get_stats()

    else:
        return {
            "status": "error",
            "message": f"Unknown command: {command}"
        }


def main_loop():
    """Main service loop."""
    global running

    # Initialize database
    news_db.init_db()

    # Set up signal handlers
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    address = (config.host_address, config.host_port)
    logger.info(f"Security Now! host starting on {address[0]}:{address[1]}")

    listener = Listener(address, authkey=authkey)

    while running:
        try:
            # Accept with timeout to allow checking running flag
            listener._listener._socket.settimeout(1.0)

            try:
                conn = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                continue

            try:
                msg = conn.recv()
                logger.debug(f"Received: {msg.get('command', 'unknown')}")

                response = handle_request(msg)
                conn.send(response)

            except Exception as e:
                logger.error(f"Request handling error: {e}")
                try:
                    conn.send({"status": "error", "message": str(e)})
                except:
                    pass
            finally:
                conn.close()

        except Exception as e:
            if running:
                logger.error(f"Main loop error: {e}")

    listener.close()
    logger.info("Security Now! host stopped")


def main():
    """Entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Security Now! NomadNet backend service")
    parser.add_argument("--port", type=int, default=config.host_port,
                        help="Port to listen on")
    parser.add_argument("--test", action="store_true",
                        help="Run a quick test and exit")

    args = parser.parse_args()

    if args.test:
        # Quick test mode
        news_db.init_db()
        print("Database initialized")
        print(f"Stats: {news_db.get_stats()}")
        print("Test passed!")
        return

    config.host_port = args.port
    main_loop()


if __name__ == "__main__":
    main()
