# MedBook API — Usage Guide & Mock Conversation

## Prerequisites
Start the API locally (Neo4j must be running):
```bash
uvicorn app.api:app --reload
```
Or with Docker (both Neo4j + API):
```bash
docker compose --profile api up -d
```

---

## 1. Health Check
```bash
curl http://localhost:8000/health
```
**Response:**
```json
{"status": "ok", "neo4j": "connected"}
```

---

## 2. Swagger UI
Open in browser: http://localhost:8000/docs

---

## 3. POST /chat — Stateless REST (Full Mock Conversation)

Each request is independent. To maintain context, pass the `messages` array
returned from the previous response back into the next request.

### Turn 1 — First message (fresh session, no messages)
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_message": "I have a bad headache and want to see a doctor"}'
```
**Response:**
```json
{
  "reply": "I'd be happy to help you book an appointment! Could you please share your phone number so I can look up your profile?",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "I have a bad headache and want to see a doctor"},
    {"role": "assistant", "content": "I'd be happy to help..."}
  ]
}
```

### Turn 2 — Provide phone number (pass `messages` from Turn 1)
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "8952039590",
    "messages": [/* paste messages array from Turn 1 response */]
  }'
```
**Response:** Agent calls `identify_patient`, finds no record, asks for name.

### Turn 3 — Provide name
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_message": "Harsh",
    "messages": [/* paste messages from Turn 2 */]
  }'
```

### Turn 4 — Age
```bash
  "user_message": "33"
```

### Turn 5 — Gender (registration completes + doctor suggestion)
```bash
  "user_message": "male"
```
**At this point** the agent registers the patient, suggests a General Physician,
and asks for a preferred date.

### Turn 6 — Request a date and time
```bash
  "user_message": "June 16, morning"
```
**Response:** Agent calls `get_available_slots`, returns list of morning slots.

### Turn 7 — Book a slot
```bash
  "user_message": "book the 09:10 slot"
```
**Response:**
```json
{
  "reply": "✅ Confirmed! Your appointment is booked:\n• Doctor: Dr. Amit Sharma\n• Date: 2026-06-16\n• Time: 09:10 - 09:20\n• Booking ID: 50023",
  "messages": [...]
}
```

---

## 4. WebSocket /ws/chat — Stateful (Recommended for Chat UIs)

### Connect with wscat (install: `npm install -g wscat`)
```bash
wscat -c ws://localhost:8000/ws/chat
```

### On connect, server sends:
```json
{
  "type": "connected",
  "session_id": "a1b2c3d4-...",
  "message": "Connected to MedBook API."
}
```

### Full mock conversation (just type messages, server replies):
```
< {"type":"connected","session_id":"a1b2c3...","message":"Connected to MedBook API."}

> I have a bad headache
< {"type":"reply","message":"I'd be happy to help! Could you please share your phone number?"}

> 8952039590
< {"type":"reply","message":"I don't see a profile for this number. What is your full name?"}

> Harsh
< {"type":"reply","message":"Thanks Harsh! What is your age?"}

> 33
< {"type":"reply","message":"And your gender?"}

> male
< {"type":"reply","message":"You're registered! Based on your headache, I recommend a General Physician. We have Dr. Amit Sharma (22+ years). What date works for you?"}

> June 16, morning
< {"type":"reply","message":"Here are morning slots for Dr. Amit Sharma on June 16:\n1. 09:00–09:10 (slot_id: 100045)\n2. 09:10–09:20 (slot_id: 100046)\n..."}

> book slot 100046
< {"type":"reply","message":"✅ Confirmed! Booking #50023\n• Dr. Amit Sharma\n• June 16, 09:10–09:20"}

> reset
< {"type":"system","message":"--- Conversation reset. Start fresh! ---"}
```

### WebSocket with Python
```python
import asyncio
import websockets
import json

async def chat():
    uri = "ws://localhost:8000/ws/chat"
    async with websockets.connect(uri) as ws:
        # Get session_id on connect
        greeting = json.loads(await ws.recv())
        print("Session:", greeting["session_id"])

        # Send messages
        messages = [
            "I have a headache",
            "8952039590",
            "Harsh",
            "33",
            "male",
            "June 16, morning",
            "book the first slot",
        ]
        for msg in messages:
            await ws.send(msg)
            reply = json.loads(await ws.recv())
            print(f"bot> {reply['message']}\n")

asyncio.run(chat())
```

---

## 5. Reschedule Booking (via WebSocket)
Once you have a `booking_id` from a previous booking:
```
> I want to reschedule booking 50023 to June 17 morning
```
The agent will call `get_available_slots` for June 17, present options, then
call `reschedule_booking` once you pick a new slot. The old booking is atomically
cancelled and the new one is confirmed — if the new slot is taken, your original
booking stays intact.

## 6. Get Next Available Date
If a requested date has no slots, the agent will automatically call
`get_next_available_date` and tell you the nearest date with open slots.
```
> Book June 14 evening with Dr. Amit Sharma
bot> There are no evening slots on June 14. The next available date for evenings is June 16. Would you like to book on June 16?
```

---

## Summary of Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/health` | Liveness probe |
| `POST` | `/chat` | Stateless REST — pass `messages` for context |
| `WebSocket` | `/ws/chat` | Stateful — server holds context per connection |
| `GET` | `/docs` | Auto Swagger UI (HTTP endpoints only) |
| `GET` | `/ws-test` | Browser-based WebSocket chat tester UI |

---

## 7. WebSocket Test UI (`/ws-test`)

Since Swagger UI does not support WebSocket endpoints, a built-in browser chat UI is available at:

```
http://localhost:8000/ws-test
```

This page lets you interact with the `/ws/chat` endpoint directly from the browser without any external tools.

---

## 8. Family Member Booking (via WebSocket)

When a phone account has multiple people (e.g. Ramesh books for himself and his son Neeraj), the agent asks who the appointment is for. If the person isn't registered yet, it adds them first.

```
> 9876500001
< "I found the profile for Neeraj (age 12). Registered family members: Ramesh (father).
   Who is this appointment for — Neeraj or Ramesh?"

> Ramesh
< "Got it, booking for Ramesh. What are your symptoms or which doctor would you like?"

> Cardiology
< [shows cardiologists, proceeds to slot selection]

> book the 10:00 slot
< "✅ Confirmed! Booking #50031
   • Patient: Ramesh
   • Doctor: Dr. Priya Nair (Cardiology)
   • Date: 2026-06-17 · 10:00–10:10"
```

To add a new family member mid-conversation:
```
> I want to book for my wife Priya
< "I'll add Priya to your account. How old is she?"
> 38
< "And her gender?"
> Female
< [registers Priya as a family member, then proceeds with booking with for_member="Priya"]
```

---

## 9. Hospital API (Data endpoints)
The raw hospital operations run on port `8001` (if using Docker Compose).
These endpoints manage the structured Neo4j database natively:

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `http://localhost:8001/doctors` | List specialities or search doctors (`?speciality=` or `?name=`) |
| `GET` | `http://localhost:8001/slots` | Get available slots for a doctor |
| `POST` | `http://localhost:8001/bookings` | Book a slot (`slot_id`, `patient_phone`, optional `member_name`) |
| `POST` | `http://localhost:8001/patients` | Register a new patient |
| `GET` | `http://localhost:8001/patients/{phone}/members` | List family members for a phone account |
| `POST` | `http://localhost:8001/patients/{phone}/members` | Add a family member (`name`, `age`, `gender`, `relationship`) |
