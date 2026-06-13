# Neo4j Cypher Cheatsheet

You can run these queries by opening the Neo4j Browser at http://localhost:7474 (Username: `neo4j`, Password: `demodemo123`).

## 1. Exploring Graphiti Knowledge Graph
Graphiti stores the unstructured knowledge graph alongside our structured data in Neo4j. It uses specific labels like `Entity`, `Episode`, and connects them.

**View all Graphiti Entities (Extracted concepts, names, etc.)**
```cypher
MATCH (e:Entity) 
RETURN e.name, e.summary, labels(e) LIMIT 20
```

**View all Graphiti Episodes (The raw text passages seeded)**
```cypher
MATCH (ep:Episode) 
RETURN ep.content, ep.source_description LIMIT 20
```

**View connections between Graphiti Entities**
```cypher
MATCH (e1:Entity)-[r]->(e2:Entity) 
WHERE type(r) <> 'HAS_SLOT' AND type(r) <> 'WITH_DOCTOR' AND type(r) <> 'MADE_BOOKING' AND type(r) <> 'BOOKED_IN'
RETURN e1.name, type(r), r.fact, e2.name LIMIT 20
```

---

## 2. Exploring Hospital Structured Data

### Doctors
**Check all doctors for a specific speciality (e.g., Gynecology)**
```cypher
MATCH (d:Doctor)
WHERE toLower(d.speciality) CONTAINS 'gynecology'
RETURN d.name, d.speciality, d.experience, d.location
```

**Count how many doctors we have by speciality**
```cypher
MATCH (d:Doctor)
RETURN d.speciality, count(d) as doctor_count
ORDER BY doctor_count DESC
```

### Slots & Availability
**Check existing available slots for a specific doctor**
```cypher
MATCH (d:Doctor)-[:HAS_SLOT]->(s:Slot)
WHERE d.name CONTAINS 'Arvind' AND s.slot_status = 'AVAILABLE'
RETURN d.name, s.appointment_date, s.slot_start, s.slot_end
ORDER BY s.appointment_date, s.slot_start LIMIT 10
```

**Count available vs booked slots across the hospital**
```cypher
MATCH (s:Slot)
RETURN s.slot_status as status, count(s) as total_slots
```

### Bookings & Patients
**View all patients and their details**
```cypher
MATCH (p:Patient)
RETURN p.name, p.phone, p.age, p.gender
```

**View newly booked slots (Confirmed Bookings)**
```cypher
MATCH (p:Patient)-[:MADE_BOOKING]->(b:Booking)-[:BOOKED_IN]->(s:Slot)
MATCH (b)-[:WITH_DOCTOR]->(d:Doctor)
WHERE b.booking_status = 'CONFIRMED'
RETURN p.name as Patient, b.booking_id as BookingID, d.name as Doctor, s.appointment_date as Date, s.slot_start as Time
ORDER BY b.booked_at DESC
```

**See all cancelled bookings**
```cypher
MATCH (p:Patient)-[:MADE_BOOKING]->(b:Booking)
WHERE b.booking_status = 'CANCELLED'
RETURN p.name, b.booking_id, b.booked_at
```

**Find the busiest doctor (Doctor with the most confirmed bookings)**
```cypher
MATCH (d:Doctor)<-[:WITH_DOCTOR]-(b:Booking {booking_status: 'CONFIRMED'})
RETURN d.name, count(b) as total_bookings
ORDER BY total_bookings DESC LIMIT 5
```

**Delete ALL Data (Warning: Destructive!)**
```cypher
MATCH (n) DETACH DELETE n
```

---

## 3. Verify a Specific Booking

**Look up a booking by Booking ID**
```cypher
MATCH (p:Patient)-[:MADE_BOOKING]->(b:Booking {booking_id: 50022})
MATCH (b)-[:BOOKED_IN]->(s:Slot)
MATCH (b)-[:WITH_DOCTOR]->(d:Doctor)
RETURN p.name AS patient, p.phone AS phone,
       b.booking_id AS booking_id, b.booking_status AS status,
       d.name AS doctor, d.speciality AS speciality,
       s.appointment_date AS date, s.slot_start AS start_time, s.slot_end AS end_time,
       b.booked_at AS booked_at
```
*(Replace `50022` with any Booking ID returned by the assistant.)*

**Check if a specific slot is still AVAILABLE**
```cypher
MATCH (d:Doctor)-[:HAS_SLOT]->(s:Slot {slot_id: 100837})
RETURN d.name, s.slot_status, s.appointment_date, s.slot_start, s.slot_end
```

---

## 4. Time-of-Day Slot Queries

