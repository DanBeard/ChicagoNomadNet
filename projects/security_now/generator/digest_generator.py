"""
Generate daily Security Now! style digest.

Fetches recent news and uses the fine-tuned LLM to generate Steve Gibson-style commentary.
"""
from datetime import datetime
import logging

from ..config import config
from ..news import news_db
from .llama_client import get_client
from .prompt_templates import SYSTEM_PROMPT, DIGEST_TEMPLATE, DIGEST_INTRO

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def format_news_for_prompt(news_items: list[dict]) -> str:
    """Format news items into a prompt-friendly summary."""
    summaries = []

    for i, item in enumerate(news_items, 1):
        summary = f"""
**{i}. {item['title']}**
Source: {item['feed_name']}
{item['summary'] or item.get('content', '')[:500]}
URL: {item['url']}
"""
        summaries.append(summary.strip())

    return "\n\n".join(summaries)


def generate_digest_content(news_items: list[dict]) -> str:
    """
    Generate digest content using the LLM.

    Args:
        news_items: List of news items from database

    Returns:
        Generated digest text
    """
    client = get_client()

    # Check if LLM is available
    if not client.health_check():
        logger.warning("LLM server not available, using fallback")
        return generate_fallback_digest(news_items)

    # Format the news for the prompt
    news_summaries = format_news_for_prompt(news_items)
    prompt = DIGEST_TEMPLATE.format(news_summaries=news_summaries)

    try:
        logger.info("Generating digest with LLM...")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        content = client.chat(
            messages=messages,
            max_tokens=8192,
            temperature=0.7
        )

        if not content or len(content) < 100:
            logger.warning("LLM returned insufficient content, using fallback")
            return generate_fallback_digest(news_items)

        return content

    except Exception as e:
        logger.error(f"LLM generation failed: {e}")
        return generate_fallback_digest(news_items)


def generate_fallback_digest(news_items: list[dict]) -> str:
    """
    Generate a simple digest without LLM (fallback mode).

    Used when the LLM server is unavailable.
    """
    lines = [
        "## Security News Digest",
        "",
        "*Note: AI commentary temporarily unavailable. Here are the raw stories:*",
        ""
    ]

    for item in news_items:
        lines.append(f"### {item['title']}")
        lines.append(f"*Source: {item['feed_name']}*")
        lines.append("")
        if item['summary']:
            lines.append(item['summary'][:500])
        lines.append("")
        lines.append(f"Read more: {item['url']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def run_daily_digest():
    """
    Main function to generate the daily digest.

    Called by cron job.
    """
    logger.info("Starting daily digest generation...")

    # Initialize database
    news_db.init_db()

    # Get unused news items
    news_items = news_db.get_unused_news(
        limit=config.max_stories_per_digest,
        max_age_days=config.max_news_age_days
    )

    if not news_items:
        logger.warning("No news items available for digest")
        return None

    logger.info(f"Found {len(news_items)} news items for digest")

    # Generate content
    content = generate_digest_content(news_items)

    # Save digest
    item_ids = [item['id'] for item in news_items]
    digest_id = news_db.save_digest(
        digest_date=datetime.now(),
        content=content,
        news_item_ids=item_ids,
        model_version=config.model_version
    )

    # Mark news as used
    news_db.mark_news_used(item_ids)

    logger.info(f"Digest saved with ID {digest_id}")
    return digest_id


def main():
    """CLI tool for digest generation."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate Security Now! digest")
    parser.add_argument("--preview", action="store_true",
                        help="Preview without saving to database")
    parser.add_argument("--fallback", action="store_true",
                        help="Use fallback mode (no LLM)")
    parser.add_argument("--max-stories", type=int, default=10,
                        help="Maximum stories to include")

    args = parser.parse_args()

    news_db.init_db()

    news_items = news_db.get_unused_news(
        limit=args.max_stories,
        max_age_days=7
    )

    if not news_items:
        print("No news items available")
        return

    print(f"Found {len(news_items)} news items\n")

    if args.fallback:
        content = generate_fallback_digest(news_items)
    else:
        content = generate_digest_content(news_items)

    print("=" * 60)
    print("GENERATED DIGEST")
    print("=" * 60)
    print(content)

    if not args.preview:
        item_ids = [item['id'] for item in news_items]
        digest_id = news_db.save_digest(
            digest_date=datetime.now(),
            content=content,
            news_item_ids=item_ids,
            model_version=config.model_version
        )
        news_db.mark_news_used(item_ids)
        print(f"\nDigest saved with ID: {digest_id}")


if __name__ == "__main__":
    main()
