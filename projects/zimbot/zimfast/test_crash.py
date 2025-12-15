#!/usr/bin/env python3
"""
Test script to isolate segfault in zimfast.

Run different tests to determine if the crash is:
1. Specific to entry 963227
2. Cumulative (only after processing many entries)
3. Related to the ZIM file itself

Usage:
    python test_crash.py /path/to/askubuntu.zim [test_name]

Tests:
    direct      - Read entry 963227 directly (no warmup)
    loop        - Read entry 963227 in a loop 100 times
    sequential  - Read entries 963200-963240 sequentially
    cumulative  - Read all entries from 0 to 963230
    skip        - Read every 1000th entry, then 963227
"""

import sys
import os

# Force unbuffered output
os.environ['PYTHONUNBUFFERED'] = '1'

def test_direct(reader):
    """Test: Read entry 963227 directly without any warmup."""
    print("TEST: Direct read of entry 963227")
    print("-" * 50)

    result = reader.get_entry(963227)
    if result:
        path, title, content, mime = result
        print(f"SUCCESS: {path} ({len(content)} bytes)")
    else:
        print("Entry is redirect")

    print(f"Last idx: {reader.get_last_idx()}, step: {reader.get_last_step()}")
    print("PASSED\n")


def test_loop(reader):
    """Test: Read entry 963227 multiple times in a loop."""
    print("TEST: Read entry 963227 in a loop (100 iterations)")
    print("-" * 50)

    for i in range(100):
        result = reader.get_entry(963227)
        if result:
            path, title, content, mime = result
            if i % 20 == 0:
                print(f"  Iteration {i}: {path} ({len(content)} bytes)")
        else:
            print(f"  Iteration {i}: redirect (unexpected!)")

    print("PASSED\n")


def test_sequential(reader):
    """Test: Read entries 963200-963240 sequentially."""
    print("TEST: Sequential read of entries 963200-963240")
    print("-" * 50)

    for idx in range(963200, 963240):
        result = reader.get_entry(idx)
        if result:
            path, title, content, mime = result
            print(f"  {idx}: {path[:50]}... ({len(content)} bytes)")
        else:
            print(f"  {idx}: redirect")
        sys.stdout.flush()

    print("PASSED\n")


def test_cumulative(reader, total_entries):
    """Test: Read all entries from 0 to past the crash point."""
    print("TEST: Cumulative read from 0 to 963235")
    print("-" * 50)
    print("This simulates what the indexer does...")

    target = min(963235, total_entries)
    text_count = 0
    redirect_count = 0

    for idx in range(target):
        result = reader.get_entry(idx)

        if result:
            path, title, content, mime = result
            if mime and mime.startswith('text/'):
                text_count += 1
        else:
            redirect_count += 1

        # Progress every 100k entries
        if idx % 100000 == 0:
            print(f"  Progress: {idx}/{target} ({idx*100//target}%) - "
                  f"{text_count} text, {redirect_count} redirects")
            sys.stdout.flush()

        # Extra logging near crash point
        if idx >= 963220:
            if result:
                print(f"  >>> {idx}: {result[0][:40]}... ({len(result[2])} bytes)")
            else:
                print(f"  >>> {idx}: redirect")
            sys.stdout.flush()

    print(f"Final: {text_count} text entries, {redirect_count} redirects")
    print("PASSED\n")


def test_skip(reader, total_entries):
    """Test: Skip through file, then read crash entry."""
    print("TEST: Skip read (every 1000th entry) then 963227")
    print("-" * 50)

    # Read every 1000th entry up to crash point
    for idx in range(0, 963000, 1000):
        result = reader.get_entry(idx)
        if idx % 100000 == 0:
            print(f"  Sampled entry {idx}")

    print("  Now reading 963227...")
    result = reader.get_entry(963227)
    if result:
        path, title, content, mime = result
        print(f"  SUCCESS: {path} ({len(content)} bytes)")

    print("PASSED\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    zim_path = sys.argv[1]
    test_name = sys.argv[2] if len(sys.argv) > 2 else "all"

    print(f"ZIM file: {zim_path}")
    print(f"Test: {test_name}")
    print("=" * 60)

    # Import here so errors are clear
    from zimfast import ZimReader

    print("Opening archive...")
    reader = ZimReader(zim_path)
    total = reader.entry_count()
    print(f"Total entries: {total}")
    print()

    tests = {
        "direct": lambda: test_direct(reader),
        "loop": lambda: test_loop(reader),
        "sequential": lambda: test_sequential(reader),
        "cumulative": lambda: test_cumulative(reader, total),
        "skip": lambda: test_skip(reader, total),
    }

    if test_name == "all":
        for name, test_func in tests.items():
            try:
                test_func()
            except Exception as e:
                print(f"FAILED: {e}")
                print(f"Last idx: {reader.get_last_idx()}, "
                      f"step: {reader.get_last_step()} "
                      f"({reader.get_step_name(reader.get_last_step())})")
                raise
    elif test_name in tests:
        tests[test_name]()
    else:
        print(f"Unknown test: {test_name}")
        print(f"Available: {', '.join(tests.keys())}")
        sys.exit(1)

    print("=" * 60)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