**Morning slots (before 12:00) for a doctor**
```cypher
MATCH (d:Doctor {doctor_record_id: 35})-[:HAS_SLOT]->(s:Slot)
WHERE s.appointment_date = '2026-06-15'
  AND s.slot_status = 'AVAILABLE'
  AND s.slot_start < '12:00'
RETURN s.slot_id, s.slot_start, s.slot_end
ORDER BY s.slot_start
```

**Afternoon slots (12:00–16:59) for a doctor**
```cypher
MATCH (d:Doctor {doctor_record_id: 35})-[:HAS_SLOT]->(s:Slot)
WHERE s.appointment_date = '2026-06-15'
  AND s.slot_status = 'AVAILABLE'
  AND s.slot_start >= '12:00' AND s.slot_start < '17:00'
RETURN s.slot_id, s.slot_start, s.slot_end
ORDER BY s.slot_start
```

**Evening slots (17:00–18:59) for a doctor**
```cypher
MATCH (d:Doctor {doctor_record_id: 35})-[:HAS_SLOT]->(s:Slot)
WHERE s.appointment_date = '2026-06-15'
  AND s.slot_status = 'AVAILABLE'
  AND s.slot_start >= '17:00'
RETURN s.slot_id, s.slot_start, s.slot_end
ORDER BY s.slot_start
```

---

## 5. One-Time: Add Missing Evening Slots (17:00–18:50)

> **Run this ONCE** in Neo4j Browser to add evening slots to the existing database
> without wiping any data. Safe to run — it only creates new Slot nodes for
> time windows that don't already exist for each (doctor, date) pair.

```cypher
// Generate all evening time slots: 17:00 to 18:50 in 10-minute increments
WITH ['17:00','17:10','17:20','17:30','17:40','17:50',
      '18:00','18:10','18:20','18:30','18:40','18:50'] AS eveningTimes

// Get all existing (doctor, date) combinations
MATCH (d:Doctor)-[:HAS_SLOT]->(existing:Slot)
WITH d, existing.appointment_date AS apptDate, eveningTimes
WHERE apptDate IS NOT NULL

// Deduplicate to one row per (doctor, date)
WITH DISTINCT d, apptDate, eveningTimes

// For each (doctor, date), expand across all 12 evening time windows
UNWIND eveningTimes AS slotStart

// Only create if this slot does NOT already exist for this doctor+date+time
WITH d, apptDate, slotStart,
     apptDate + 'T' + slotStart AS uniqueKey
WHERE NOT EXISTS {
    MATCH (d)-[:HAS_SLOT]->(check:Slot)
    WHERE check.appointment_date = apptDate
      AND check.slot_start = slotStart
}

// Compute end time (slotStart + 10 min)
WITH d, apptDate, slotStart,
     CASE slotStart
       WHEN '17:00' THEN '17:10'
       WHEN '17:10' THEN '17:20'
       WHEN '17:20' THEN '17:30'
       WHEN '17:30' THEN '17:40'
       WHEN '17:40' THEN '17:50'
       WHEN '17:50' THEN '18:00'
       WHEN '18:00' THEN '18:10'
       WHEN '18:10' THEN '18:20'
       WHEN '18:20' THEN '18:30'
       WHEN '18:30' THEN '18:40'
       WHEN '18:40' THEN '18:50'
       WHEN '18:50' THEN '19:00'
     END AS slotEnd

// Assign unique slot IDs starting after current max (127456)
WITH d, apptDate, slotStart, slotEnd,
     127457 + (toInteger(d.doctor_record_id) * 10000) +
     (toInteger(replace(replace(apptDate,'-',''), apptDate, 0)) % 10000) AS baseId

// Create the slot node and relationship
WITH d, apptDate, slotStart, slotEnd,
     toInteger(toString(d.doctor_record_id) + toString(toInteger(replace(apptDate,'-',''))) + toString(toInteger(replace(slotStart,':','')))) AS newSlotId

CREATE (s:Slot {
    slot_id: newSlotId,
    appointment_date: apptDate,
    slot_start: slotStart,
    slot_end: slotEnd,
    slot_status: 'AVAILABLE'
})
CREATE (d)-[:HAS_SLOT]->(s)

RETURN count(s) AS evening_slots_added
```

> **Expected output:** `evening_slots_added: 6864` (11 doctors × 52 dates × 12 slots).

**Verify evening slots were added correctly:**
```cypher
MATCH (d:Doctor)-[:HAS_SLOT]->(s:Slot)
WHERE s.slot_start >= '17:00'
RETURN d.name, count(s) AS evening_slots
ORDER BY d.name
```

