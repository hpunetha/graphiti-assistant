"""One-time script to inject evening slots (17:00-18:50) into Neo4j."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()


EVENING_TIMES = [
    ("17:00", "17:10"), ("17:10", "17:20"), ("17:20", "17:30"),
    ("17:30", "17:40"), ("17:40", "17:50"), ("17:50", "18:00"),
    ("18:00", "18:10"), ("18:10", "18:20"), ("18:20", "18:30"),
    ("18:30", "18:40"), ("18:40", "18:50"), ("18:50", "19:00"),
]


async def main():
    from neo4j import AsyncGraphDatabase
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async with driver.session() as s:
        # Current state
        r = await s.run("MATCH (s:Slot) RETURN max(s.slot_id) AS max_id, count(s) AS total")
        rec = await r.single()
        print(f"Before: total slots={rec['total']}, max slot_id={rec['max_id']}")

        r2 = await s.run("MATCH (s:Slot) WHERE s.slot_start >= '17:00' RETURN count(s) AS cnt")
        rec2 = await r2.single()
        print(f"Before: evening slots (17:00+) = {rec2['cnt']}")

        if rec2["cnt"] > 0:
            print("\nEvening slots already exist. Nothing to do.")
            await driver.close()
            return

        # Fetch all (doctor, date) pairs
        r3 = await s.run(
            "MATCH (d:Doctor)-[:HAS_SLOT]->(s:Slot) "
            "RETURN DISTINCT d.doctor_record_id AS did, s.appointment_date AS dt "
            "ORDER BY did, dt"
        )
        pairs = [(rec["did"], rec["dt"]) async for rec in r3]
        print(f"\nFound {len(pairs)} (doctor, date) pairs. Adding 12 evening slots each...")

        # Get current max slot_id to avoid conflicts
        r4 = await s.run("MATCH (s:Slot) RETURN coalesce(max(s.slot_id), 200000) AS max_id")
        max_rec = await r4.single()
        next_id = max_rec["max_id"] + 1

        created = 0
        batch = []
        for did, dt in pairs:
            for start, end in EVENING_TIMES:
                batch.append({
                    "slot_id": next_id,
                    "doctor_record_id": did,
                    "appointment_date": dt,
                    "slot_start": start,
                    "slot_end": end,
                    "slot_status": "AVAILABLE",
                })
                next_id += 1

        # Insert in batches of 500
        BATCH_SIZE = 500
        for i in range(0, len(batch), BATCH_SIZE):
            chunk = batch[i: i + BATCH_SIZE]
            await s.run(
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
                rows=chunk,
            )
            created += len(chunk)
            print(f"  Inserted {created}/{len(batch)}...", end="\r")

        print(f"\nDone! Created {created} evening slots.\n")

        # Verify
        r5 = await s.run("MATCH (s:Slot) RETURN count(s) AS total")
        rec5 = await r5.single()
        r6 = await s.run("MATCH (s:Slot) WHERE s.slot_start >= '17:00' RETURN count(s) AS cnt")
        rec6 = await r6.single()
        print(f"After: total slots={rec5['total']}, evening slots={rec6['cnt']}")

        # Per doctor check
        print("\nEvening slots per doctor:")
        r7 = await s.run(
            "MATCH (d:Doctor)-[:HAS_SLOT]->(s:Slot) "
            "WHERE s.slot_start >= '17:00' "
            "RETURN d.name AS name, count(s) AS cnt ORDER BY name"
        )
        async for rec in r7:
            print(f"  {rec['name']}: {rec['cnt']}")

    await driver.close()


asyncio.run(main())
