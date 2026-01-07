"""
Process Security Now! transcripts to extract speaker segments.

The transcripts have various speaker label formats:
- "STEVE GIBSON:" or "STEVE:"
- "LEO LAPORTE:" or "LEO:"
- Sometimes "Steve:" or "Leo:" (lowercase)
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Optional
import logging

logger = logging.getLogger(__name__)

# Speaker patterns - capture speaker name and the content that follows
SPEAKER_PATTERN = re.compile(
    r'^(STEVE(?:\s+GIBSON)?|LEO(?:\s+LAPORTE)?|FATHER\s+ROBERT|FR\.\s+ROBERT):\s*(.*)$',
    re.IGNORECASE | re.MULTILINE
)

# Episode metadata patterns
EPISODE_PATTERN = re.compile(r'Security Now!?\s*#?\s*(\d+)', re.IGNORECASE)
DATE_PATTERN = re.compile(r'(\w+\s+\d+,?\s+\d{4})')
TITLE_PATTERN = re.compile(r'^(.+?)(?:\n|$)', re.MULTILINE)


@dataclass
class Segment:
    """A single speaker segment from the transcript."""
    speaker: str  # Normalized: "STEVE" or "LEO"
    content: str
    episode: Optional[int] = None


@dataclass
class Episode:
    """Parsed episode with metadata and segments."""
    number: Optional[int]
    title: Optional[str]
    date: Optional[str]
    segments: list[Segment]
    raw_text: str


def normalize_speaker(speaker: str) -> str:
    """Normalize speaker name to STEVE or LEO."""
    speaker = speaker.upper().strip()
    if speaker.startswith("STEVE"):
        return "STEVE"
    elif speaker.startswith("LEO"):
        return "LEO"
    elif "ROBERT" in speaker:
        return "ROBERT"  # Father Robert Ballecer (guest host)
    return speaker


def extract_segments(text: str) -> list[Segment]:
    """
    Extract speaker segments from transcript text.

    Returns list of Segment objects with speaker and content.
    """
    segments = []
    current_speaker = None
    current_content = []

    for line in text.split('\n'):
        # Check if this line starts a new speaker
        match = SPEAKER_PATTERN.match(line.strip())

        if match:
            # Save previous segment if exists
            if current_speaker and current_content:
                content = ' '.join(current_content).strip()
                if content:
                    segments.append(Segment(
                        speaker=normalize_speaker(current_speaker),
                        content=content
                    ))

            # Start new segment
            current_speaker = match.group(1)
            # Include any text on the same line as the speaker label
            first_content = match.group(2).strip()
            current_content = [first_content] if first_content else []

        elif current_speaker:
            # Continue current segment
            line = line.strip()
            if line:
                current_content.append(line)

    # Don't forget the last segment
    if current_speaker and current_content:
        content = ' '.join(current_content).strip()
        if content:
            segments.append(Segment(
                speaker=normalize_speaker(current_speaker),
                content=content
            ))

    return segments


def extract_metadata(text: str, filename: Optional[str] = None) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Extract episode number, title, and date from transcript.

    Returns (episode_number, title, date)
    """
    episode_num = None
    title = None
    date = None

    # Try to get episode number from filename first
    if filename:
        match = re.search(r'sn-?(\d+)', filename, re.IGNORECASE)
        if match:
            episode_num = int(match.group(1))

    # Try to find in text
    ep_match = EPISODE_PATTERN.search(text[:2000])  # Check first part
    if ep_match:
        episode_num = int(ep_match.group(1))

    date_match = DATE_PATTERN.search(text[:1000])
    if date_match:
        date = date_match.group(1)

    # Title is trickier - often in the first few lines
    lines = text.strip().split('\n')[:10]
    for line in lines:
        line = line.strip()
        # Skip empty lines and common headers
        if not line or line.startswith('GIBSON') or 'Security Now' in line:
            continue
        if len(line) > 10 and len(line) < 200:
            # Looks like a title candidate
            title = line
            break

    return episode_num, title, date


