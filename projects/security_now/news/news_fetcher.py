"""
Fetch security news from RSS feeds.

Aggregates news from multiple security sources and stores in SQLite.
"""
import feedparser
import re
from datetime import datetime
from time import mktime
from typing import Optional
import logging

from .feed_config import SECURITY_FEEDS, PRIORITY_KEYWORDS
from . import news_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def calculate_relevance(title: str, summary: str, priority: int) -> float:
    """
    Calculate relevance score for a news item.

    Higher scores = more relevant to Security Now! audience.
    """
    score = 0.0
    text = f"{title} {summary}".lower()

    # Base score from feed priority (1-3, inverted so 1 = highest)
    score += (4 - priority) * 10  # Priority 1 = 30, Priority 3 = 10

    # Keyword matches
    for keyword in PRIORITY_KEYWORDS:
        if keyword.lower() in text:
            score += 5

    # Length bonus for detailed stories
    if len(summary or "") > 500:
        score += 5

    # Recency is handled in DB query, not here

    return score


def parse_published_date(entry) -> Optional[datetime]:
    """Extract published date from feed entry."""
    for date_field in ['published_parsed', 'updated_parsed', 'created_parsed']:
        parsed = getattr(entry, date_field, None)
        if parsed:
            try:
                return datetime.fromtimestamp(mktime(parsed))
            except (ValueError, OverflowError):
                continue
    return None


def clean_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', '', text)
    # Normalize whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def fetch_feed(feed_config: dict) -> list[dict]:
    """
    Fetch and parse a single RSS feed.

    Returns list of parsed items.
    """
    name = feed_config['name']
    url = feed_config['url']
    priority = feed_config.get('priority', 2)

    logger.info(f"Fetching {name}...")

    try:
        feed = feedparser.parse(url)

        if feed.bozo and feed.bozo_exception:
            logger.warning(f"{name}: Parse warning - {feed.bozo_exception}")

        items = []
        for entry in feed.entries:
            title = clean_html(getattr(entry, 'title', ''))
            if not title:
                continue

            summary = clean_html(
                getattr(entry, 'summary', '') or
                getattr(entry, 'description', '')
            )

            # Get full content if available
            content = ""
            if hasattr(entry, 'content'):
                content = clean_html(entry.content[0].get('value', ''))

            items.append({
                'feed_name': name,
                'title': title,
                'url': getattr(entry, 'link', ''),
                'summary': summary[:2000] if summary else None,  # Truncate long summaries
                'content': content[:10000] if content else None,
                'author': getattr(entry, 'author', None),
                'published_at': parse_published_date(entry),
                'relevance_score': calculate_relevance(title, summary, priority)
            })

        logger.info(f"{name}: Found {len(items)} items")
        return items

    except Exception as e:
        logger.error(f"{name}: Fetch failed - {e}")
        return []


def fetch_all_feeds() -> dict:
    """
    Fetch all configured feeds and store new items.

    Returns statistics dict.
    """
    stats = {
        'feeds_fetched': 0,
        'feeds_failed': 0,
        'items_found': 0,
        'items_new': 0,
        'items_duplicate': 0
    }

    # Ensure DB is initialized
    news_db.init_db()

    for feed_config in SECURITY_FEEDS:
        items = fetch_feed(feed_config)

        if items:
            stats['feeds_fetched'] += 1
            stats['items_found'] += len(items)

            for item in items:
                item_id = news_db.add_news_item(**item)
                if item_id:
                    stats['items_new'] += 1
                else:
                    stats['items_duplicate'] += 1
        else:
            stats['feeds_failed'] += 1

    return stats


def main():
    """CLI tool to fetch news."""
    import argparse

    parser = argparse.ArgumentParser(description="Fetch security news from RSS feeds")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    parser.add_argument("--cleanup", type=int, metavar="DAYS",
                        help="Delete news older than N days")

    args = parser.parse_args()

    news_db.init_db()

    if args.stats:
        stats = news_db.get_stats()
        print("Database Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        return

    if args.cleanup:
        deleted = news_db.cleanup_old_news(args.cleanup)
        print(f"Deleted {deleted} old news items")
        return

    print("Fetching security news...")
    stats = fetch_all_feeds()

    print("\nFetch Statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
