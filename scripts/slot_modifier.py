"""
slot_modifier.py — Standalone script that modifies slot statuses in Neo4j.

Run this in a separate terminal alongside the booking assistant to simulate
real-world slot changes: admin blocking slots, walk-in bookings, schedule
reopenings, etc.

The booking assistant reads slot status in real-time, so any change made here
is immediately reflected when a patient queries for available slots.

Usage:
    python -m scripts.slot_modifier            # default: every 30 seconds
    python -m scripts.slot_modifier --interval 10   # every 10 seconds
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# Import after dotenv so NEO4J env vars are available
from app.db import HospitalDB


async def modify_slots(db: HospitalDB) -> None:
    """Perform one round of random slot modifications."""
    action = random.choices(
        ["block", "walkin_book", "reopen"],
        weights=[40, 40, 20],
        k=1,
    )[0]

    timestamp = datetime.now().strftime("%H:%M:%S")

    if action == "block":
        # Mark 1-3 AVAILABLE slots as NOT_AVAILABLE (doctor break, emergency)
        count = random.randint(1, 3)
        slots = await db.get_random_slots_by_status("AVAILABLE", count)
        for slot in slots:
            await db.update_slot_status(slot["slot_id"], "NOT_AVAILABLE")
            print(
                f"  [{timestamp}] [BLOCKED] slot {slot['slot_id']} "
                f"({slot.get('doctor_name', '?')}, "
                f"{slot.get('appointment_date', '?')} "
                f"{slot.get('slot_start', '?')}-{slot.get('slot_end', '?')})"
            )
        if not slots:
            print(f"  [{timestamp}] No AVAILABLE slots to block.")

    elif action == "walkin_book":
        # Mark 1-2 AVAILABLE slots as BOOKED (walk-in booking from another system)
        count = random.randint(1, 2)
        slots = await db.get_random_slots_by_status("AVAILABLE", count)
        for slot in slots:
            await db.update_slot_status(slot["slot_id"], "BOOKED")
            print(
                f"  [{timestamp}] [WALK-IN] BOOKED slot {slot['slot_id']} "
                f"({slot.get('doctor_name', '?')}, "
                f"{slot.get('appointment_date', '?')} "
                f"{slot.get('slot_start', '?')}-{slot.get('slot_end', '?')})"
            )
        if not slots:
            print(f"  [{timestamp}] No AVAILABLE slots for walk-in booking.")

    elif action == "reopen":
        # Reopen 1 NOT_AVAILABLE slot back to AVAILABLE (schedule change)
        slots = await db.get_random_slots_by_status("NOT_AVAILABLE", 1)
        for slot in slots:
            await db.update_slot_status(slot["slot_id"], "AVAILABLE")
            print(
                f"  [{timestamp}] [REOPENED] slot {slot['slot_id']} "
                f"({slot.get('doctor_name', '?')}, "
                f"{slot.get('appointment_date', '?')} "
                f"{slot.get('slot_start', '?')}-{slot.get('slot_end', '?')})"
            )
        if not slots:
            print(f"  [{timestamp}] No NOT_AVAILABLE slots to reopen.")


async def main(interval: int) -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    db = HospitalDB(uri, user, password)

    try:
        await db.connect()
        print("=" * 60)
        print("  [~] Slot Modifier -- Running in background")
        print(f"  Interval: every {interval} seconds")
        print("  Press Ctrl+C to stop")
        print("=" * 60)
        print()

        iteration = 0
        while True:
            iteration += 1
            print(f"--- Round {iteration} ---")
            await modify_slots(db)
            print()
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        print("\nSlot modifier stopped.")
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Modify doctor slot statuses in Neo4j")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Seconds between each modification round (default: 30)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.interval))
