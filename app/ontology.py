"""
ontology.py — Domain entity and edge types for MedBook's Graphiti knowledge graph.

Passing these typed schemas to add_episode() tells Graphiti's extraction LLM
exactly which node labels and relationship names to create, replacing the default
generic "Entity / relates_to" extraction with precise medical-domain types.

## Why the KG prefix?

Both the hospital-api and Graphiti write to the same Neo4j database. The
hospital-api already owns (:Doctor), (:Patient) etc. for transactional data.
Without a prefix, Graphiti's EntityNode+typed-label nodes would carry the same
labels, making direct Cypher queries like MATCH (n:Doctor) return a confusing mix
of both sets. Prefixing Graphiti's entity types with "KG" keeps the two layers
visually and structurally distinct:

  (:Doctor)             — hospital-api transactional node (doctor_record_id, name, …)
  (:Entity:KGDoctor)    — Graphiti knowledge-graph node   (uuid, group_id, name_embedding, …)

Graphiti's own search APIs already scope by uuid/group_id/name_embedding, so there
is no functional clash even without the prefix — but the prefix makes Neo4j Browser
inspection and direct Cypher work unambiguous.

Two namespace scopes:
  * PATIENT_*   — used when ingesting patient conversations (group_id = phone)
  * HOSPITAL_*  — used when seeding medical knowledge (group_id = "hospital")

All fields must have defaults so Graphiti can populate only what it extracts.
Field names must not overlap with core EntityNode fields:
  (uuid, name, group_id, labels, created_at, name_embedding, summary, attributes)
"""

from __future__ import annotations

from pydantic import BaseModel


# ── Entity types ─────────────────────────────────────────────────────────────

class KGPatient(BaseModel):
    phone: str = ""
    age: int | None = None
    gender: str = ""


class KGDoctor(BaseModel):
    doctor_record_id: int | None = None
    speciality: str = ""
    gender: str = ""


class KGSymptom(BaseModel):
    severity: str = ""   # mild | moderate | severe
    duration: str = ""   # acute | chronic | ongoing


class KGAllergy(BaseModel):
    allergen: str = ""
    reaction: str = ""


class KGPreference(BaseModel):
    preference_type: str = ""  # time_of_day | location | doctor_gender
    value: str = ""


class KGFamilyMember(BaseModel):
    relation: str = ""   # son | daughter | parent | spouse | sibling
    age: int | None = None


class KGAppointment(BaseModel):
    booking_id: str = ""
    date: str = ""
    time: str = ""
    status: str = ""     # confirmed | cancelled


# ── Edge types ────────────────────────────────────────────────────────────────
# Edge types become Neo4j relationship types; the hospital-api uses different
# relationship names (HAS_SLOT, BOOKED_IN, MADE_BOOKING, WITH_DOCTOR), so no
# prefix is needed here.

class HAS_SYMPTOM(BaseModel):
    onset: str = ""
    duration: str = ""


class ALLERGIC_TO(BaseModel):
    severity: str = ""
    reaction: str = ""


class PREFERS(BaseModel):
    context: str = ""    # free text: "prefers evening appointments"


class BOOKED_WITH(BaseModel):
    booking_id: str = ""
    date: str = ""
    time: str = ""


class GUARDIAN_OF(BaseModel):
    relation: str = ""   # parent | guardian


class TREATS(BaseModel):
    speciality: str = ""


# ── Patient namespace — used for conversation ingestion ───────────────────────

PATIENT_ENTITY_TYPES: dict[str, type[BaseModel]] = {
    "KGPatient":      KGPatient,
    "KGSymptom":      KGSymptom,
    "KGAllergy":      KGAllergy,
    "KGPreference":   KGPreference,
    "KGFamilyMember": KGFamilyMember,
    "KGAppointment":  KGAppointment,
}

PATIENT_EDGE_TYPES: dict[str, type[BaseModel]] = {
    "HAS_SYMPTOM":  HAS_SYMPTOM,
    "ALLERGIC_TO":  ALLERGIC_TO,
    "PREFERS":      PREFERS,
    "BOOKED_WITH":  BOOKED_WITH,
    "GUARDIAN_OF":  GUARDIAN_OF,
}

PATIENT_EDGE_TYPE_MAP: dict[tuple[str, str], list[str]] = {
    ("KGPatient",      "KGSymptom"):      ["HAS_SYMPTOM"],
    ("KGFamilyMember", "KGSymptom"):      ["HAS_SYMPTOM"],
    ("KGPatient",      "KGAllergy"):      ["ALLERGIC_TO"],
    ("KGFamilyMember", "KGAllergy"):      ["ALLERGIC_TO"],
    ("KGPatient",      "KGPreference"):   ["PREFERS"],
    ("KGPatient",      "KGAppointment"):  ["BOOKED_WITH"],
    ("KGPatient",      "KGFamilyMember"): ["GUARDIAN_OF"],
}

# ── Hospital namespace — used when seeding medical knowledge ──────────────────

HOSPITAL_ENTITY_TYPES: dict[str, type[BaseModel]] = {
    "KGDoctor":  KGDoctor,
    "KGSymptom": KGSymptom,
}

HOSPITAL_EDGE_TYPES: dict[str, type[BaseModel]] = {
    "TREATS": TREATS,
}

HOSPITAL_EDGE_TYPE_MAP: dict[tuple[str, str], list[str]] = {
    ("KGDoctor", "KGSymptom"): ["TREATS"],
}

# ── Extraction instructions — shared across both namespaces ───────────────────

EXTRACTION_INSTRUCTIONS = """\
Extract entities and relationships with medical precision:

Entities:
- KGPatient: the person whose medical record this is (identified by phone number)
- KGDoctor: a named physician with a speciality
- KGSymptom: a health complaint or condition the patient or family member has \
(include severity and duration when mentioned)
- KGAllergy: a substance the patient reacts to — use KGAllergy, NOT KGSymptom, even if \
symptoms are described (e.g. "allergic to penicillin" → KGAllergy node, allergen=penicillin)
- KGPreference: an explicit stated preference (time of day, location, doctor gender)
- KGFamilyMember: a person the caller is booking for (son, daughter, parent, spouse)
- KGAppointment: a confirmed or cancelled booking with a doctor (include booking ID, \
date, and time if stated)

Relationships:
- GUARDIAN_OF: when "I am booking for my [relation]", link KGPatient → KGFamilyMember
- HAS_SYMPTOM: link KGPatient or KGFamilyMember to their KGSymptom nodes
- ALLERGIC_TO: link KGPatient or KGFamilyMember to their KGAllergy nodes
- PREFERS: link KGPatient to any stated preference
- BOOKED_WITH: link KGPatient to KGAppointment or KGDoctor when a booking is confirmed
- TREATS: link KGDoctor to KGSymptom (for hospital knowledge seeding only)
"""
