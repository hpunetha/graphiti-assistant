
from pathlib import Path
import pandas as pd
from datetime import datetime, timedelta
import random

DOCTOR_FILE = Path(__file__).parent.parent / "data" / "doctor_directory.csv"

MONTHS_TO_GENERATE = 2
SLOT_DURATION_MINUTES = 10

doctor_df = pd.read_csv(DOCTOR_FILE)

doctor_id_col = "doctor_record_id"

doctor_df[doctor_id_col] = range(1, len(doctor_df) + 1)

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
        ).replace(hour=17, minute=0)

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
                    ["AVAILABLE", "BOOKED"],
                    weights=[92, 8],
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
        n=min(random.randint(1,2), len(doctor_slots))
    )

    for _, row in sampled.iterrows():

        booking_id += 1

        appointment_rows.append({
            "booking_id": booking_id,
            "slot_id": row["slot_id"],
            "doctor_record_id": row["doctor_record_id"],
            "patient_name": random.choice([
                "Rahul Sharma",
                "Priya Verma",
                "Amit Singh",
                "Sneha Gupta"
            ]),
            "patient_phone": random.randint(
                7000000000,
                9999999999
            ),
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

print("Files generated successfully")
