"""
tools.py — Tool definitions and implementations for the booking agent.

Each tool maps to a HospitalApiClient method or a Graphiti search. The OpenAI
function-calling schema is defined here, along with the dispatch function
that executes tools by name.

Tools:
    identify_patient         — Look up or register a patient by phone
    search_doctors           — Find doctors by speciality or name
    suggest_speciality       — Given symptoms/age/gender, suggest speciality
    get_available_slots      — List available slots for a doctor on a date
    get_next_available_date  — Find nearest future date with open slots
    book_appointment         — Book a specific slot for a patient
    get_my_bookings          — List a patient's existing bookings
    reschedule_booking       — Atomically move a booking to a new slot
    cancel_booking           — Cancel an existing booking
    record_patient_fact      — Record an allergy, symptom, or preference
    recall_patient_history   — Retrieve patient's history/preferences
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.api_client import HospitalApiClient
from app.memory import GraphMemory
from app.ontology import (
    EXTRACTION_INSTRUCTIONS,
    PATIENT_EDGE_TYPE_MAP,
    PATIENT_EDGE_TYPES,
    PATIENT_ENTITY_TYPES,
)

# Time-of-day slot buckets (HH:MM strings, lexicographic comparison works for HH:MM)
TIME_BUCKETS: dict[str, tuple[str, str]] = {
    "morning":   ("09:00", "12:00"),   # before noon
    "afternoon": ("12:00", "17:00"),   # noon to 5 PM
    "evening":   ("17:00", "19:00"),   # 5 PM to 7 PM
}

# ──────────────────────────────────────────────────────────────────────
# OpenAI function-calling tool schemas
# ──────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "identify_patient",
            "description": (
                "Look up a patient by phone number. If the patient exists, "
                "returns their profile. If not found, ask for name, age, and gender "
                "ONE AT A TIME across separate turns if needed — do NOT demand all three "
                "at once. Call this tool again once you have all three to register them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Patient's 10-digit phone number",
                    },
                    "name": {
                        "type": "string",
                        "description": "Patient's full name (required for new registration)",
                    },
                    "age": {
                        "type": "integer",
                        "description": "Patient's age in years (required for new registration)",
                    },
                    "gender": {
                        "type": "string",
                        "enum": ["Male", "Female", "Other"],
                        "description": "Patient's gender (required for new registration)",
                    },
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_doctors",
            "description": (
                "Search for doctors by speciality or by name. Provide either "
                "speciality or name (or both). Returns full doctor details "
                "(name, speciality, qualification, experience, languages). "
                "Present only name and speciality by default; share other "
                "details only if the user explicitly asks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "speciality": {
                        "type": "string",
                        "description": "Medical speciality to search for (e.g., 'Gynecology', 'General Physician')",
                    },
                    "name": {
                        "type": "string",
                        "description": "Doctor's name to search for (partial match supported)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_speciality",
            "description": (
                "Given a patient's symptoms, age, and gender, suggest the most "
                "appropriate medical speciality. Uses both curated symptom "
                "mappings and AI reasoning to recommend the right specialist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symptoms": {
                        "type": "string",
                        "description": "Patient's symptoms or health concerns described in natural language",
                    },
                    "age": {
                        "type": "integer",
                        "description": "Patient's age (helps route children to pediatric specialists)",
                    },
                    "gender": {
                        "type": "string",
                        "description": "Patient's gender (helps route to gender-specific specialists)",
                    },
                },
                "required": ["symptoms"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": (
                "Get available appointment slots for a specific doctor on a "
                "specific date. Returns slot IDs, start/end times. Only shows "
                "slots with status AVAILABLE. You can handle various time requests: "
                "1) Time of day ('morning', 'afternoon', 'evening') using the time_of_day parameter. "
                "2) Specific time ranges ('between 11am - 2pm') using after_time and before_time. "
                "3) Open-ended times ('before 3pm', 'after 4pm') using before_time or after_time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_record_id": {
                        "type": "integer",
                        "description": "The doctor's unique record ID",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format (e.g., '2026-06-15')",
                    },
                    "time_of_day": {
                        "type": "string",
                        "enum": ["morning", "afternoon", "evening"],
                        "description": "Optional coarse time bucket. Use after_time/before_time for precise requests like 'before 3pm' or 'between 11am - 2pm'.",
                    },
                    "after_time": {
                        "type": "string",
                        "description": "Optional precise start time in HH:MM format (e.g., '16:30' for 4:30 PM). Use for 'after 4pm' or the start of a range.",
                    },
                    "before_time": {
                        "type": "string",
                        "description": "Optional precise end time in HH:MM format (e.g., '18:00' for 6:00 PM). Use for 'before 3pm' or the end of a range.",
                    },
                },
                "required": ["doctor_record_id", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book a specific slot for a patient. The slot must be AVAILABLE. "
                "If the slot was taken or blocked since the patient last checked, "
                "returns a failure message. The patient must be identified first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_id": {
                        "type": "integer",
                        "description": "The exact slot ID to book (from get_available_slots). CRITICAL: You must carefully match the user's chosen time to the correct slot_id.",
                    },
                    "patient_phone": {
                        "type": "string",
                        "description": "The patient's phone number (must be registered)",
                    },
                },
                "required": ["slot_id", "patient_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_bookings",
            "description": (
                "List all bookings for a patient. Shows confirmed and cancelled "
                "appointments with doctor name, date, and time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_phone": {
                        "type": "string",
                        "description": "The patient's phone number",
                    },
                },
                "required": ["patient_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": (
                "Cancel an existing confirmed booking. The slot will be freed "
                "back to AVAILABLE status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {
                        "type": "integer",
                        "description": "The booking ID to cancel",
                    },
                    "patient_phone": {
                        "type": "string",
                        "description": "The patient's phone number (for verification)",
                    },
                },
                "required": ["booking_id", "patient_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_available_date",
            "description": (
                "Find the nearest future date that has available slots for a doctor. "
                "Use this when the requested date has no slots, instead of guessing dates one by one. "
                "Supports the same time filters as get_available_slots: time_of_day ('morning', 'afternoon', 'evening'), "
                "or precise ranges using after_time and before_time (e.g. 'before 3pm', 'between 11am - 2pm')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_record_id": {
                        "type": "integer",
                        "description": "The doctor's unique record ID",
                    },
                    "time_of_day": {
                        "type": "string",
                        "enum": ["morning", "afternoon", "evening"],
                        "description": "Optional coarse time bucket. Use after_time/before_time for precise requests like 'before 3pm' or 'between 11am - 2pm'.",
                    },
                    "after_time": {
                        "type": "string",
                        "description": "Optional precise start time in HH:MM format (e.g., '16:30'). Use for 'after 4pm' or the start of a range.",
                    },
                    "before_time": {
                        "type": "string",
                        "description": "Optional precise end time in HH:MM format (e.g., '18:00'). Use for 'before 3pm' or the end of a range.",
                    },
                },
                "required": ["doctor_record_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_booking",
            "description": (
                "Atomically reschedule a booking to a new slot. "
                "If the new slot is unavailable, the old booking is kept intact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {
                        "type": "integer",
                        "description": "The existing booking ID to reschedule",
                    },
                    "patient_phone": {
                        "type": "string",
                        "description": "The patient's phone number",
                    },
                    "new_slot_id": {
                        "type": "integer",
                        "description": "The exact ID of the new slot to book. CRITICAL: You must carefully match the user's chosen time to the correct slot_id from get_available_slots.",
                    },
                },
                "required": ["booking_id", "patient_phone", "new_slot_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_patient_fact",
            "description": (
                "Save important, long-term patient information into their history (e.g., "
                "allergies, evolving symptoms, chronic conditions, time preferences, or doctor preferences). "
                "Do NOT save transactional data like appointment dates or slots."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "The patient's phone number",
                    },
                    "fact": {
                        "type": "string",
                        "description": "The semantic fact to remember (e.g., 'Patient prefers evening slots', 'Allergic to penicillin')",
                    },
                },
                "required": ["phone", "fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_patient_history",
            "description": (
                "Retrieve a patient's historical profile, including past symptoms, "
                "allergies, relationships (e.g. 'caller is father'), and preferences. "
                "Use this to answer questions like 'what can you tell me about my son' "
                "by querying for 'son', 'father', or the patient's name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "The patient's phone number",
                    },
                    "query": {
                        "type": "string",
                        "description": "What specifically to look for (e.g., 'allergies', 'time preferences', 'son', or 'general health history')",
                    },
                },
                "required": ["phone", "query"],
            },
        },
    },
]


# ──────────────────────────────────────────────────────────────────────
# Tool execution dispatcher
# ──────────────────────────────────────────────────────────────────────


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    db: HospitalApiClient,
    memory: GraphMemory,
) -> str:
    """Execute a tool by name and return a JSON string result.

    The result is always a JSON string that gets sent back to the LLM
    as a tool response message.
    """
    try:
        result = await _dispatch(name, arguments, db, memory)
        return json.dumps(result, default=str, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _dispatch(
    name: str,
    args: dict[str, Any],
    db: HospitalApiClient,
    memory: GraphMemory,
) -> Any:
    """Internal dispatcher — calls the right DB/memory method."""

    if name == "identify_patient":
        return await _identify_patient(db, **args)
    elif name == "search_doctors":
        return await _search_doctors(db, **args)
    elif name == "suggest_speciality":
        return await _suggest_speciality(db, memory, **args)
    elif name == "get_available_slots":
        return await _get_available_slots(db, **args)
    elif name == "book_appointment":
        return await _book_appointment(db, memory, **args)
    elif name == "get_my_bookings":
        return await _get_my_bookings(db, **args)
    elif name == "cancel_booking":
        return await _cancel_booking(db, **args)
    elif name == "get_next_available_date":
        return await _get_next_available_date(db, **args)
    elif name == "reschedule_booking":
        return await _reschedule_booking(db, **args)
    elif name == "record_patient_fact":
        return await _record_patient_fact(memory, **args)
    elif name == "recall_patient_history":
        return await _recall_patient_history(memory, **args)
    else:
        return {"error": f"Unknown tool: {name}"}


# ──────────────────────────────────────────────────────────────────────
# Tool implementations
# ──────────────────────────────────────────────────────────────────────


async def _identify_patient(
    db: HospitalApiClient,
    phone: str,
    name: str | None = None,
    age: int | None = None,
    gender: str | None = None,
) -> dict:
    """Look up or register a patient."""
    patient = await db.get_patient(phone)
    if patient:
        return {"status": "found", "patient": patient}

    # New patient — need name, age, gender to register
    if not all([name, age, gender]):
        return {
            "status": "not_found",
            "message": (
                "No patient found with this phone number. "
                "Please ask for the patient's full name, age, and gender "
                "to register them."
            ),
        }

    patient = await db.register_patient(phone, name, age, gender)
    return {"status": "registered", "patient": patient}


async def _search_doctors(
    db: HospitalApiClient,
    speciality: str | None = None,
    name: str | None = None,
) -> dict:
    """Search for doctors by speciality and/or name."""
    results = []

    if name:
        results = await db.find_doctor_by_name(name)
    elif speciality:
        results = await db.find_doctors_by_speciality(speciality)
    else:
        # No filter — return all specialities available
        specialities = await db.get_all_specialities()
        return {
            "message": "Please specify a speciality or doctor name. Available specialities:",
            "specialities": specialities,
        }

    if not results:
        return {"message": "No doctors found matching your criteria."}

    doctors = []
    for doc in results:
        doctors.append({
            "doctor_record_id": doc["doctor_record_id"],
            "name": doc["name"],
            "speciality": doc["speciality"],
            "gender": doc["gender"],
            "qualification": doc["qualification"],
            "experience": doc["experience"],
            "languages": doc["languages_spoken"],
        })
    return {"doctors": doctors}


async def _suggest_speciality(
    db: HospitalApiClient,
    memory: GraphMemory,
    symptoms: str,
    age: int | None = None,
    gender: str | None = None,
) -> dict:
    """Suggest the right speciality using Graphiti semantic search."""
    # Build a rich query combining symptoms with demographics
    query_parts = [f"symptoms: {symptoms}"]
    if age is not None:
        query_parts.append(f"patient age: {age} years")
    if gender:
        query_parts.append(f"patient gender: {gender}")

    query = ", ".join(query_parts)

    # Search Graphiti for relevant symptom mappings and doctor profiles
    facts = await memory.recall_medical_knowledge(query, limit=10)

    # Also get available specialities from the DB for context
    specialities = await db.get_all_specialities()

    return {
        "relevant_medical_knowledge": facts,
        "available_specialities": specialities,
        "patient_info": {"symptoms": symptoms, "age": age, "gender": gender},
        "instruction": (
            "Based on the medical knowledge above and patient info, "
            "recommend the most appropriate speciality and explain why. "
            "Consider age-based routing (children under 14 → pediatric specialists) "
            "and gender-specific needs."
        ),
    }


async def _get_available_slots(
    db: HospitalApiClient,
    doctor_record_id: int,
    date: str,
    time_of_day: str | None = None,
    after_time: str | None = None,
    before_time: str | None = None,
) -> dict:
    """Get available slots for a doctor on a date, with optional time-of-day filtering.

    Filters out past slots automatically when the requested date is today
    (using APP_TIMEZONE, default Asia/Kolkata).
    """
    tz_name = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    is_today = (date == today_str)

    # Resolve time-of-day bucket if precise times are not provided
    if not after_time and not before_time and time_of_day and time_of_day in TIME_BUCKETS:
        after_time, before_time = TIME_BUCKETS[time_of_day]

    # For today: ensure we never show already-passed slots
    if is_today:
        # Push after_time forward to current time if needed
        if after_time is None or current_time > after_time:
            after_time = current_time

    slots = await db.get_available_slots(
        doctor_record_id, date,
        after_time=after_time,
        before_time=before_time,
    )

    if not slots:
        bucket_label = f" in the {time_of_day}" if time_of_day else ""
        return {
            "message": f"No available slots for this doctor on {date}{bucket_label}.",
            "suggestion": "Try a different date, time of day, or another doctor.",
        }

    formatted = []
    for s in slots:
        formatted.append({
            "slot_id": s["slot_id"],
            "time": f"{s['slot_start']} - {s['slot_end']}",
            "date": s["appointment_date"],
            "doctor_name": s.get("doctor_name", ""),
        })

    return {
        "available_slots": formatted,
        "total_available": len(formatted),
        "date": date,
        "time_of_day_filter": time_of_day or "all",
    }


async def _book_appointment(
    db: HospitalApiClient,
    memory: GraphMemory,
    slot_id: int,
    patient_phone: str,
) -> dict:
    """Atomically book a slot."""
    # Verify patient exists
    patient = await db.get_patient(patient_phone)
    if not patient:
        return {
            "status": "error",
            "message": "Patient not found. Please register first using identify_patient.",
        }

    # Attempt atomic booking
    booking = await db.book_slot(slot_id, patient_phone, patient["name"])

    if not booking:
        return {
            "status": "slot_unavailable",
            "message": (
                "Sorry, this slot is no longer available. "
                "It may have been booked by someone else or blocked by the clinic. "
                "Please check available slots again for updated availability."
            ),
        }

    # Fire-and-forget: record the doctor visit in Graphiti so future recalls can
    # answer "which doctor did I see for X?" The task runs in the background and
    # does not block the booking confirmation returned to the LLM.
    doctor_name = booking.get("doctor_name", "Unknown")
    speciality = booking.get("speciality", "")
    appt_date = booking.get("appointment_date", "")
    fact = f"Patient booked an appointment with Dr. {doctor_name} ({speciality}) on {appt_date}"

    async def _remember_safely() -> None:
        try:
            await memory.remember(text=fact, user_id=patient_phone, source_desc="booking")
        except Exception:
            pass  # never let background memory writes break the booking flow

    asyncio.create_task(_remember_safely())

    return {
        "status": "confirmed",
        "booking": booking,
        "message": "Appointment booked successfully!",
    }


async def _get_my_bookings(db: HospitalApiClient, patient_phone: str) -> dict:
    """Get all bookings for a patient."""
    bookings = await db.get_patient_bookings(patient_phone)

    if not bookings:
        return {"message": "No bookings found for this phone number."}

    formatted = []
    for b in bookings:
        formatted.append({
            "booking_id": b["booking_id"],
            "status": b["booking_status"],
            "doctor": b.get("doctor_name", ""),
            "doctor_record_id": b.get("doctor_record_id", None),
            "speciality": b.get("speciality", ""),
            "date": b.get("appointment_date", ""),
            "time": f"{b.get('slot_start', '')} - {b.get('slot_end', '')}",
        })

    return {"bookings": formatted}


async def _cancel_booking(
    db: HospitalApiClient,
    booking_id: int,
    patient_phone: str,
) -> dict:
    """Cancel a booking."""
    success = await db.cancel_booking(booking_id, patient_phone)

    if success:
        return {
            "status": "cancelled",
            "message": f"Booking {booking_id} has been cancelled. The slot is now available again.",
        }
    else:
        return {
            "status": "error",
            "message": (
                "Could not cancel this booking. Either the booking ID is invalid, "
                "the phone number doesn't match, or it was already cancelled."
            ),
        }


async def _get_next_available_date(
    db: HospitalApiClient,
    doctor_record_id: int,
    time_of_day: str | None = None,
    after_time: str | None = None,
    before_time: str | None = None,
) -> dict:
    """Find the nearest future date with available slots."""
    tz_name = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    if not after_time and not before_time and time_of_day and time_of_day in TIME_BUCKETS:
        after_time, before_time = TIME_BUCKETS[time_of_day]

    # For today check: push after_time if needed
    today_after_time = after_time
    if today_after_time is None or current_time > today_after_time:
        today_after_time = current_time

    # Try today first
    date = await db.get_next_available_date(
        doctor_record_id, from_date=today_str, after_time=today_after_time, before_time=before_time
    )

    if date == today_str:
        return {"next_available_date": date, "message": "There are slots available today."}

    # If today didn't work, try tomorrow onwards with the original time filter
    tomorrow_str = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).strftime("%Y-%m-%d")
    date = await db.get_next_available_date(
        doctor_record_id, from_date=tomorrow_str, after_time=after_time, before_time=before_time
    )

    if date:
        return {"next_available_date": date}
    else:
        return {"message": "Could not find any future available dates for this doctor."}


async def _reschedule_booking(
    db: HospitalApiClient,
    booking_id: int,
    patient_phone: str,
    new_slot_id: int,
) -> dict:
    """Reschedule a booking to a new slot atomically."""
    booking = await db.reschedule_booking(booking_id, patient_phone, new_slot_id)

    if not booking:
        return {
            "status": "error",
            "message": (
                "Could not reschedule. The new slot might be taken, or the old "
                "booking ID/phone is incorrect."
            ),
        }

    return {
        "status": "rescheduled",
        "booking": booking,
        "message": "Appointment rescheduled successfully!",
    }


async def _record_patient_fact(memory: GraphMemory, phone: str, fact: str) -> dict:
    """Record a semantic fact about the patient in Graphiti with typed ontology."""
    await memory.remember(
        text=fact,
        user_id=phone,
        source_desc="patient_fact",
        entity_types=PATIENT_ENTITY_TYPES,
        edge_types=PATIENT_EDGE_TYPES,
        edge_type_map=PATIENT_EDGE_TYPE_MAP,
        instructions=EXTRACTION_INSTRUCTIONS,
    )
    return {"status": "saved", "message": f"Recorded patient fact: {fact}"}


async def _recall_patient_history(memory: GraphMemory, phone: str, query: str) -> dict:
    """Retrieve patient history from Graphiti using typed, scoped retrieval."""
    facts = await memory.recall_patient_facts(query=query, patient_phone=phone, limit=5)
    if not facts:
        return {"message": "No relevant history found."}
    return {"history": facts}
