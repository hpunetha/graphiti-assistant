import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from hospital_api.db import HospitalDB

db: HospitalDB = None  # type: ignore

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    db = HospitalDB(uri, user, password)
    await db.connect()
    yield
    await db.close()

app = FastAPI(title="Hospital Data API", lifespan=lifespan)

class PatientData(BaseModel):
    phone: str
    name: str
    age: int
    gender: str

class BookingRequest(BaseModel):
    slot_id: int
    patient_phone: str

class RescheduleRequest(BaseModel):
    booking_id: int
    patient_phone: str
    new_slot_id: int

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/doctors")
async def get_doctors(speciality: str | None = None, name: str | None = None):
    if name:
        return await db.find_doctor_by_name(name)
    if speciality:
        return await db.find_doctors_by_speciality(speciality)
    
    # Return all specialities if no filter
    specs = await db.get_all_specialities()
    return {"specialities": specs}

@app.get("/doctors/{doctor_id}")
async def get_doctor(doctor_id: int):
    doc = await db.get_doctor_by_id(doctor_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doc

@app.get("/slots")
async def get_slots(
    doctor_record_id: int,
    date: str,
    limit: int = 70,
    after_time: str | None = None,
    before_time: str | None = None
):
    slots = await db.get_available_slots(
        doctor_record_id, date, limit, after_time, before_time
    )
    return slots

@app.get("/slots/next")
async def get_next_available_date(
    doctor_record_id: int,
    from_date: str,
    after_time: str | None = None,
    before_time: str | None = None
):
    date = await db.get_next_available_date(
        doctor_record_id, from_date, after_time, before_time
    )
    return {"next_date": date}

@app.get("/patients/{phone}")
async def get_patient(phone: str):
    patient = await db.get_patient(phone)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient

@app.post("/patients")
async def register_patient(data: PatientData):
    patient = await db.register_patient(data.phone, data.name, data.age, data.gender)
    return patient

@app.get("/bookings")
async def get_bookings(phone: str):
    bookings = await db.get_patient_bookings(phone)
    return bookings

@app.post("/bookings")
async def book_appointment(req: BookingRequest):
    patient = await db.get_patient(req.patient_phone)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    booking = await db.book_slot(req.slot_id, req.patient_phone, patient["name"])
    if not booking:
        raise HTTPException(status_code=409, detail="Slot no longer available")
    return booking

@app.delete("/bookings/{booking_id}")
async def cancel_booking(booking_id: int, patient_phone: str):
    success = await db.cancel_booking(booking_id, patient_phone)
    if not success:
        raise HTTPException(status_code=400, detail="Could not cancel booking")
    return {"status": "cancelled"}

@app.put("/bookings/{booking_id}/reschedule")
async def reschedule_booking(booking_id: int, req: RescheduleRequest):
    if booking_id != req.booking_id:
        raise HTTPException(status_code=400, detail="Booking ID mismatch")
    
    booking = await db.reschedule_booking(req.booking_id, req.patient_phone, req.new_slot_id)
    if not booking:
        raise HTTPException(status_code=400, detail="Could not reschedule")
    return booking
