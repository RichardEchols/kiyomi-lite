#!/usr/bin/env python3
"""
Kiyomi Bot Pool Creator
========================
Interactive helper to create Telegram bots via BotFather.

Run this script, then follow the prompts. For each bot:
  1. Script shows you what to send to BotFather
  2. You copy-paste it into Telegram
  3. Paste the token BotFather gives you back here

Usage:
    python3 scripts/create_bots.py              # Create 30 bots (default)
    python3 scripts/create_bots.py --count 10   # Create 10 bots
    python3 scripts/create_bots.py --add 5      # Add 5 more to existing pool
"""
import json
import random
import re
import string
import sys
from pathlib import Path

POOL_FILE = Path(__file__).parent.parent / "data" / "bot_pool.json"
TOKEN_PATTERN = re.compile(r"^\d{8,15}:[A-Za-z0-9_-]{30,50}$")


def random_suffix(length=5):
    """Generate a random alphanumeric suffix."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def generate_bot_info(index: int):
    """Generate a display name and username for bot #index."""
    suffix = random_suffix()
    display_name = f"Kiyomi #{index:02d}"
    username = f"kiyomi_{suffix}_bot"
    return display_name, username


def load_pool():
    """Load existing pool or return empty structure."""
    if POOL_FILE.exists():
        with open(POOL_FILE) as f:
            return json.load(f)
    return {"bots": []}


def save_pool(pool):
    """Save pool to JSON file."""
    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POOL_FILE, "w") as f:
        json.dump(pool, f, indent=2)


def print_header():
    print()
    print("=" * 56)
    print("       Kiyomi Bot Pool Creator")
    print("=" * 56)
    print()
    print("  This will walk you through creating Telegram bots")
    print("  via @BotFather. Have Telegram open and ready.")
    print()
    print("  Open this link first:")
    print("  https://t.me/BotFather")
    print()
    print("=" * 56)
    print()


def create_bots(count: int, start_index: int = 1):
    """Interactively create bots and collect tokens.
    Saves incrementally after each bot so Ctrl+C won't lose progress.
    Type 'done' or 'quit' to stop early and save.
    """
    pool = load_pool()
    created_count = 0

    for i in range(count):
        num = start_index + i
        display_name, username = generate_bot_info(num)

        print(f"\n--- Bot {num} of {start_index + count - 1} (type 'done' to stop) ---\n")

        # Step 1: /newbot
        print(f"  1. Send this to BotFather:\n")
        print(f"     /newbot\n")

        # Step 2: name
        print(f"  2. When BotFather asks for a name, send:\n")
        print(f"     {display_name}\n")

        # Step 3: username
        print(f"  3. When BotFather asks for a username, send:\n")
        print(f"     {username}\n")
        print(f"     (If taken, try: kiyomi_{random_suffix()}_bot)\n")

        # Step 4: collect token
        while True:
            token = input("  4. Paste the token BotFather gave you: ").strip()

            if token.lower() in ("done", "quit", "q", "exit"):
                print(f"\n     Stopping early. {created_count} bots saved.\n")
                save_pool(pool)
                return created_count

            if token.lower() in ("skip", "s"):
                print("     Skipped.\n")
                break

            if TOKEN_PATTERN.match(token):
                # Ask for actual username in case they had to change it
                actual_username = input(
                    f"     Username (press Enter for {username}): "
                ).strip()
                if not actual_username:
                    actual_username = username
                if not actual_username.endswith("_bot"):
                    actual_username += "_bot"

                bot = {
                    "token": token,
                    "username": actual_username,
                    "display_name": display_name,
                    "claimed": False,
                    "claimed_by": None,
                }
                pool["bots"].append(bot)
                save_pool(pool)  # Save after EACH bot
                created_count += 1
                print(f"     Saved! ({created_count} new, {len(pool['bots'])} total)\n")
                break
            else:
                print("     That doesn't look like a valid token.")
                print("     Format: 123456789:ABCdefGhIjKlMnOpQrStUvWxYz")
                print("     Type 'skip' to skip, 'done' to stop.\n")

    return created_count


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Create Telegram bots for Kiyomi pool")
    parser.add_argument("--count", type=int, default=30, help="Number of bots to create (default: 30)")
    parser.add_argument("--add", type=int, default=0, help="Add N more bots to existing pool")
    args = parser.parse_args()

    print_header()

    pool = load_pool()
    existing_count = len(pool["bots"])

    if args.add > 0:
        count = args.add
        start = existing_count + 1
        print(f"  Adding {count} bots to existing pool ({existing_count} bots).\n")
    else:
        count = args.count
        start = existing_count + 1
        if existing_count > 0:
            print(f"  Pool already has {existing_count} bots.")
            print(f"  Creating {count} more (starting at #{start}).\n")

    input("  Press Enter when you have BotFather open... ")

    created_count = create_bots(count, start)

    pool = load_pool()
    total = len(pool["bots"])
    unclaimed = sum(1 for b in pool["bots"] if not b["claimed"])

    print("\n" + "=" * 56)
    print(f"  Done! Created {created_count} bots.")
    print(f"  Pool total: {total} bots ({unclaimed} available)")
    print(f"  Saved to: {POOL_FILE}")
    print("=" * 56)


if __name__ == "__main__":
    main()
