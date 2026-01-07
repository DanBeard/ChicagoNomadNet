"""
Scrape Security Now! transcripts from grc.com.

Transcripts are available at: https://www.grc.com/sn/sn-{episode}.txt
Episodes numbered from 1 to current (~1059+).
"""
import os
import time
import requests
from pathlib import Path
from typing import Optional
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://www.grc.com/sn/sn-{episode}.txt"
USER_AGENT = "SecurityNow-Transcript-Scraper/1.0 (NomadNet project; polite scraping)"


def get_transcript_path(episode: int, output_dir: Path) -> Path:
    """Get the local path for a transcript file."""
    return output_dir / f"sn-{episode:04d}.txt"


def scrape_transcript(episode: int, delay: float = 1.0) -> Optional[str]:
    """
    Fetch a single transcript from GRC.

    Args:
        episode: Episode number (1-based)
        delay: Seconds to wait after request (be polite to GRC servers)

    Returns:
        Transcript text or None if not found
    """
    url = BASE_URL.format(episode=episode)
    headers = {"User-Agent": USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 404:
            logger.info(f"Episode {episode} not found (404)")
            return None

        response.raise_for_status()

        # GRC transcripts are plain text
        content = response.text

        # Basic validation - should contain speaker labels
        if "STEVE" not in content.upper() and "GIBSON" not in content.upper():
            logger.warning(f"Episode {episode}: Content doesn't look like a transcript")
            return None

        time.sleep(delay)  # Be polite
        return content

    except requests.RequestException as e:
        logger.error(f"Episode {episode}: Request failed - {e}")
        return None


def scrape_range(start: int, end: int, output_dir: Path, delay: float = 1.0,
                 skip_existing: bool = True) -> dict:
    """
    Scrape a range of episodes.

    Args:
        start: First episode number
        end: Last episode number (inclusive)
        output_dir: Directory to save transcripts
        delay: Seconds between requests
        skip_existing: Skip episodes that already have local files

    Returns:
        Dict with counts: {'scraped': N, 'skipped': N, 'failed': N, 'not_found': N}
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {'scraped': 0, 'skipped': 0, 'failed': 0, 'not_found': 0}

    for episode in range(start, end + 1):
        filepath = get_transcript_path(episode, output_dir)

        if skip_existing and filepath.exists():
            logger.debug(f"Episode {episode}: Skipping (exists)")
            stats['skipped'] += 1
            continue

        logger.info(f"Scraping episode {episode}...")
        content = scrape_transcript(episode, delay)

        if content is None:
            # Check if it's a 404 vs other error
            stats['not_found'] += 1
            continue

        try:
            filepath.write_text(content, encoding='utf-8')
            stats['scraped'] += 1
            logger.info(f"Episode {episode}: Saved ({len(content)} chars)")
        except IOError as e:
            logger.error(f"Episode {episode}: Failed to save - {e}")
            stats['failed'] += 1

    return stats


def get_latest_episode() -> int:
    """
    Try to find the latest available episode by binary search.
    """
    # Start with a known recent episode and search forward
    low, high = 1000, 1200

    while low < high:
        mid = (low + high + 1) // 2
        url = BASE_URL.format(episode=mid)

        try:
            response = requests.head(url, timeout=10)
            if response.status_code == 200:
                low = mid
            else:
                high = mid - 1
        except requests.RequestException:
            high = mid - 1

        time.sleep(0.5)

    return low


def main():
    """CLI entry point for scraping transcripts."""
    import argparse

    parser = argparse.ArgumentParser(description="Scrape Security Now! transcripts from GRC")
    parser.add_argument("--start", type=int, default=1, help="Starting episode number")
    parser.add_argument("--end", type=int, default=None, help="Ending episode (default: auto-detect)")
    parser.add_argument("--output", type=str, default="./transcripts", help="Output directory")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests (seconds)")
    parser.add_argument("--no-skip", action="store_true", help="Re-download existing files")

    args = parser.parse_args()

    output_dir = Path(args.output)

    if args.end is None:
        logger.info("Detecting latest episode...")
        args.end = get_latest_episode()
        logger.info(f"Latest episode appears to be: {args.end}")

    logger.info(f"Scraping episodes {args.start} to {args.end}")
    logger.info(f"Output directory: {output_dir}")

    stats = scrape_range(
        args.start,
        args.end,
        output_dir,
        delay=args.delay,
        skip_existing=not args.no_skip
    )

    logger.info(f"Done! Scraped: {stats['scraped']}, Skipped: {stats['skipped']}, "
                f"Not found: {stats['not_found']}, Failed: {stats['failed']}")


if __name__ == "__main__":
    main()
