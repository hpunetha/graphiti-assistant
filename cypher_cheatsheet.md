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

### Advanced Analytics
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
