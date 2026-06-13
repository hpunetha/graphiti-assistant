
from pathlib import Path
import pandas as pd
from datetime import datetime, timedelta
import random

DOCTOR_FILE = Path(__file__).parent.parent / "data" / "doctor_directory.csv"

MONTHS_TO_GENERATE = 2
SLOT_DURATION_MINUTES = 10

doctor_df = pd.read_csv(DOCTOR_FILE)

# Use actual doctor_record_id from the CSV, don't overwrite with sequential IDs
doctor_id_col = "doctor_record_id"

# Patient pool with demographic data for pre-seeding
PATIENT_POOL = [
    {"name": "Rahul Sharma", "gender": "Male", "age_range": (25, 40)},
    {"name": "Priya Verma", "gender": "Female", "age_range": (22, 38)},
    {"name": "Amit Singh", "gender": "Male", "age_range": (30, 55)},
    {"name": "Sneha Gupta", "gender": "Female", "age_range": (20, 35)},
    {"name": "Anita Desai", "gender": "Female", "age_range": (28, 45)},
    {"name": "Vikram Patel", "gender": "Male", "age_range": (35, 60)},
    {"name": "Meera Joshi", "gender": "Female", "age_range": (5, 12)},
    {"name": "Arjun Reddy", "gender": "Male", "age_range": (2, 8)},
]

slot_rows = []
slot_id = 100000

start_date = datetime.today().date()
end_date = start_date + timedelta(days=MONTHS_TO_GENERATE * 30)

for current_date in pd.date_range(start_date, end_date):

    if current_date.weekday() == 6:
        continue

    for _, doctor in doctor_df.iterrows():

        doctor_id = doctor[doctor_id_col]

        current_slot = datetime.combine(
            current_date.date(),
            datetime.min.time()
        ).replace(hour=9, minute=0)

        end_slot = datetime.combine(
            current_date.date(),
            datetime.min.time()
        ).replace(hour=19, minute=0)  # Extended to 19:00 to include evening slots (17:00-18:50)

        while current_slot < end_slot:

            next_slot = current_slot + timedelta(
                minutes=SLOT_DURATION_MINUTES
            )

            slot_id += 1

            slot_rows.append({
                "slot_id": slot_id,
                "doctor_record_id": doctor_id,
                "appointment_date": current_date.strftime("%Y-%m-%d"),
                "slot_start": current_slot.strftime("%H:%M"),
                "slot_end": next_slot.strftime("%H:%M"),
                "slot_status": random.choices(
                    ["AVAILABLE", "BOOKED", "NOT_AVAILABLE"],
                    weights=[90, 8, 2],
                    k=1
                )[0]
            })

            current_slot = next_slot

slot_df = pd.DataFrame(slot_rows)

booked_slots = slot_df[
    slot_df["slot_status"] == "BOOKED"
]

appointment_rows = []
booking_id = 50000

for doctor_id in doctor_df[doctor_id_col].unique():

    doctor_slots = booked_slots[
        booked_slots["doctor_record_id"] == doctor_id
    ]

    if len(doctor_slots) == 0:
        continue

    sampled = doctor_slots.sample(
        n=min(random.randint(1, 3), len(doctor_slots))
    )

    for _, row in sampled.iterrows():

        booking_id += 1

        patient = random.choice(PATIENT_POOL)
        patient_age = random.randint(*patient["age_range"])

        appointment_rows.append({
            "booking_id": booking_id,
            "slot_id": row["slot_id"],
            "doctor_record_id": row["doctor_record_id"],
            "patient_name": patient["name"],
            "patient_phone": random.randint(
                7000000000,
                9999999999
            ),
            "patient_age": patient_age,
            "patient_gender": patient["gender"],
            "booking_status": "CONFIRMED",
            "booked_at": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        })

appointment_df = pd.DataFrame(appointment_rows)

slot_df.to_csv(
    Path(__file__).parent.parent / "data" / "doctor_slot_availability.csv",
    index=False
)

appointment_df.to_csv(
    Path(__file__).parent.parent / "data" / "appointment_booking.csv",
    index=False
)

print(f"Generated {len(slot_df)} slots across {len(doctor_df)} doctors")
print(f"Generated {len(appointment_df)} appointment bookings")
print(f"Slot statuses: {slot_df['slot_status'].value_counts().to_dict()}")
print("Files generated successfully")
