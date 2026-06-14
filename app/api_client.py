import httpx

class HospitalApiClient:
    """Async HTTP client for hospital booking operations via the Hospital API."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0)

    async def connect(self) -> None:
        """Verify the API is reachable."""
        response = await self.client.get("/health")
        response.raise_for_status()

    async def close(self) -> None:
        await self.client.aclose()

    async def find_doctors_by_speciality(self, speciality: str) -> list[dict]:
        response = await self.client.get("/doctors", params={"speciality": speciality})
        response.raise_for_status()
        return response.json()

    async def find_doctor_by_name(self, name: str) -> list[dict]:
        response = await self.client.get("/doctors", params={"name": name})
        response.raise_for_status()
        return response.json()

    async def get_doctor_by_id(self, doctor_record_id: int) -> dict | None:
        response = await self.client.get(f"/doctors/{doctor_record_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def get_all_specialities(self) -> list[str]:
        response = await self.client.get("/doctors")
        response.raise_for_status()
        return response.json().get("specialities", [])

    async def get_available_slots(
        self,
        doctor_record_id: int,
        date: str,
        limit: int = 70,
        after_time: str | None = None,
        before_time: str | None = None,
    ) -> list[dict]:
        params = {"doctor_record_id": doctor_record_id, "date": date, "limit": limit}
        if after_time: params["after_time"] = after_time
        if before_time: params["before_time"] = before_time
        response = await self.client.get("/slots", params=params)
        response.raise_for_status()
        return response.json()

    async def get_next_available_date(
        self,
        doctor_record_id: int,
        from_date: str,
        after_time: str | None = None,
        before_time: str | None = None,
    ) -> str | None:
        params = {"doctor_record_id": doctor_record_id, "from_date": from_date}
        if after_time: params["after_time"] = after_time
        if before_time: params["before_time"] = before_time
        response = await self.client.get("/slots/next", params=params)
        response.raise_for_status()
        return response.json().get("next_date")

    async def get_patient(self, phone: str) -> dict | None:
        response = await self.client.get(f"/patients/{phone}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def register_patient(
        self, phone: str, name: str, age: int, gender: str
    ) -> dict:
        data = {"phone": phone, "name": name, "age": age, "gender": gender}
        response = await self.client.post("/patients", json=data)
        response.raise_for_status()
        return response.json()

    async def book_slot(
        self,
        slot_id: int,
        patient_phone: str,
        patient_name: str,
    ) -> dict | None:
        data = {"slot_id": slot_id, "patient_phone": patient_phone}
        response = await self.client.post("/bookings", json=data)
        if response.status_code == 409: # Slot unavailable
            return None
        response.raise_for_status()
        return response.json()

    async def get_patient_bookings(self, phone: str) -> list[dict]:
        response = await self.client.get("/bookings", params={"phone": phone})
        response.raise_for_status()
        return response.json()

    async def cancel_booking(self, booking_id: int, patient_phone: str) -> bool:
        params = {"patient_phone": patient_phone}
        response = await self.client.delete(f"/bookings/{booking_id}", params=params)
        if response.status_code == 400:
            return False
        response.raise_for_status()
        return True

    async def reschedule_booking(self, booking_id: int, patient_phone: str, new_slot_id: int) -> dict | None:
        data = {"booking_id": booking_id, "patient_phone": patient_phone, "new_slot_id": new_slot_id}
        response = await self.client.put(f"/bookings/{booking_id}/reschedule", json=data)
        if response.status_code == 400:
            return None
        response.raise_for_status()
        return response.json()
