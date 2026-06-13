"""
main.py — Unified entry point for the hospital booking system.

Usage:
    python main.py                     # Run the booking assistant
    python main.py --seed              # Seed the database, then run assistant
    python main.py --seed-only         # Only seed the database (don't start chat)
    python -m app.assistant            # Run the assistant directly
    python -m app.seed_hospital        # Seed the database directly
    python -m scripts.slot_modifier    # Run the slot modifier in parallel
"""

import argparse
import asyncio
import sys


def main():
    parser = argparse.ArgumentParser(
        description="MedBook — Doctor Appointment Booking System"
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed the Neo4j database before starting the assistant",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only seed the database (don't start the assistant)",
    )
    args = parser.parse_args()

    if args.seed or args.seed_only:
        print("Seeding hospital data into Neo4j...\n")
        from app.seed_hospital import main as seed_main
        asyncio.run(seed_main())

        if args.seed_only:
            return

        print("\n" + "-" * 60 + "\n")

    from app.assistant import main as assistant_main
    asyncio.run(assistant_main())


if __name__ == "__main__":
    main()
