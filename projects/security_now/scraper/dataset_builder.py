"""
Build training dataset for MLX-LM fine-tuning.

Creates JSONL files in ChatML format for Mistral instruction tuning.
"""
import json
import random
from pathlib import Path
from typing import Generator
import logging

from .transcript_processor import (
    Episode, iter_transcripts, get_steve_explanations, get_qa_pairs
)

logger = logging.getLogger(__name__)

# System prompt that captures Steve Gibson's communication style
STEVE_SYSTEM_PROMPT = """You are Steve Gibson, co-host of Security Now! podcast with Leo Laporte since 2005.

Your communication style:
- Start with the key point, then dive deep into technical details
- Use first-principles thinking to explain WHY something matters
- Reference historical context when relevant (Heartbleed, WannaCry, Kaminsky, etc.)
- Ask rhetorical questions before answering ("But here's the thing...")
- End with practical recommendations for listeners
- Express genuine fascination with technical topics ("This is really elegant...")
- Be thorough but engaging, never dry or academic
- Use analogies to make complex topics accessible
- Occasionally reference SpinRite or your own security tools when relevant

You're writing security news content in the style of the Security Now! podcast."""

# Various prompt templates for different training examples
EXPLANATION_PROMPTS = [
    "Explain {topic} to the Security Now audience.",
    "What should people know about {topic}?",
    "Can you break down {topic} for our listeners?",
    "Steve, tell us about {topic}.",
    "What's the deal with {topic}?",
]

NEWS_PROMPTS = [
    "What's happening with {topic} this week?",
    "Give us the security news about {topic}.",
    "What do we need to know about {topic}?",
]


def format_chatml(system: str, user: str, assistant: str) -> str:
    """
    Format a training example in ChatML format for Mistral.
    """
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant}<|im_end|>"
    )


def extract_topic_hint(text: str) -> str:
    """
    Try to extract a topic/subject from the text for the user prompt.
    """
    # Look for common patterns that indicate the topic
    text_lower = text.lower()

    # Common security topics
    topics = [
        "ransomware", "vulnerability", "exploit", "zero-day", "patch",
        "encryption", "certificate", "TLS", "SSL", "password", "authentication",
        "malware", "phishing", "breach", "hack", "privacy", "tracking",
        "browser", "Chrome", "Firefox", "Windows", "Linux", "Apple", "iOS",
        "Android", "Microsoft", "Google", "Facebook", "Amazon",
    ]

    for topic in topics:
        if topic.lower() in text_lower:
            return topic

    # Fallback: use first few words
    words = text.split()[:10]
    return " ".join(words)[:50] + "..."


def build_explanation_examples(episode: Episode) -> Generator[dict, None, None]:
    """
    Build training examples from Steve's explanatory segments.
    """
    explanations = get_steve_explanations(episode, min_length=300)

    for explanation in explanations:
        topic = extract_topic_hint(explanation)
        prompt_template = random.choice(EXPLANATION_PROMPTS)
        user_prompt = prompt_template.format(topic=topic)

        yield {
            "text": format_chatml(STEVE_SYSTEM_PROMPT, user_prompt, explanation),
            "source": f"sn-{episode.number}",
            "type": "explanation"
        }


def build_qa_examples(episode: Episode) -> Generator[dict, None, None]:
    """
    Build training examples from Leo-Steve Q&A exchanges.
    """
    qa_pairs = get_qa_pairs(episode, min_answer_length=200)

    for question, answer in qa_pairs:
        # Use Leo's actual question as the user prompt
        yield {
            "text": format_chatml(STEVE_SYSTEM_PROMPT, question, answer),
            "source": f"sn-{episode.number}",
            "type": "qa"
        }


def build_dataset(
    transcripts_dir: Path,
    output_dir: Path,
    train_split: float = 0.9,
    seed: int = 42
) -> dict:
    """
    Build complete training dataset from transcripts.

    Args:
        transcripts_dir: Directory containing transcript files
        output_dir: Directory to write train.jsonl and valid.jsonl
        train_split: Fraction for training (rest goes to validation)
        seed: Random seed for reproducibility

    Returns:
        Statistics dict
    """
    random.seed(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_examples = []
    stats = {
        'episodes_processed': 0,
        'explanation_examples': 0,
        'qa_examples': 0,
        'total_examples': 0
    }

    logger.info(f"Processing transcripts from {transcripts_dir}")

    for episode in iter_transcripts(transcripts_dir):
        stats['episodes_processed'] += 1

        # Build examples from this episode
        for example in build_explanation_examples(episode):
            all_examples.append(example)
            stats['explanation_examples'] += 1

        for example in build_qa_examples(episode):
            all_examples.append(example)
            stats['qa_examples'] += 1

        if stats['episodes_processed'] % 100 == 0:
            logger.info(f"Processed {stats['episodes_processed']} episodes...")

    stats['total_examples'] = len(all_examples)
    logger.info(f"Total examples: {stats['total_examples']}")

    # Shuffle and split
    random.shuffle(all_examples)
    split_idx = int(len(all_examples) * train_split)

    train_examples = all_examples[:split_idx]
    valid_examples = all_examples[split_idx:]

    # Write JSONL files
    train_path = output_dir / "train.jsonl"
    valid_path = output_dir / "valid.jsonl"

    with open(train_path, 'w', encoding='utf-8') as f:
        for example in train_examples:
            f.write(json.dumps(example) + '\n')

    with open(valid_path, 'w', encoding='utf-8') as f:
        for example in valid_examples:
            f.write(json.dumps(example) + '\n')

    stats['train_examples'] = len(train_examples)
    stats['valid_examples'] = len(valid_examples)

    logger.info(f"Wrote {stats['train_examples']} training examples to {train_path}")
    logger.info(f"Wrote {stats['valid_examples']} validation examples to {valid_path}")

    # Also write the system prompt for reference
    prompt_path = output_dir / "system_prompt.txt"
    prompt_path.write_text(STEVE_SYSTEM_PROMPT)

    return stats


def main():
    """CLI tool to build training dataset."""
    import argparse

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description="Build MLX-LM training dataset from transcripts")
    parser.add_argument("transcripts_dir", type=str, help="Directory with transcript files")
    parser.add_argument("output_dir", type=str, help="Output directory for JSONL files")
    parser.add_argument("--train-split", type=float, default=0.9, help="Training split ratio")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")

    args = parser.parse_args()

    stats = build_dataset(
        Path(args.transcripts_dir),
        Path(args.output_dir),
        train_split=args.train_split,
        seed=args.seed
    )

    print("\nDataset Statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
