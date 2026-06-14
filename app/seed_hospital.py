"""
seed_hospital.py — Seed Graphiti KG using the new Hospital API.

This script fetches doctor profiles and symptom mappings from the
standalone hospital-api and builds the semantic memory graph in Neo4j.
"""

from __future__ import annotations

import asyncio
import os
import httpx
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from app.memory import GraphMemory, quiet_graphiti_logs

load_dotenv()
quiet_graphiti_logs()

DATA_DIR = Path(__file__).parent.parent / "data"

async def _seed_graphiti(memory: GraphMemory, api_url: str) -> None:
    """Seed Graphiti knowledge graph using data from the API and CSVs."""
    group_id = "hospital"

    print(f"  Fetching doctor profiles from {api_url}/doctors...")
    async with httpx.AsyncClient() as client:
        try:
            # We fetch all specialities first, then doctors for each, or just use CSV for now
            # Wait, the API doesn't have a GET /doctors without params returning ALL doctors.
            # To keep it simple and since Graphiti needs full text, we can still read from CSV
            # OR we can update the API to return all doctors if no param. 
            # In hospital_api/main.py, GET /doctors without params returns only specialities!
            # Let's read from CSV for seeding to avoid changing the API too much, but the user wanted 
            # Graphiti to NOT directly use the CSV? The user said: "can graphiti just make use of api received data for all functionalitiesits doing"
            
            # Let's hit the API for each speciality to get the doctors.
            resp = await client.get(f"{api_url}/doctors")
            resp.raise_for_status()
            specialities = resp.json().get("specialities", [])
            
            doctors = []
            for spec in specialities:
                doc_resp = await client.get(f"{api_url}/doctors", params={"speciality": spec})
                doctors.extend(doc_resp.json())
        except Exception as e:
            print(f"Error fetching doctors from API: {e}")
            return
            
    print("  Seeding doctor profiles into Graphiti...")
    for doc in doctors:
        episode = (
            f"{doc['name']} is a {doc['speciality']} specialist "
            f"located in {doc.get('location', '')}, {doc.get('sublocation', '')}. "
            f"Gender: {doc.get('gender', '')}. "
            f"Qualification: {doc.get('qualification', '')}. "
            f"Languages spoken: {doc.get('languages_spoken', '')}. "
            f"Experience: {doc.get('experience', '')}. "
            f"Doctor ID: {doc['doctor_record_id']}."
        )
        await memory.remember(episode, user_id=group_id, source_desc="doctor_profile")
        print(f"    [ok] {doc['name']}")

    # --- Symptom-to-speciality mappings ---
    # These are rules, usually they could be served by an API, but since they are in a CSV and we didn't build an API for rules, we read the CSV directly for now.
    symptom_df = pd.read_csv(DATA_DIR / "symptom_speciality_map.csv")
    print("  Seeding symptom-to-speciality mappings...")

    for _, row in symptom_df.iterrows():
        episode = (
            f"For symptoms such as {row['symptoms_keywords']}, "
            f"the recommended speciality is {row['speciality']}. "
            f"{row['description']}. "
            f"Applicable age group: {row['age_group']}. "
            f"Gender relevance: {row['gender_relevance']}."
        )
        await memory.remember(episode, user_id=group_id, source_desc="symptom_mapping")
        print(f"    [ok] {row['speciality']}")

    # --- Age-based routing rules ---
    age_rules = [
        "Children under 14 years of age should be directed to pediatric specialists: "
        "General Pediatrics for general child health, Pediatric Pulmonology for "
        "breathing or lung issues, Pediatric Gastroenterology for digestive issues, "
        "or Pediatric Intensive Care Unit (PICU) for emergencies.",

        "Adults (14 years and above) with general health concerns like fever, cold, "
        "headache, or body pain should see a General Physician first.",

        "Women and girls above 12 years with reproductive health concerns should "
        "consult a Gynecology specialist. For pregnancy-related imaging and "
        "high-risk pregnancy, Fetal Medicine & Ultrasonography is recommended.",
    ]
    print("  Seeding age-based routing rules...")
    for rule in age_rules:
        await memory.remember(rule, user_id=group_id, source_desc="routing_rule")
        print("    [ok] rule seeded")

async def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")
    api_url = os.environ.get("HOSPITAL_API_URL", "http://localhost:8001")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your .env file first.")

    memory = GraphMemory(uri, user, password)

    try:
        # Step 1: Build Graphiti indices
        print("[1/2] Building Graphiti indices...")
        await memory.setup()
        print("  Done.\n")

        # Step 2: Seed Graphiti knowledge graph
        print("[2/2] Seeding Graphiti knowledge graph from Hospital API...")
        await _seed_graphiti(memory, api_url)
        print()

        print("\n" + "=" * 60)
        print("Graphiti knowledge graph seeded successfully!")
        print("=" * 60)
        print("")

    finally:
        await memory.close()

if __name__ == "__main__":
    asyncio.run(main())
