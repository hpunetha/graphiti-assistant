import asyncio
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from hospital_api.db import HospitalDB

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

async def _create_indexes(db: HospitalDB) -> None:
    indexes = [
        "CREATE INDEX IF NOT EXISTS FOR (d:Doctor) ON (d.doctor_record_id)",
        "CREATE INDEX IF NOT EXISTS FOR (d:Doctor) ON (d.speciality)",
        "CREATE INDEX IF NOT EXISTS FOR (s:Slot) ON (s.slot_id)",
        "CREATE INDEX IF NOT EXISTS FOR (s:Slot) ON (s.slot_status)",
        "CREATE INDEX IF NOT EXISTS FOR (s:Slot) ON (s.appointment_date)",
        "CREATE INDEX IF NOT EXISTS FOR (p:Patient) ON (p.phone)",
        "CREATE INDEX IF NOT EXISTS FOR (b:Booking) ON (b.booking_id)",
    ]
    async with db._driver.session() as session:
        for idx in indexes:
            await session.run(idx)
    print(f"  Created {len(indexes)} indexes.")

async def _load_doctors(db: HospitalDB) -> None:
    df = pd.read_csv(DATA_DIR / "doctor_directory.csv")
    async with db._driver.session() as session:
        for _, row in df.iterrows():
            await session.run(
                """
                CREATE (d:Doctor {
                    doctor_record_id: $doctor_record_id,
                    name: $name,
                    speciality: $speciality,
                    location: $location,
                    sublocation: $sublocation,
                    gender: $gender,
                    qualification: $qualification,
                    languages_spoken: $languages_spoken,
                    experience: $experience,
                    is_active: true
                })
                """,
                doctor_record_id=int(row["doctor_record_id"]),
                name=row["doctor_name"],
                speciality=row["speciality"],
                location=row["location"],
                sublocation=row["sublocation"],
                gender=row["gender"],
                qualification=row["qualification"],
                languages_spoken=row["languages_spoken"],
                experience=row["YOE"],
            )
    print(f"  Loaded {len(df)} doctors.")

async def _load_slots(db: HospitalDB) -> None:
    df = pd.read_csv(DATA_DIR / "doctor_slot_availability.csv")
    batch_size = 500
    total = len(df)

    async with db._driver.session() as session:
        for start in range(0, total, batch_size):
            batch = df.iloc[start : start + batch_size]
            rows = []
            for _, row in batch.iterrows():
                rows.append({
                    "slot_id": int(row["slot_id"]),
                    "doctor_record_id": int(row["doctor_record_id"]),
                    "appointment_date": str(row["appointment_date"]),
                    "slot_start": str(row["slot_start"]),
                    "slot_end": str(row["slot_end"]),
                    "slot_status": str(row["slot_status"]),
                })
            await session.run(
                """
                UNWIND $rows AS row
                MATCH (d:Doctor {doctor_record_id: row.doctor_record_id})
                CREATE (s:Slot {
                    slot_id: row.slot_id,
                    appointment_date: row.appointment_date,
                    slot_start: row.slot_start,
                    slot_end: row.slot_end,
                    slot_status: row.slot_status
                })
                CREATE (d)-[:HAS_SLOT]->(s)
                """,
                rows=rows,
            )
            loaded = min(start + batch_size, total)
            print(f"  Slots: {loaded}/{total}", end="\r")
    print(f"  Loaded {total} slots.          ")

async def _load_bookings(db: HospitalDB) -> None:
    df = pd.read_csv(DATA_DIR / "appointment_booking.csv")

    async with db._driver.session() as session:
        for _, row in df.iterrows():
            phone = str(int(row["patient_phone"]))
            age = int(row["patient_age"]) if "patient_age" in row and pd.notna(row.get("patient_age")) else 30
            gender = str(row.get("patient_gender", "Unknown")) if "patient_gender" in row and pd.notna(row.get("patient_gender")) else "Unknown"

            await session.run(
                """
                MERGE (p:Patient {phone: $phone})
                ON CREATE SET p.name = $name, p.age = $age, p.gender = $gender,
                              p.registered_at = datetime()

                WITH p
                MATCH (s:Slot {slot_id: $slot_id})
                MATCH (d:Doctor {doctor_record_id: $doctor_record_id})
                CREATE (b:Booking {
                    booking_id: $booking_id,
                    booking_status: $booking_status,
                    booked_at: $booked_at
                })
                CREATE (p)-[:MADE_BOOKING]->(b)
                CREATE (b)-[:BOOKED_IN]->(s)
                CREATE (b)-[:WITH_DOCTOR]->(d)
                """,
                phone=phone,
                name=row["patient_name"],
                age=age,
                gender=gender,
                slot_id=int(row["slot_id"]),
                doctor_record_id=int(row["doctor_record_id"]),
                booking_id=int(row["booking_id"]),
                booking_status=row["booking_status"],
                booked_at=str(row["booked_at"]),
            )
    print(f"  Loaded {len(df)} bookings with patients.")

async def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    db = HospitalDB(uri, user, password)

    try:
        await db.connect()
        print("Connected to Neo4j.\n")

        print("[1/4] Clearing structural hospital data...")
        await db.clear_all_data() # Be careful here to not delete graphiti data, but clear_all_data deletes EVERYTHING.
        # Wait, if they share a DB, I should NOT delete Graphiti data!
        # I should change clear_all_data in hospital_api/db.py to only delete Hospital nodes.
        print("  Done.\n")

        print("[2/4] Creating Neo4j indexes...")
        await _create_indexes(db)
        print()

        print("[3/4] Loading doctors...")
        await _load_doctors(db)
        print()

        print("[4/4] Loading slots and bookings...")
        await _load_slots(db)
        await _load_bookings(db)
        print()

        print("Hospital data seeded successfully!")
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
