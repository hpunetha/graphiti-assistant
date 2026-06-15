"""
db.py — Async Neo4j access layer for structured hospital data.

Handles all Cypher queries for doctors, slots, bookings, and patients.
This is separate from Graphiti because slot/booking operations need atomic
transactions, not LLM-based knowledge extraction.

Usage:
    db = HospitalDB(uri, user, password)
    await db.connect()
    slots = await db.get_available_slots(doctor_id=1, date="2026-06-15")
    await db.close()
"""

from __future__ import annotations

from neo4j import AsyncGraphDatabase


class HospitalDB:
    """Async Neo4j wrapper for hospital booking operations."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def connect(self) -> None:
        """Verify the connection is alive."""
        await self._driver.verify_connectivity()

    async def close(self) -> None:
        await self._driver.close()

    # ------------------------------------------------------------------
    # Doctor queries
    # ------------------------------------------------------------------

    async def find_doctors_by_speciality(self, speciality: str) -> list[dict]:
        """Find active doctors by speciality (case-insensitive partial match)."""
        query = """
        MATCH (d:Doctor)
        WHERE toLower(d.speciality) CONTAINS toLower($speciality)
          AND d.is_active = true
        RETURN d {.*} AS doctor
        ORDER BY d.name
        """
        async with self._driver.session() as session:
            result = await session.run(query, speciality=speciality)
            records = await result.data()
            return [r["doctor"] for r in records]

    async def find_doctor_by_name(self, name: str) -> list[dict]:
        """Find doctors by name (case-insensitive partial match)."""
        query = """
        MATCH (d:Doctor)
        WHERE toLower(d.name) CONTAINS toLower($name)
          AND d.is_active = true
        RETURN d {.*} AS doctor
        """
        async with self._driver.session() as session:
            result = await session.run(query, name=name)
            records = await result.data()
            return [r["doctor"] for r in records]

    async def get_doctor_by_id(self, doctor_record_id: int) -> dict | None:
        """Get a single doctor by ID."""
        query = """
        MATCH (d:Doctor {doctor_record_id: $doctor_record_id})
        RETURN d {.*} AS doctor
        """
        async with self._driver.session() as session:
            result = await session.run(query, doctor_record_id=doctor_record_id)
            record = await result.single()
            return record["doctor"] if record else None

    async def get_all_specialities(self) -> list[str]:
        """Get distinct specialities of active doctors."""
        query = """
        MATCH (d:Doctor)
        WHERE d.is_active = true
        RETURN DISTINCT d.speciality AS speciality
        ORDER BY speciality
        """
        async with self._driver.session() as session:
            result = await session.run(query)
            records = await result.data()
            return [r["speciality"] for r in records]

    # ------------------------------------------------------------------
    # Slot queries
    # ------------------------------------------------------------------

    async def get_available_slots(
        self,
        doctor_record_id: int,
        date: str,
        limit: int = 70,
        after_time: str | None = None,
        before_time: str | None = None,
    ) -> list[dict]:
        """Get available slots for a doctor on a specific date.

        Args:
            doctor_record_id: The doctor's ID.
            date: Date string in YYYY-MM-DD format.
            limit: Max number of slots to return (default 70 covers full 10-hr day).
            after_time: HH:MM string — only return slots starting at or after this time.
            before_time: HH:MM string — only return slots starting before this time.
        """
        query = """
        MATCH (d:Doctor {doctor_record_id: $doctor_record_id})-[:HAS_SLOT]->(s:Slot)
        WHERE s.appointment_date = $date
          AND s.slot_status = 'AVAILABLE'
          AND ($after_time IS NULL OR s.slot_start >= $after_time)
          AND ($before_time IS NULL OR s.slot_start < $before_time)
        RETURN s {.*, doctor_name: d.name, speciality: d.speciality} AS slot
        ORDER BY s.slot_start
        LIMIT $limit
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                doctor_record_id=doctor_record_id,
                date=date,
                limit=limit,
                after_time=after_time,
                before_time=before_time,
            )
            records = await result.data()
            return [r["slot"] for r in records]

    async def get_next_available_date(
        self,
        doctor_record_id: int,
        from_date: str,
        after_time: str | None = None,
        before_time: str | None = None,
    ) -> str | None:
        """Find the nearest future date that has at least one AVAILABLE slot.

        Args:
            doctor_record_id: The doctor's ID.
            from_date: Date string in YYYY-MM-DD format (start searching from here, inclusive).
            after_time: Optional HH:MM start time filter.
            before_time: Optional HH:MM end time filter.
        """
        query = """
        MATCH (d:Doctor {doctor_record_id: $doctor_record_id})-[:HAS_SLOT]->(s:Slot)
        WHERE s.appointment_date >= $from_date
          AND s.slot_status = 'AVAILABLE'
          AND ($after_time IS NULL OR s.slot_start >= $after_time)
          AND ($before_time IS NULL OR s.slot_start < $before_time)
        RETURN s.appointment_date AS next_date
        ORDER BY s.appointment_date
        LIMIT 1
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                doctor_record_id=doctor_record_id,
                from_date=from_date,
                after_time=after_time,
                before_time=before_time,
            )
            record = await result.single()
            return record["next_date"] if record else None

    # ------------------------------------------------------------------
    # Booking operations (atomic)
    # ------------------------------------------------------------------

    async def _do_book_slot(
        self, tx, slot_id: int, patient_phone: str, member_name: str | None = None
    ) -> dict | None:
        """Inner transaction function for atomic slot booking.

        Both steps run in the same write transaction — get the max booking_id
        first, then check slot status and create the booking atomically.
        Returns the booking dict, or None if the slot is no longer AVAILABLE.
        """
        # Step 1: get max booking_id inside this transaction
        max_result = await tx.run(
            "MATCH (b:Booking) RETURN coalesce(max(b.booking_id), 50000) AS max_id"
        )
        max_record = await max_result.single()
        new_id = (max_record["max_id"] if max_record else 50000) + 1

        # Step 2: check-and-book atomically (WHERE guards against race condition).
        # patient_name is stored on the Booking node so it reflects who the
        # appointment is actually for (account holder or a family member).
        book_result = await tx.run(
            """
            MATCH (s:Slot {slot_id: $slot_id})
            WHERE s.slot_status = 'AVAILABLE'
            MATCH (d:Doctor)-[:HAS_SLOT]->(s)
            MATCH (p:Patient {phone: $patient_phone})
            SET s.slot_status = 'BOOKED'
            CREATE (b:Booking {
                booking_id: $new_id,
                booking_status: 'CONFIRMED',
                booked_at: datetime(),
                patient_name: coalesce($member_name, p.name)
            })
            CREATE (p)-[:MADE_BOOKING]->(b)
            CREATE (b)-[:BOOKED_IN]->(s)
            CREATE (b)-[:WITH_DOCTOR]->(d)
            RETURN b {
                .*,
                slot_id: s.slot_id,
                appointment_date: s.appointment_date,
                slot_start: s.slot_start,
                slot_end: s.slot_end,
                doctor_name: d.name,
                speciality: d.speciality,
                patient_phone: p.phone
            } AS booking
            """,
            slot_id=slot_id,
            patient_phone=patient_phone,
            new_id=new_id,
            member_name=member_name,
        )
        record = await book_result.single()
        return record["booking"] if record else None

    async def book_slot(
        self,
        slot_id: int,
        patient_phone: str,
        member_name: str | None = None,
    ) -> dict | None:
        """Atomically book a slot. Returns booking details or None if the slot
        is no longer available (race condition handled).

        Both steps (get max ID + check-and-create booking) run inside a single
        write transaction — if the slot was changed to BOOKED/NOT_AVAILABLE by
        another process in between, the WHERE clause fails and we return None.
        """
        async with self._driver.session() as session:
            return await session.execute_write(
                self._do_book_slot, slot_id, patient_phone, member_name
            )

    async def cancel_booking(
        self, booking_id: int, patient_phone: str
    ) -> bool:
        """Cancel a booking and free the slot back to AVAILABLE."""
        query = """
        MATCH (p:Patient {phone: $patient_phone})-[:MADE_BOOKING]->(b:Booking {booking_id: $booking_id})
        MATCH (b)-[:BOOKED_IN]->(s:Slot)
        WHERE b.booking_status = 'CONFIRMED'
        SET b.booking_status = 'CANCELLED',
            s.slot_status = 'AVAILABLE'
        RETURN b.booking_id AS cancelled_id
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                booking_id=booking_id,
                patient_phone=patient_phone,
            )
            record = await result.single()
            return record is not None

    async def _do_reschedule_booking(self, tx, booking_id: int, patient_phone: str, new_slot_id: int) -> dict | None:
        """Inner transaction function for atomic slot reschedule."""
        max_result = await tx.run(
            "MATCH (b:Booking) RETURN coalesce(max(b.booking_id), 50000) AS max_id"
        )
        max_record = await max_result.single()
        new_id = (max_record["max_id"] if max_record else 50000) + 1

        query = """
        MATCH (p:Patient {phone: $patient_phone})-[:MADE_BOOKING]->(old_b:Booking {booking_id: $booking_id})
        MATCH (old_b)-[old_rel:BOOKED_IN]->(old_s:Slot)
        MATCH (old_b)-[:WITH_DOCTOR]->(d:Doctor)
        MATCH (new_s:Slot {slot_id: $new_slot_id})
        WHERE old_b.booking_status = 'CONFIRMED'
          AND new_s.slot_status = 'AVAILABLE'

        // Cancel old
        SET old_b.booking_status = 'CANCELLED',
            old_s.slot_status = 'AVAILABLE'

        // Book new — carry patient_name forward so family member info is preserved
        SET new_s.slot_status = 'BOOKED'
        CREATE (new_b:Booking {
            booking_id: $new_id,
            booking_status: 'CONFIRMED',
            booked_at: datetime(),
            patient_name: coalesce(old_b.patient_name, p.name)
        })
        CREATE (p)-[:MADE_BOOKING]->(new_b)
        CREATE (new_b)-[:BOOKED_IN]->(new_s)
        CREATE (new_b)-[:WITH_DOCTOR]->(d)

        RETURN new_b {
            .*,
            slot_id: new_s.slot_id,
            appointment_date: new_s.appointment_date,
            slot_start: new_s.slot_start,
            slot_end: new_s.slot_end,
            doctor_name: d.name,
            speciality: d.speciality,
            patient_phone: p.phone
        } AS booking
        """
        result = await tx.run(
            query,
            booking_id=booking_id,
            patient_phone=patient_phone,
            new_slot_id=new_slot_id,
            new_id=new_id,
        )
        record = await result.single()
        return record["booking"] if record else None

    async def reschedule_booking(self, booking_id: int, patient_phone: str, new_slot_id: int) -> dict | None:
        """Atomically reschedule a booking. 
        Cancels the old one and books the new one in a single transaction.
        Returns the new booking details, or None if it failed (e.g., new slot taken).
        """
        async with self._driver.session() as session:
            return await session.execute_write(
                self._do_reschedule_booking, booking_id, patient_phone, new_slot_id
            )

    async def get_patient_bookings(self, phone: str) -> list[dict]:
        """Get all bookings for a patient."""
        query = """
        MATCH (p:Patient {phone: $phone})-[:MADE_BOOKING]->(b:Booking)
        MATCH (b)-[:BOOKED_IN]->(s:Slot)
        MATCH (b)-[:WITH_DOCTOR]->(d:Doctor)
        RETURN b {
            .*,
            patient_name: coalesce(b.patient_name, p.name),
            slot_id: s.slot_id,
            appointment_date: s.appointment_date,
            slot_start: s.slot_start,
            slot_end: s.slot_end,
            doctor_name: d.name,
            doctor_record_id: d.doctor_record_id,
            speciality: d.speciality
        } AS booking
        ORDER BY s.appointment_date, s.slot_start
        """
        async with self._driver.session() as session:
            result = await session.run(query, phone=phone)
            records = await result.data()
            return [r["booking"] for r in records]

    # ------------------------------------------------------------------
    # Patient operations
    # ------------------------------------------------------------------

    async def get_patient(self, phone: str) -> dict | None:
        """Look up a patient by phone number."""
        query = """
        MATCH (p:Patient {phone: $phone})
        RETURN p {.*} AS patient
        """
        async with self._driver.session() as session:
            result = await session.run(query, phone=phone)
            record = await result.single()
            return record["patient"] if record else None

    async def register_patient(
        self, phone: str, name: str, age: int, gender: str
    ) -> dict:
        """Register a new patient. Returns the created patient node."""
        query = """
        MERGE (p:Patient {phone: $phone})
        ON CREATE SET p.name = $name, p.age = $age, p.gender = $gender,
                      p.registered_at = datetime()
        RETURN p {.*} AS patient
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                phone=phone,
                name=name,
                age=age,
                gender=gender,
            )
            record = await result.single()
            return record["patient"]

    async def get_family_members(self, phone: str) -> list[dict]:
        """Return all family members registered under this phone number."""
        query = """
        MATCH (p:Patient {phone: $phone})-[:HAS_MEMBER]->(fm:FamilyMember)
        RETURN fm {.*} AS member
        ORDER BY fm.name
        """
        async with self._driver.session() as session:
            result = await session.run(query, phone=phone)
            records = await result.data()
            return [r["member"] for r in records]

    async def register_family_member(
        self, phone: str, name: str, age: int, gender: str, relationship: str
    ) -> dict:
        """Add a family member under an existing patient account.

        Uses MERGE on (phone, name) so re-registering the same person just
        updates their details without creating a duplicate node.
        """
        query = """
        MATCH (p:Patient {phone: $phone})
        MERGE (p)-[:HAS_MEMBER]->(fm:FamilyMember {phone: $phone, name: $name})
        ON CREATE SET fm.age = $age, fm.gender = $gender,
                      fm.relationship = $relationship,
                      fm.registered_at = datetime()
        ON MATCH SET  fm.age = $age, fm.gender = $gender,
                      fm.relationship = $relationship
        RETURN fm {.*} AS member
        """
        async with self._driver.session() as session:
            result = await session.run(
                query,
                phone=phone,
                name=name,
                age=age,
                gender=gender,
                relationship=relationship,
            )
            record = await result.single()
            return record["member"]

    # ------------------------------------------------------------------
    # Admin / modifier operations
    # ------------------------------------------------------------------

    async def update_slot_status(
        self, slot_id: int, new_status: str
    ) -> dict | None:
        """Update a slot's status. Used by the slot_modifier script."""
        query = """
        MATCH (d:Doctor)-[:HAS_SLOT]->(s:Slot {slot_id: $slot_id})
        SET s.slot_status = $new_status
        RETURN s {.*, doctor_name: d.name} AS slot
        """
        async with self._driver.session() as session:
            result = await session.run(
                query, slot_id=slot_id, new_status=new_status
            )
            record = await result.single()
            return record["slot"] if record else None

    async def get_random_slots_by_status(
        self, status: str, count: int = 3
    ) -> list[dict]:
        """Get random slots with a given status. Used by slot_modifier."""
        query = """
        MATCH (d:Doctor)-[:HAS_SLOT]->(s:Slot)
        WHERE s.slot_status = $status
        WITH s, d, rand() AS r
        ORDER BY r
        LIMIT $count
        RETURN s {.*, doctor_name: d.name} AS slot
        """
        async with self._driver.session() as session:
            result = await session.run(query, status=status, count=count)
            records = await result.data()
            return [r["slot"] for r in records]

    async def clear_all_data(self) -> None:
        """Delete all hospital nodes and relationships. Preserves Graphiti data."""
        async with self._driver.session() as session:
            await session.run("MATCH (n) WHERE labels(n) IN [['Doctor'], ['Slot'], ['Booking'], ['Patient']] DETACH DELETE n")