def parse_transcript(text: str, filename: Optional[str] = None) -> Episode:
    """
    Parse a full transcript into an Episode object.
    """
    episode_num, title, date = extract_metadata(text, filename)
    segments = extract_segments(text)

    # Tag segments with episode number
    for seg in segments:
        seg.episode = episode_num

    return Episode(
        number=episode_num,
        title=title,
        date=date,
        segments=segments,
        raw_text=text
    )


def parse_transcript_file(filepath: Path) -> Episode:
    """Parse a transcript file."""
    text = filepath.read_text(encoding='utf-8', errors='replace')
    return parse_transcript(text, filepath.name)


def iter_transcripts(transcripts_dir: Path) -> Generator[Episode, None, None]:
    """
    Iterate over all transcript files in a directory.

    Yields Episode objects in episode number order.
    """
    files = sorted(transcripts_dir.glob("sn-*.txt"))

    for filepath in files:
        try:
            yield parse_transcript_file(filepath)
        except Exception as e:
            logger.error(f"Failed to parse {filepath}: {e}")
            continue


def get_steve_explanations(episode: Episode, min_length: int = 200) -> list[str]:
    """
    Extract Steve's longer explanatory segments.

    These are good candidates for training data - Steve explaining things
    in his characteristic first-principles style.
    """
    explanations = []

    for segment in episode.segments:
        if segment.speaker == "STEVE" and len(segment.content) >= min_length:
            explanations.append(segment.content)

    return explanations


def get_qa_pairs(episode: Episode, min_answer_length: int = 100) -> list[tuple[str, str]]:
    """
    Extract Q&A pairs where Leo asks and Steve answers.

    Returns list of (question, answer) tuples.
    """
    pairs = []
    segments = episode.segments

    for i, seg in enumerate(segments):
        # Look for Leo followed by Steve
        if seg.speaker == "LEO" and i + 1 < len(segments):
            next_seg = segments[i + 1]
            if next_seg.speaker == "STEVE" and len(next_seg.content) >= min_answer_length:
                # Check if Leo's segment looks like a question
                leo_text = seg.content
                if '?' in leo_text or len(leo_text) < 500:  # Short Leo = likely a question
                    pairs.append((leo_text, next_seg.content))

    return pairs


def main():
    """CLI tool to test transcript processing."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Process Security Now! transcripts")
    parser.add_argument("input", type=str, help="Transcript file or directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--stats", action="store_true", help="Show statistics only")

    args = parser.parse_args()
    input_path = Path(args.input)

    if input_path.is_file():
        episodes = [parse_transcript_file(input_path)]
    else:
        episodes = list(iter_transcripts(input_path))

    if args.stats:
        total_segments = sum(len(ep.segments) for ep in episodes)
        steve_segments = sum(
            sum(1 for s in ep.segments if s.speaker == "STEVE")
            for ep in episodes
        )
        print(f"Episodes: {len(episodes)}")
        print(f"Total segments: {total_segments}")
        print(f"Steve segments: {steve_segments}")
        print(f"Avg segments/episode: {total_segments / len(episodes):.1f}")
        return

    for ep in episodes:
        if args.json:
            print(json.dumps({
                'number': ep.number,
                'title': ep.title,
                'date': ep.date,
                'segment_count': len(ep.segments),
                'steve_explanations': len(get_steve_explanations(ep)),
                'qa_pairs': len(get_qa_pairs(ep))
            }))
        else:
            print(f"\nEpisode {ep.number}: {ep.title}")
            print(f"  Date: {ep.date}")
            print(f"  Segments: {len(ep.segments)}")
            print(f"  Steve explanations (>200 chars): {len(get_steve_explanations(ep))}")
            print(f"  Q&A pairs: {len(get_qa_pairs(ep))}")


if __name__ == "__main__":
    main()
