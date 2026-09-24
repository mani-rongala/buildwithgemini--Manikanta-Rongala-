"""Seed script to populate Firestore database with initial transit routes for CommutePulse AI."""

import datetime
from google.cloud import firestore

# HARDCODED PROJECT ID (REQUIRED FOR AGENT PLATFORM COMPATIBILITY)
FIRESTORE_PROJECT = "qwiklabs-gcp-02-5e57235b3d5f"


def seed_data():
    db = firestore.Client(project=FIRESTORE_PROJECT)
    collection_ref = db.collection("transit_routes")

    sample_routes = [
        {
            "route_id": "route-bay-bridge",
            "name": "Bay Bridge Express Carpool",
            "origin": "Oakland Downtown",
            "destination": "San Francisco Financial District",
            "transport_mode": "Carpool / HOV3+",
            "avg_duration_mins": 25,
            "est_cost_usd": 3.00,
            "co2_saved_kg": 4.2,
            "traffic_status": "Light traffic on HOV lane",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        {
            "route_id": "route-bart-transbay",
            "name": "Transbay BART Rapid Metro",
            "origin": "Berkeley Plaza",
            "destination": "San Francisco Embarcadero",
            "transport_mode": "BART Metro",
            "avg_duration_mins": 22,
            "est_cost_usd": 4.65,
            "co2_saved_kg": 5.1,
            "traffic_status": "On time (Trains every 6 mins)",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        {
            "route_id": "route-peninsula-caltrain",
            "name": "Peninsula Express Caltrain",
            "origin": "Palo Alto Station",
            "destination": "San Francisco 4th & King",
            "transport_mode": "Electric Caltrain",
            "avg_duration_mins": 40,
            "est_cost_usd": 6.50,
            "co2_saved_kg": 6.8,
            "traffic_status": "On time (Electric express)",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        {
            "route_id": "route-ferry-express",
            "name": "Alameda Ferry Shuttle",
            "origin": "Alameda Main Street Ferry",
            "destination": "SF Ferry Building",
            "transport_mode": "Ferry Boat",
            "avg_duration_mins": 20,
            "est_cost_usd": 4.50,
            "co2_saved_kg": 3.5,
            "traffic_status": "Smooth sailing (Scenic & no congestion)",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    ]

    print(f"Seeding {len(sample_routes)} transit routes into Firestore (project: {FIRESTORE_PROJECT})...")
    for route in sample_routes:
        doc_ref = collection_ref.document(route["route_id"])
        doc_ref.set(route)
        print(f"  ✓ Seeded document '{route['route_id']}': {route['name']}")

    print("Seeding complete!")


if __name__ == "__main__":
    seed_data()
