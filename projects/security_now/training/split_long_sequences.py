#!/usr/bin/env python3
"""
Pre-split long training sequences to reduce memory usage during training.

This splits examples longer than max_tokens into multiple shorter examples,
preserving the system prompt in each chunk.

Usage:
    python split_long_sequences.py ./training_data --max-tokens 1024
"""
import json
import argparse
from pathlib import Path
import re


# Approximate chars per token (conservative estimate for English)
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """Estimate token count from character count."""
    return int(len(text) / CHARS_PER_TOKEN)


def extract_chatml_parts(text: str) -> tuple[str, str, str] | None:
    """
    Extract system, user, and assistant parts from ChatML formatted text.
    Returns None if parsing fails.
    """
    # Pattern to match ChatML format
    pattern = r'<\|im_start\|>system\n(.*?)<\|im_end\|>\n<\|im_start\|>user\n(.*?)<\|im_end\|>\n<\|im_start\|>assistant\n(.*?)<\|im_end\|>'

    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None


def format_chatml(system: str, user: str, assistant: str) -> str:
    """Format parts back into ChatML."""
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant}<|im_end|>"
    )


def split_text_into_chunks(text: str, max_chars: int) -> list[str]:
    """
    Split text into chunks at sentence boundaries.
    """
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks = []
    current_chunk = []
    current_len = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        if current_len + sentence_len > max_chars and current_chunk:
            # Save current chunk and start new one
            chunks.append(' '.join(current_chunk))
            current_chunk = [sentence]
            current_len = sentence_len
        else:
            current_chunk.append(sentence)
            current_len += sentence_len + 1  # +1 for space

    # Don't forget the last chunk
    if current_chunk:
        chunks.append(' '.join(current_chunk))

    return chunks


def split_example(example: dict, max_tokens: int) -> list[dict]:
    """
    Split a single example into multiple if it exceeds max_tokens.
    """
    text = example.get('text', '')

    if estimate_tokens(text) <= max_tokens:
        return [example]

    # Parse ChatML
    parts = extract_chatml_parts(text)
    if not parts:
        # Can't parse, return as-is with a warning
        print(f"  Warning: Could not parse ChatML, keeping original")
        return [example]

    system, user, assistant = parts

    # Calculate how much space we have for the assistant response
    overhead = len(system) + len(user) + 100  # 100 chars for ChatML tags
    max_assistant_chars = int((max_tokens * CHARS_PER_TOKEN) - overhead)

    if max_assistant_chars < 200:
        # Not enough room, just truncate
        print(f"  Warning: System+user too long, truncating")
        return [example]

    # Split the assistant response
    chunks = split_text_into_chunks(assistant, max_assistant_chars)

    # Create new examples for each chunk
    results = []
    for i, chunk in enumerate(chunks):
        new_example = example.copy()

        # Modify user prompt slightly for continuation chunks
        if i == 0:
            new_user = user
        else:
            new_user = f"{user}\n\n(Continue from where you left off)"

        new_example['text'] = format_chatml(system, new_user, chunk)
        new_example['chunk'] = i + 1
        new_example['total_chunks'] = len(chunks)
        results.append(new_example)

    return results


def process_file(input_path: Path, output_path: Path, max_tokens: int) -> dict:
    """Process a single JSONL file."""
    stats = {
        'original_count': 0,
        'split_count': 0,
        'final_count': 0,
        'long_examples': 0
    }

    examples = []

    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            example = json.loads(line)
            stats['original_count'] += 1

            split_examples = split_example(example, max_tokens)

            if len(split_examples) > 1:
                stats['long_examples'] += 1
                stats['split_count'] += len(split_examples) - 1

            examples.extend(split_examples)

    stats['final_count'] = len(examples)

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        for example in examples:
            f.write(json.dumps(example) + '\n')

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Split long training sequences to reduce memory usage"
    )
    parser.add_argument(
        "data_dir",
        type=str,
        help="Directory containing train.jsonl and valid.jsonl"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Maximum tokens per example (default: 1024)"
    )
    parser.add_argument(
        "--output-suffix",
        type=str,
        default="_split",
        help="Suffix for output files (default: _split)"
    )

    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    print(f"Processing training data in {data_dir}")
    print(f"Max tokens per example: {args.max_tokens}")
    print()

    for filename in ['train.jsonl', 'valid.jsonl']:
        input_path = data_dir / filename
        if not input_path.exists():
            print(f"Skipping {filename} (not found)")
            continue

        output_name = filename.replace('.jsonl', f'{args.output_suffix}.jsonl')
        output_path = data_dir / output_name

        print(f"Processing {filename}...")
        stats = process_file(input_path, output_path, args.max_tokens)

        print(f"  Original examples: {stats['original_count']}")
        print(f"  Long examples split: {stats['long_examples']}")
        print(f"  New examples from splits: {stats['split_count']}")
        print(f"  Final example count: {stats['final_count']}")
        print(f"  Output: {output_path}")
        print()

    print("Done! Update your config to use the new files:")
    print(f'  data: "{data_dir}_split"')
    print()
    print("Or rename the split files to replace the originals:")
    print(f"  mv {data_dir}/train_split.jsonl {data_dir}/train.jsonl")
    print(f"  mv {data_dir}/valid_split.jsonl {data_dir}/valid.jsonl")


if __name__ == "__main__":
    main()
