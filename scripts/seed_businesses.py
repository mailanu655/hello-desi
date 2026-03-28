#!/usr/bin/env python3
"""
Hello Desi — Seed Indian businesses from Google Maps Places API (legacy).

Uses the Text Search endpoint (maps.googleapis.com/maps/api/place/textsearch)
which is more widely enabled on API keys than the new Places API.

Usage:
    pip install requests supabase python-dotenv
    python scripts/seed_businesses.py
"""

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not all([GOOGLE_MAPS_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("ERROR: Missing GOOGLE_MAPS_API_KEY, SUPABASE_URL, or SUPABASE_KEY in .env")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Search categories ────────────────────────────────────────────────
SEARCH_QUERIES = [
    {"query": "Indian restaurant", "category": "restaurant", "subcategory": "Indian"},
    {"query": "South Indian restaurant dosa", "category": "restaurant", "subcategory": "South Indian"},
    {"query": "Indian vegetarian restaurant", "category": "restaurant", "subcategory": "Vegetarian Indian"},
    {"query": "biryani restaurant", "category": "restaurant", "subcategory": "Biryani"},
    {"query": "Indian grocery store", "category": "grocery", "subcategory": "Indian Grocery"},
    {"query": "Hindu temple", "category": "temple", "subcategory": "Hindu Temple"},
    {"query": "Sikh Gurdwara", "category": "temple", "subcategory": "Gurdwara"},
    {"query": "Indian immigration lawyer", "category": "lawyer", "subcategory": "Immigration Lawyer"},
]

# ── Major US metro areas ─────────────────────────────────────────────
US_METROS = [
    # California
    ("San Jose, CA", 37.3382, -121.8863),
    ("San Francisco, CA", 37.7749, -122.4194),
    ("Los Angeles, CA", 34.0522, -118.2437),
    ("Fremont, CA", 37.5485, -121.9886),
    ("Irvine, CA", 33.6846, -117.8265),
    ("Sacramento, CA", 38.5816, -121.4944),
    ("San Diego, CA", 32.7157, -117.1611),
    # Texas
    ("Houston, TX", 29.7604, -95.3698),
    ("Dallas, TX", 32.7767, -96.7970),
    ("Austin, TX", 30.2672, -97.7431),
    ("Irving, TX", 32.8140, -96.9489),
    ("Plano, TX", 33.0198, -96.6989),
    # Northeast
    ("New York, NY", 40.7128, -74.0060),
    ("Jersey City, NJ", 40.7178, -74.0431),
    ("Edison, NJ", 40.5187, -74.4121),
    ("Philadelphia, PA", 39.9526, -75.1652),
    ("Boston, MA", 42.3601, -71.0589),
    # Mid-Atlantic / Southeast
    ("Washington, DC", 38.9072, -77.0369),
    ("Herndon, VA", 38.9696, -77.3861),
    ("Atlanta, GA", 33.7490, -84.3880),
    ("Charlotte, NC", 35.2271, -80.8431),
    ("Raleigh, NC", 35.7796, -78.6382),
    ("Tampa, FL", 27.9506, -82.4572),
    ("Orlando, FL", 28.5383, -81.3792),
    ("Miami, FL", 25.7617, -80.1918),
    # Midwest
    ("Chicago, IL", 41.8781, -87.6298),
    ("Schaumburg, IL", 42.0334, -88.0834),
    ("Columbus, OH", 39.9612, -82.9988),
    ("Cleveland, OH", 41.4993, -81.6944),
    ("Detroit, MI", 42.3314, -83.0458),
    ("Troy, MI", 42.6064, -83.1498),
    ("Minneapolis, MN", 44.9778, -93.2650),
    ("Indianapolis, IN", 39.7684, -86.1581),
    ("St. Louis, MO", 38.6270, -90.1994),
    ("Kansas City, MO", 39.0997, -94.5786),
    # Pacific Northwest
    ("Seattle, WA", 47.6062, -122.3321),
    ("Bellevue, WA", 47.6101, -122.2015),
    ("Portland, OR", 45.5152, -122.6784),
    # Mountain / Southwest
    ("Denver, CO", 39.7392, -104.9903),
    ("Phoenix, AZ", 33.4484, -112.0740),
    ("Salt Lake City, UT", 40.7608, -111.8910),
    ("Las Vegas, NV", 36.1699, -115.1398),
    # Other
    ("Nashville, TN", 36.1627, -86.7816),
    ("Pittsburgh, PA", 40.4406, -79.9959),
    ("Cincinnati, OH", 39.1031, -84.5120),
]


def text_search(query: str, lat: float, lng: float, page_token: str = None) -> dict:
    """
    Legacy Places Text Search — returns up to 20 results + next_page_token.
    """
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "location": f"{lat},{lng}",
        "radius": 40000,
        "key": GOOGLE_MAPS_API_KEY,
    }
    if page_token:
        params = {"pagetoken": page_token, "key": GOOGLE_MAPS_API_KEY}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  API error: {e}")
        return {"results": [], "status": "ERROR"}


def parse_place(place: dict, category: str, subcategory: str) -> dict:
    """Convert legacy Places result into a businesses table row."""
    loc = place.get("geometry", {}).get("location", {})
    address = place.get("formatted_address", "")

    # Parse city/state from address like "123 Main St, Columbus, OH 43215, USA"
    parts = [p.strip() for p in address.split(",")]
    city = ""
    state = ""
    if len(parts) >= 3:
        city = parts[-3] if len(parts) >= 4 else parts[-2]
        state_zip = parts[-2] if len(parts) >= 4 else parts[-1]
        state = state_zip.strip().split(" ")[0] if state_zip else ""
        if len(state) != 2 or not state.isalpha():
            state = ""

    return {
        "name": place.get("name", "Unknown"),
        "category": category,
        "subcategory": subcategory,
        "address": address,
        "city": city,
        "state": state,
        "phone": "",
        "rating": place.get("rating", 0),
        "review_count": place.get("user_ratings_total", 0),
        "latitude": loc.get("lat"),
        "longitude": loc.get("lng"),
        "source": "google_maps",
        "source_id": place.get("place_id", ""),
    }


def upsert_batch(rows: list[dict]) -> int:
    """Insert rows, skip existing source_id duplicates."""
    if not rows:
        return 0
    inserted = 0
    for row in rows:
        try:
            existing = (
                supabase.table("businesses")
                .select("id")
                .eq("source_id", row["source_id"])
                .execute()
            )
            if existing.data:
                continue
            supabase.table("businesses").insert(row).execute()
            inserted += 1
        except Exception as e:
            if "duplicate" not in str(e).lower():
                print(f"    DB error for {row['name']}: {e}")
    return inserted


def fetch_all_pages(query: str, lat: float, lng: float, category: str, subcategory: str, seen_ids: set) -> list[dict]:
    """Fetch up to 3 pages (60 results) for a single query+location."""
    all_rows = []
    page_token = None

    for page in range(3):  # max 3 pages per query
        data = text_search(query, lat, lng, page_token)
        status = data.get("status", "")

        if status not in ("OK", "ZERO_RESULTS"):
            print(f"  API status: {status} — {data.get('error_message', '')}")
            break

        results = data.get("results", [])
        for p in results:
            pid = p.get("place_id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                row = parse_place(p, category, subcategory)
                if row["city"] and row["name"]:
                    all_rows.append(row)

        page_token = data.get("next_page_token")
        if not page_token:
            break

        # Google requires ~2s delay before using next_page_token
        time.sleep(2.5)

    return all_rows


def main():
    total_inserted = 0
    total_found = 0
    seen_ids = set()

    print("=" * 60)
    print("Hello Desi — Indian Business Data Seeder (Legacy API)")
    print(f"Metros: {len(US_METROS)} | Search queries: {len(SEARCH_QUERIES)}")
    print("=" * 60)
    sys.stdout.flush()

    for metro_idx, (metro_name, lat, lng) in enumerate(US_METROS):
        print(f"\n[{metro_idx + 1}/{len(US_METROS)}] {metro_name}")
        metro_count = 0

        for sq in SEARCH_QUERIES:
            query = sq["query"]
            category = sq["category"]
            subcategory = sq["subcategory"]

            rows = fetch_all_pages(query, lat, lng, category, subcategory, seen_ids)
            total_found += len(rows)

            if rows:
                inserted = upsert_batch(rows)
                total_inserted += inserted
                metro_count += inserted
                print(f"  {query}: {len(rows)} new → {inserted} inserted")
            else:
                print(f"  {query}: 0 new")

            sys.stdout.flush()
            time.sleep(0.3)

        print(f"  ── {metro_name} total: +{metro_count}")
        sys.stdout.flush()

    print(f"\n{'=' * 60}")
    print(f"DONE! Total found: {total_found} | Inserted: {total_inserted}")
    print(f"Unique place IDs: {len(seen_ids)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
