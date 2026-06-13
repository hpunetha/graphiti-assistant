"""
main.py — Unified entry point for the hospital booking system.

Usage:
    python main.py                     # Run the booking assistant
    python main.py --seed              # Seed the database, then run assistant
    python main.py --seed-only         # Only seed the database (don't start chat)
    python -m app.assistant            # Run the assistant directly
    python -m app.seed_hospital        # Seed the database directly
    python -m scripts.slot_modifier    # Run the slot modifier in parallel

Logs are written to logs/medbook_YYYYMMDD.log (DEBUG+) and printed to
the console at INFO level. Set APP_TIMEZONE in .env to change the timezone
(default: Asia/Kolkata).
"""

import argparse
import asyncio
import sys

from app.logger import get_logger

log = get_logger("medbook.main")


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

    log.info("MedBook starting (seed=%s seed_only=%s)", args.seed, args.seed_only)

    try:
        if args.seed or args.seed_only:
            print("Seeding hospital data into Neo4j...\n")
            log.info("Running seed_hospital...")
            from app.seed_hospital import main as seed_main
            asyncio.run(seed_main())
            log.info("Seeding complete.")

            if args.seed_only:
                log.info("--seed-only mode: exiting after seed.")
                return

            print("\n" + "-" * 60 + "\n")

        from app.assistant import main as assistant_main
        asyncio.run(assistant_main())

    except KeyboardInterrupt:
        print("\n\nInterrupted. Goodbye!")
        log.info("KeyboardInterrupt at top level — exiting cleanly.")
        sys.exit(0)
    except Exception as exc:
        log.exception("Fatal error in main: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
