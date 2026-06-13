"""
tools.py — Tool definitions and implementations for the booking agent.

Each tool maps to a HospitalDB method or a Graphiti search. The OpenAI
function-calling schema is defined here, along with the dispatch function
that executes tools by name.

Tools:
    identify_patient     — Look up or register a patient by phone
    search_doctors       — Find doctors by speciality or name
    suggest_speciality   — Given symptoms/age/gender, suggest speciality
    get_available_slots  — List available slots for a doctor on a date
    book_appointment     — Book a specific slot for a patient
    get_my_bookings      — List a patient's existing bookings
    cancel_booking       — Cancel an existing booking
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db import HospitalDB
from app.memory import GraphMemory

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
                "speciality or name (or both). Returns doctor details including "
                "qualifications, experience, and languages."
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
                "slots with status AVAILABLE. Optionally filter by time_of_day "
                "(morning/afternoon/evening) or let the user specify an exact time."
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
                        "description": (
                            "Optional time-of-day bucket: "
                            "'morning' (before 12:00), "
                            "'afternoon' (12:00-17:00), "
                            "'evening' (17:00-19:00). "
                            "Omit to return all available slots."
                        ),
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
                        "description": "The slot ID to book (from get_available_slots)",
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
]


# ──────────────────────────────────────────────────────────────────────
# Tool execution dispatcher
# ──────────────────────────────────────────────────────────────────────


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    db: HospitalDB,
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
    db: HospitalDB,
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
        return await _book_appointment(db, **args)
    elif name == "get_my_bookings":
        return await _get_my_bookings(db, **args)
    elif name == "cancel_booking":
        return await _cancel_booking(db, **args)
    else:
        return {"error": f"Unknown tool: {name}"}


# ──────────────────────────────────────────────────────────────────────
# Tool implementations
# ──────────────────────────────────────────────────────────────────────


async def _identify_patient(
    db: HospitalDB,
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
    db: HospitalDB,
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

    # Format for readability
    doctors = []
    for doc in results:
        doctors.append({
            "doctor_record_id": doc["doctor_record_id"],
            "name": doc["name"],
            "speciality": doc["speciality"],
            "qualification": doc["qualification"],
            "experience": doc["experience"],
            "languages": doc["languages_spoken"],
            "gender": doc["gender"],
        })
    return {"doctors": doctors}


async def _suggest_speciality(
    db: HospitalDB,
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
    facts = await memory.recall(query, user_id="hospital", limit=10)

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
    db: HospitalDB,
    doctor_record_id: int,
    date: str,
    time_of_day: str | None = None,
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

    # Resolve time-of-day bucket to (after_time, before_time)
    after_time: str | None = None
    before_time: str | None = None
    if time_of_day and time_of_day in TIME_BUCKETS:
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
    db: HospitalDB,
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

    return {
        "status": "confirmed",
        "booking": booking,
        "message": "Appointment booked successfully!",
    }


async def _get_my_bookings(db: HospitalDB, patient_phone: str) -> dict:
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
            "speciality": b.get("speciality", ""),
            "date": b.get("appointment_date", ""),
            "time": f"{b.get('slot_start', '')} - {b.get('slot_end', '')}",
        })

    return {"bookings": formatted}


async def _cancel_booking(
    db: HospitalDB,
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
