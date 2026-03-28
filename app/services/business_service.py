"""
Hello Desi — Business Lookup Service

Queries the Supabase `businesses` table to find Indian restaurants,
grocery stores, temples, doctors, and lawyers near a user's city.
"""

import logging
from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)

# Category aliases — map common user phrases to DB categories
CATEGORY_MAP = {
    "restaurant": "restaurant",
    "food": "restaurant",
    "eat": "restaurant",
    "dinner": "restaurant",
    "lunch": "restaurant",
    "dosa": "restaurant",
    "biryani": "restaurant",
    "curry": "restaurant",
    "thali": "restaurant",
    "grocery": "grocery",
    "groceries": "grocery",
    "store": "grocery",
    "supermarket": "grocery",
    "temple": "temple",
    "mandir": "temple",
    "gurdwara": "temple",
    "church": "temple",
    "mosque": "temple",
    "doctor": "doctor",
    "physician": "doctor",
    "medical": "doctor",
    "clinic": "doctor",
    "lawyer": "lawyer",
    "attorney": "lawyer",
    "immigration lawyer": "lawyer",
    "legal": "lawyer",
}

# US state abbreviation lookup
STATE_ABBREVS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

# Common city-state pairs people might mention without state
CITY_STATE_HINTS = {
    "columbus": "OH", "houston": "TX", "dallas": "TX", "austin": "TX",
    "plano": "TX", "irving": "TX", "san jose": "CA", "san francisco": "CA",
    "fremont": "CA", "sunnyvale": "CA", "los angeles": "CA", "irvine": "CA",
    "new york": "NY", "nyc": "NY", "jersey city": "NJ", "edison": "NJ",
    "iselin": "NJ", "chicago": "IL", "seattle": "WA", "bellevue": "WA",
    "redmond": "WA", "atlanta": "GA", "boston": "MA", "philadelphia": "PA",
    "denver": "CO", "phoenix": "AZ", "miami": "FL", "tampa": "FL",
    "orlando": "FL", "charlotte": "NC", "raleigh": "NC", "detroit": "MI",
    "troy": "MI", "minneapolis": "MN", "nashville": "TN", "portland": "OR",
    "las vegas": "NV", "salt lake city": "UT", "pittsburgh": "PA",
    "indianapolis": "IN", "washington": "DC", "herndon": "VA",
    "rockville": "MD", "san diego": "CA", "sacramento": "CA",
}


def detect_category(message: str) -> str | None:
    """Detect the business category from the user's message."""
    msg = message.lower()
    # Check multi-word keys first
    for phrase, cat in sorted(CATEGORY_MAP.items(), key=lambda x: -len(x[0])):
        if phrase in msg:
            return cat
    return None


def detect_city_state(message: str) -> tuple[str | None, str | None]:
    """
    Extract city and state from user message.
    Returns (city, state_abbrev) or (None, None).
    """
    msg = message.lower()

    # Check for state abbreviations like "OH", "TX", "CA"
    state = None
    for full_name, abbrev in STATE_ABBREVS.items():
        if full_name in msg:
            state = abbrev
            break
    # Also check 2-letter abbreviations
    if not state:
        import re
        state_match = re.search(r'\b([A-Z]{2})\b', message)
        if state_match:
            candidate = state_match.group(1)
            if candidate in STATE_ABBREVS.values():
                state = candidate

    # Check for known city names
    city = None
    for city_name, default_state in sorted(CITY_STATE_HINTS.items(), key=lambda x: -len(x[0])):
        if city_name in msg:
            city = city_name.title()
            if not state:
                state = default_state
            break

    return city, state


def search_businesses(
    message: str,
    settings: Settings,
    limit: int = 5,
) -> list[dict]:
    """
    Search the businesses table based on the user's message.
    Returns a list of matching business dicts.
    """
    category = detect_category(message)
    city, state = detect_city_state(message)

    if not city and not state:
        return []

    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        query = client.table("businesses").select(
            "name, category, subcategory, address, city, state, phone, rating, review_count"
        )

        if category:
            query = query.eq("category", category)

        if city:
            query = query.ilike("city", f"%{city}%")
        elif state:
            query = query.eq("state", state)

        query = query.order("rating", desc=True).order("review_count", desc=True)
        query = query.limit(limit)

        result = query.execute()
        logger.info(
            f"Business search: category={category}, city={city}, state={state} "
            f"→ {len(result.data)} results"
        )
        return result.data

    except Exception as e:
        logger.error(f"Business search error: {e}")
        return []


def format_businesses_for_prompt(businesses: list[dict]) -> str:
    """Format business results as context for Claude's system prompt."""
    if not businesses:
        return ""

    lines = ["\n\n📍 **Matching businesses from our database:**\n"]
    for i, b in enumerate(businesses, 1):
        stars = f"⭐ {b['rating']}" if b.get('rating') else ""
        reviews = f"({b['review_count']} reviews)" if b.get('review_count') else ""
        phone = f"📞 {b['phone']}" if b.get('phone') else ""
        lines.append(
            f"{i}. *{b['name']}* — {b.get('subcategory', b['category'])}\n"
            f"   📍 {b['address']}\n"
            f"   {stars} {reviews} {phone}"
        )

    lines.append(
        "\nPresent these results naturally to the user. Add your own helpful commentary "
        "about the options (cuisine style, what they're known for, etc.)."
    )
    return "\n".join(lines)
