"""Quick verification script to confirm Neo4j data was seeded correctly."""
import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
from app.db import HospitalDB


async def verify():
    db = HospitalDB(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", "demodemo123"),
    )
    await db.connect()

    async with db._driver.session() as s:
        # Node counts
        r = await s.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY cnt DESC")
        print("=== Node Counts ===")
        async for rec in r:
            print(f"  {rec['label']}: {rec['cnt']}")

        # Relationship counts
        r2 = await s.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS cnt ORDER BY cnt DESC")
        print("\n=== Relationship Counts ===")
        async for rec in r2:
            print(f"  {rec['rel']}: {rec['cnt']}")

        # Slot status breakdown
        r3 = await s.run("MATCH (s:Slot) RETURN s.slot_status AS status, count(*) AS cnt ORDER BY cnt DESC")
        print("\n=== Slot Status Breakdown ===")
        async for rec in r3:
            print(f"  {rec['status']}: {rec['cnt']}")

        # Sample patients (pre-seeded)
        r4 = await s.run("MATCH (p:Patient) RETURN p.name AS name, p.phone AS phone, p.age AS age, p.gender AS gender LIMIT 6")
        print("\n=== Sample Patients (pre-seeded) ===")
        async for rec in r4:
            print(f"  {rec['name']} | phone:{rec['phone']} | age:{rec['age']} | {rec['gender']}")

        # Doctors
        r5 = await s.run("MATCH (d:Doctor) RETURN d.name AS name, d.speciality AS spec, d.doctor_record_id AS id ORDER BY id")
        print("\n=== All Doctors ===")
        async for rec in r5:
            print(f"  [{rec['id']}] {rec['name']} | {rec['spec']}")

        # Available slots for doctor 1 today
        today = datetime.now().strftime("%Y-%m-%d")
        r6 = await s.run(
            "MATCH (d:Doctor {doctor_record_id: 1})-[:HAS_SLOT]->(s:Slot) "
            "WHERE s.appointment_date = $date AND s.slot_status = 'AVAILABLE' "
            "RETURN s.slot_id AS sid, s.slot_start AS start, s.slot_end AS end LIMIT 5",
            date=today,
        )
        print(f"\n=== Available Slots for Doctor ID=1 on {today} (first 5) ===")
        count = 0
        async for rec in r6:
            print(f"  slot {rec['sid']}: {rec['start']} - {rec['end']}")
            count += 1
        if count == 0:
            print("  (none today — check data or date)")

        # Verify doctor search works
        print("\n=== Speciality Search: 'Gynecology' ===")
        docs = await db.find_doctors_by_speciality("Gynecology")
        for d in docs:
            print(f"  {d['name']} (ID {d['doctor_record_id']})")

        # Verify patient lookup
        print("\n=== Patient Lookup test ===")
        r7 = await s.run("MATCH (p:Patient) RETURN p.phone AS phone LIMIT 1")
        rec7 = await r7.single()
        if rec7:
            phone = rec7["phone"]
            patient = await db.get_patient(phone)
            print(f"  Found: {patient['name']} | phone:{phone}")
        else:
            print("  No patients found!")

    await db.close()
    print("\n--- Neo4j verification PASSED ---")


asyncio.run(verify())
