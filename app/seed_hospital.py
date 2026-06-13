"""
seed_hospital.py — Load hospital data into Neo4j and seed Graphiti KG.

This script:
  1. Clears ALL existing Neo4j data (nodes, relationships, Graphiti data)
  2. Creates structured nodes (Doctor, Slot, Patient, Booking) from CSVs
  3. Creates Neo4j indexes for fast lookups
  4. Seeds Graphiti with doctor profiles and symptom-to-speciality mappings

Usage:
    docker compose up -d
    python -m app.seed_hospital
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from app.db import HospitalDB
from app.memory import GraphMemory, quiet_graphiti_logs

load_dotenv()
quiet_graphiti_logs()

DATA_DIR = Path(__file__).parent.parent / "data"


async def _create_indexes(db: HospitalDB) -> None:
    """Create Neo4j indexes for fast Cypher lookups."""
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
    """Load doctors from CSV into Neo4j."""
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
    """Load slot availability from CSV and create HAS_SLOT relationships."""
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
    """Load bookings from CSV, creating Patient and Booking nodes."""
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


async def _seed_graphiti(memory: GraphMemory) -> None:
    """Seed Graphiti knowledge graph with doctor profiles and symptom mappings."""
    group_id = "hospital"

    # --- Doctor profiles ---
    doctors_df = pd.read_csv(DATA_DIR / "doctor_directory.csv")
    print("  Seeding doctor profiles into Graphiti...")

    for _, doc in doctors_df.iterrows():
        episode = (
            f"{doc['doctor_name']} is a {doc['speciality']} specialist "
            f"located in {doc['location']}, {doc['sublocation']}. "
            f"Gender: {doc['gender']}. "
            f"Qualification: {doc['qualification']}. "
            f"Languages spoken: {doc['languages_spoken']}. "
            f"Experience: {doc['YOE']}. "
            f"Doctor ID: {doc['doctor_record_id']}."
        )
        await memory.remember(episode, user_id=group_id, source_desc="doctor_profile")
        print(f"    [ok] {doc['doctor_name']}")

    # --- Symptom-to-speciality mappings ---
    symptom_df = pd.read_csv(DATA_DIR / "symptom_speciality_map.csv")
    print("  Seeding symptom-to-speciality mappings...")

    for _, row in symptom_df.iterrows():
        episode = (
            f"For symptoms such as {row['symptoms_keywords']}, "
            f"the recommended speciality is {row['speciality']}. "
            f"{row['description']}. "
            f"Applicable age group: {row['age_group']}. "
            f"Gender relevance: {row['gender_relevance']}."
        )
        await memory.remember(episode, user_id=group_id, source_desc="symptom_mapping")
        print(f"    [ok] {row['speciality']}")

    # --- Age-based routing rules ---
    age_rules = [
        "Children under 14 years of age should be directed to pediatric specialists: "
        "General Pediatrics for general child health, Pediatric Pulmonology for "
        "breathing or lung issues, Pediatric Gastroenterology for digestive issues, "
        "or Pediatric Intensive Care Unit (PICU) for emergencies.",

        "Adults (14 years and above) with general health concerns like fever, cold, "
        "headache, or body pain should see a General Physician first.",

        "Women and girls above 12 years with reproductive health concerns should "
        "consult a Gynecology specialist. For pregnancy-related imaging and "
        "high-risk pregnancy, Fetal Medicine & Ultrasonography is recommended.",
    ]
    print("  Seeding age-based routing rules...")
    for rule in age_rules:
        await memory.remember(rule, user_id=group_id, source_desc="routing_rule")
        print("    [ok] rule seeded")


async def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your .env file first.")

    db = HospitalDB(uri, user, password)
    memory = GraphMemory(uri, user, password)

    try:
        await db.connect()
        print("Connected to Neo4j.\n")

        # Step 1: Clear everything
        print("[1/6] Clearing all existing data...")
        await db.clear_all_data()
        print("  Done.\n")

        # Step 2: Build Graphiti indices
        print("[2/6] Building Graphiti indices...")
        await memory.setup()
        print("  Done.\n")

        # Step 3: Create Neo4j indexes
        print("[3/6] Creating Neo4j indexes...")
        await _create_indexes(db)
        print()

        # Step 4: Load structured data from CSVs
        print("[4/6] Loading doctors...")
        await _load_doctors(db)
        print()

        print("[5/6] Loading slots and bookings...")
        await _load_slots(db)
        await _load_bookings(db)
        print()

        # Step 5: Seed Graphiti knowledge graph
        print("[6/6] Seeding Graphiti knowledge graph...")
        await _seed_graphiti(memory)
        print()

        print("\n" + "=" * 60)
        print("Hospital data seeded successfully!")
        print("Open http://localhost:7474 and try:")
        print("  MATCH (d:Doctor) RETURN d.name, d.speciality")
        print("  MATCH (s:Slot) RETURN count(s)")
        print("  MATCH (p:Patient) RETURN p.name, p.phone")
        print("=" * 60)
        print("")

    finally:
        await db.close()
        await memory.close()


if __name__ == "__main__":
    asyncio.run(main())
