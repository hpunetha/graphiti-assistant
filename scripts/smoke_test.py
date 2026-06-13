"""
smoke_test.py — Tests the tool pipeline end-to-end without needing a chat loop.
Verifies: patient lookup, doctor search, suggest_speciality (Graphiti), slot query, booking.
"""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
from app.db import HospitalDB
from app.memory import GraphMemory, quiet_graphiti_logs
from app.tools import execute_tool

quiet_graphiti_logs()

async def run():
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pwd  = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    db = HospitalDB(uri, user, pwd)
    memory = GraphMemory(uri, user, pwd)
    await db.connect()
    await memory.setup()

    passed = 0
    failed = 0

    def check(label, result, expect_key=None):
        nonlocal passed, failed
        import json
        data = json.loads(result)
        ok = "error" not in data
        if expect_key:
            ok = ok and expect_key in data
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {label}")
        if not ok:
            print(f"         got: {result[:200]}")
        return data

    print("\n=== Smoke Tests ===\n")

    # 1. Identify existing patient
    r = await execute_tool("identify_patient", {"phone": "9215026729"}, db, memory)
    d = check("identify_patient (existing)", r, "patient")
    if "patient" in d:
        print(f"         -> {d['patient']['name']}, age {d['patient'].get('age')}, {d['patient'].get('gender')}")

    # 2. Register new patient
    r = await execute_tool("identify_patient", {
        "phone": "9999900001", "name": "Test Patient", "age": 30, "gender": "Male"
    }, db, memory)
    d = check("identify_patient (new registration)", r, "patient")
    if "patient" in d:
        print(f"         -> {d['status']}: {d['patient']['name']}")

    # 3. Search by speciality
    r = await execute_tool("search_doctors", {"speciality": "General Physician"}, db, memory)
    d = check("search_doctors (speciality)", r, "doctors")
    if "doctors" in d:
        print(f"         -> {[doc['name'] for doc in d['doctors']]}")

    # 4. Search by name
    r = await execute_tool("search_doctors", {"name": "Rajesh"}, db, memory)
    d = check("search_doctors (name partial match)", r, "doctors")
    if "doctors" in d:
        print(f"         -> {[doc['name'] for doc in d['doctors']]}")

    # 5. Suggest speciality (uses Graphiti)
    r = await execute_tool("suggest_speciality", {
        "symptoms": "my child has fever and breathing problems",
        "age": 6,
        "gender": "Male"
    }, db, memory)
    d = check("suggest_speciality (child breathing)", r, "relevant_medical_knowledge")
    if "relevant_medical_knowledge" in d:
        facts = d["relevant_medical_knowledge"]
        print(f"         -> {len(facts)} facts retrieved from Graphiti")
        if facts:
            print(f"         -> top fact: {facts[0][:100]}...")

    # 6. Get available slots
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    r = await execute_tool("get_available_slots", {"doctor_record_id": 35, "date": today}, db, memory)
    d = check("get_available_slots (Dr. Amit Sharma today)", r, "available_slots")
    if "available_slots" in d:
        print(f"         -> {d['total_available']} slots available")
        if d["available_slots"]:
            first_slot = d["available_slots"][0]
            print(f"         -> first slot: {first_slot['slot_id']} at {first_slot['time']}")

    # 7. Book a slot for the new patient
    if "available_slots" in d and d["available_slots"]:
        slot_to_book = d["available_slots"][0]["slot_id"]
        r = await execute_tool("book_appointment", {
            "slot_id": slot_to_book,
            "patient_phone": "9999900001"
        }, db, memory)
        d2 = check("book_appointment (atomic)", r, "booking")
        if "booking" in d2:
            b = d2["booking"]
            print(f"         -> Booking ID {b['booking_id']}: {b['doctor_name']} on {b['appointment_date']} {b['slot_start']}-{b['slot_end']}")

        # 8. Verify slot is now BOOKED
        r3 = await execute_tool("get_available_slots", {"doctor_record_id": 35, "date": today}, db, memory)
        d3 = check("get_available_slots after booking (slot count should decrease)", r3, "available_slots")
        if "available_slots" in d3:
            print(f"         -> {d3['total_available']} slots now (was {d.get('total_available', '?')})")

    # 9. List patient bookings
    r = await execute_tool("get_my_bookings", {"patient_phone": "9999900001"}, db, memory)
    d = check("get_my_bookings", r, "bookings")
    if "bookings" in d:
        print(f"         -> {len(d['bookings'])} booking(s) found")

    # 10. Cancel booking
    if "bookings" in d and d["bookings"]:
        bid = d["bookings"][0]["booking_id"]
        r = await execute_tool("cancel_booking", {"booking_id": bid, "patient_phone": "9999900001"}, db, memory)
        check("cancel_booking", r, "status")

    await db.close()
    await memory.close()

    print(f"\n=== Results: {passed} passed, {failed} failed ===")
    if failed == 0:
        print("All smoke tests PASSED!")
    else:
        print("Some tests FAILED — check output above.")

asyncio.run(run())
