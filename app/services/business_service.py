"""
Mira — Business Lookup Service (v2)

Queries the Supabase `businesses` table to find Indian restaurants,
grocery stores, temples, doctors, and lawyers near a user's city.

v2 improvements:
- Default city fallback (fixes "near me" queries)
- User city lookup from Supabase profile
- Fuzzy category matching (fixes typos: "biryan" → biryani)
- Multi-category support ("grocery and tiffin" → both)
- Expanded keyword map (tiffin, nanny, catering, tutor, etc.)
- Explicit no-results message
- Prompt injection sanitization
- Result deduplication
- Recency boost (created_at in sort)
- Redis-backed query cache (5-min TTL)
"""

import json
import logging
import re
from difflib import get_close_matches

from supabase import create_client
from config.settings import Settings

logger = logging.getLogger(__name__)

# ── Default location (used when user says "near me" with no city) ──
DEFAULT_CITY = "Columbus"
DEFAULT_STATE = "OH"

# ── Category aliases — map common user phrases to DB categories ────
CATEGORY_MAP = {
    # Restaurant / food
    "restaurant": "restaurant",
    "restaurants": "restaurant",
    "food": "restaurant",
    "eat": "restaurant",
    "eating": "restaurant",
    "dinner": "restaurant",
    "lunch": "restaurant",
    "breakfast": "restaurant",
    "dosa": "restaurant",
    "biryani": "restaurant",
    "curry": "restaurant",
    "thali": "restaurant",
    "tiffin": "restaurant",
    "idli": "restaurant",
    "samosa": "restaurant",
    "chaat": "restaurant",
    "pani puri": "restaurant",
    "tandoori": "restaurant",
    "naan": "restaurant",
    "buffet": "restaurant",
    "catering": "restaurant",
    "caterer": "restaurant",
    "home cooked": "restaurant",
    "homemade food": "restaurant",
    "dabba": "restaurant",
    # Grocery
    "grocery": "grocery",
    "groceries": "grocery",
    "store": "grocery",
    "supermarket": "grocery",
    "indian store": "grocery",
    "spices": "grocery",
    "atta": "grocery",
    "basmati": "grocery",
    # Temple / worship
    "temple": "temple",
    "mandir": "temple",
    "gurdwara": "temple",
    "church": "temple",
    "mosque": "temple",
    "masjid": "temple",
    "prayer": "temple",
    "pooja": "temple",
    "puja": "temple",
    # Doctor / medical
    "doctor": "doctor",
    "physician": "doctor",
    "medical": "doctor",
    "clinic": "doctor",
    "dentist": "doctor",
    "pediatri": "doctor",
    "cardiolog": "doctor",
    "eye doctor": "doctor",
    "ophthalmol": "doctor",
    "dermatolog": "doctor",
    "gynaecolog": "doctor",
    "gynecolog": "doctor",
    "orthoped": "doctor",
    "psychiatr": "doctor",
    "therapist": "doctor",
    "urgent care": "doctor",
    "hospital": "doctor",
    # Lawyer / legal
    "lawyer": "lawyer",
    "attorney": "lawyer",
    "immigration lawyer": "lawyer",
    "legal": "lawyer",
    # CPA / tax
    "cpa": "cpa",
    "accountant": "cpa",
    "tax": "cpa",
    "tax filing": "cpa",
    "chartered accountant": "cpa",
    "bookkeeper": "cpa",
    # Realtor / real estate
    "realtor": "realtor",
    "real estate": "realtor",
    "house": "realtor",
    "apartment": "realtor",
    "home buying": "realtor",
    "rental": "realtor",
    # Travel
    "travel": "travel",
    "travel agent": "travel",
    "flight": "travel",
    "ticket": "travel",
    "india trip": "travel",
    "visa stamping": "travel",
    # Insurance
    "insurance": "insurance",
    "insurance agent": "insurance",
    "life insurance": "insurance",
    "health insurance": "insurance",
    "auto insurance": "insurance",
    # Salon / beauty
    "salon": "salon",
    "beauty": "salon",
    "parlor": "salon",
    "parlour": "salon",
    "threading": "salon",
    "bridal": "salon",
    "mehndi": "salon",
    "henna": "salon",
    "haircut": "salon",
    "spa": "salon",
    # Jeweler
    "jeweler": "jeweler",
    "jeweller": "jeweler",
    "jewelry": "jeweler",
    "jewellery": "jeweler",
    "gold": "jeweler",
    "diamond": "jeweler",
    "necklace": "jeweler",
    "bangles": "jeweler",
    # Banquet / events
    "banquet": "banquet",
    "banquet hall": "banquet",
    "event venue": "banquet",
    "wedding venue": "banquet",
    "party hall": "banquet",
    "reception hall": "banquet",
    "wedding hall": "banquet",
    # Childcare / tutoring (new)
    "nanny": "childcare",
    "babysitter": "childcare",
    "daycare": "childcare",
    "childcare": "childcare",
    "tutor": "tutor",
    "tutoring": "tutor",
    "coaching": "tutor",
    "math tutor": "tutor",
    "sat prep": "tutor",
    # Driving school (new)
    "driving school": "driving",
    "driving instructor": "driving",
    "driving lesson": "driving",
    # Cleaning / services (new)
    "cleaning": "cleaning",
    "maid": "cleaning",
    "housekeeping": "cleaning",
    # Photographer (new)
    "photographer": "photographer",
    "photography": "photographer",
    "videographer": "photographer",
}

# ── US state abbreviation lookup ───────────────────────────────────
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

# ── Common city-state pairs ────────────────────────────────────────
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
    "dublin": "OH", "westerville": "OH", "hilliard": "OH",
    "milpitas": "CA", "mountain view": "CA", "santa clara": "CA",
    "artesia": "CA", "culver city": "CA",
    "richardson": "TX", "allen": "TX", "frisco": "TX",
    "morrisville": "NC", "cary": "NC", "durham": "NC",
    "decatur": "GA", "duluth": "GA",
    "naperville": "IL", "schaumburg": "IL",
    "ann arbor": "MI",
    "stamford": "CT", "new haven": "CT",
    "milwaukee": "WI", "madison": "WI",
    "st louis": "MO", "kansas city": "MO",
    "greenville": "SC", "charleston": "SC",
    "birmingham": "AL", "huntsville": "AL",
    "new orleans": "LA", "baton rouge": "LA",
    "oklahoma city": "OK", "tulsa": "OK",
    "honolulu": "HI",
    "nashua": "NH", "manchester": "NH",
    "des moines": "IA", "iowa city": "IA",
    "jacksonville": "FL", "aventura": "FL",
    "falls church": "VA", "fairfax": "VA",
    "cleveland": "OH", "cincinnati": "OH",
    "arvada": "CO", "aurora": "CO",
    "tempe": "AZ", "chandler": "AZ", "mesa": "AZ",
    "louisville": "KY", "omaha": "NE", "wichita": "KS", "shawnee": "KS",
    "little rock": "AR", "albuquerque": "NM", "providence": "RI",
    "wilmington": "DE", "burlington": "VT", "anchorage": "AK",
    "bozeman": "MT", "fargo": "ND", "sioux falls": "SD", "cheyenne": "WY",
    "montclair": "NJ", "south plainfield": "NJ", "norcross": "GA",
    "doraville": "GA", "grand prairie": "TX", "stafford": "TX",
    "lanham": "MD", "maple grove": "MN", "englewood": "CO",
    "cranston": "RI", "hockessin": "DE",
    "missoula": "MT", "overland park": "KS", "conway": "AR",
    "santa fe": "NM", "metairie": "LA", "columbia": "SC",
    "hudson": "NH", "kapaa": "HI", "edina": "MN",
    "bethesda": "MD", "arlington": "VA", "lexington": "MA",
    "jackson heights": "NY", "chamblee": "GA", "fords": "NJ",
    "scottsdale": "AZ",
}

# ── DB query cache TTL ─────────────────────────────────────────────
SEARCH_CACHE_TTL = 300  # 5 minutes


# ── Prompt injection sanitizer ─────────────────────────────────────

def _sanitize(text: str) -> str:
    """Strip characters that could be used for prompt injection."""
    if not text:
        return ""
    return (
        text
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("{", "")
        .replace("}", "")
        .replace("```", "")
        .replace("IGNORE", "")
        .replace("SYSTEM", "")
        .strip()
    )


# ── Category detection (v2: multi-category + fuzzy) ────────────────

def detect_categories(message: str) -> list[str]:
    """
    Detect ALL matching business categories from the user's message.

    v2 improvements:
    - Returns multiple categories (not just first match)
    - Falls back to fuzzy matching if no exact match
    """
    msg = message.lower()

    # ── Pass 1: exact match (multi-word first, word boundary for short keys) ─
    matches = []
    for phrase, cat in sorted(CATEGORY_MAP.items(), key=lambda x: -len(x[0])):
        # Short keywords (<=4 chars) need word boundary to avoid false positives
        # e.g. "dal" matching inside "Dallas", "eat" matching inside "theater"
        if len(phrase) <= 4:
            if re.search(r'\b' + re.escape(phrase) + r'\b', msg) and cat not in matches:
                matches.append(cat)
        elif phrase in msg and cat not in matches:
            matches.append(cat)

    if matches:
        return matches

    # ── Pass 2: fuzzy match on individual words ────────────────
    words = re.findall(r'\w+', msg)
    all_keys = list(CATEGORY_MAP.keys())
    for word in words:
        if len(word) < 3:
            continue
        close = get_close_matches(word, all_keys, n=1, cutoff=0.7)
        if close:
            cat = CATEGORY_MAP[close[0]]
            if cat not in matches:
                matches.append(cat)
                logger.info(f"Fuzzy match: '{word}' → '{close[0]}' → category '{cat}'")

    return matches


def detect_category(message: str) -> str | None:
    """Backward-compatible: returns first category or None."""
    cats = detect_categories(message)
    return cats[0] if cats else None


# ── Location detection ─────────────────────────────────────────────

def detect_city_state(message: str) -> tuple[str | None, str | None]:
    """
    Extract city and state from user message.
    Returns (city, state_abbrev) or (None, None).
    """
    msg = message.lower()

    # Check for full state names
    state = None
    for full_name, abbrev in sorted(STATE_ABBREVS.items(), key=lambda x: -len(x[0])):
        if full_name in msg:
            state = abbrev
            break

    # Also check 2-letter abbreviations (uppercase in original message)
    if not state:
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


def _get_user_city(wa_id: str, settings: Settings) -> tuple[str | None, str | None]:
    """
    Look up the user's city from their Supabase profile or business registration.
    Returns (city, state) or (None, None).
    """
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        # Check businesses table first (business owners have city on file)
        biz = (
            client.table("businesses")
            .select("city, state")
            .ilike("source_id", f"%{wa_id}%")
            .limit(1)
            .execute()
        )
        if biz.data:
            city = biz.data[0].get("city", "")
            state = biz.data[0].get("state", "")
            if city:
                return city.title(), state
    except Exception as e:
        logger.debug(f"User city lookup failed: {e}")
    return None, None


# ── Result deduplication ───────────────────────────────────────────

def _deduplicate(results: list[dict]) -> list[dict]:
    """Remove duplicate businesses by (name, phone) pair."""
    seen = set()
    unique = []
    for b in results:
        key = (b.get("name", "").lower(), b.get("phone", ""))
        if key not in seen:
            seen.add(key)
            unique.append(b)
    return unique


# ── Redis cache helpers ────────────────────────────────────────────

def _get_cached_search(cache_key: str, settings: Settings) -> list[dict] | None:
    """Check Redis for cached search results."""
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if r:
            cached = r.get(cache_key)
            if cached:
                return json.loads(cached)
    except Exception:
        pass
    return None


def _cache_search(cache_key: str, results: list[dict], settings: Settings) -> None:
    """Cache search results in Redis."""
    try:
        from app.services.session_store import _get_redis
        r = _get_redis(settings)
        if r:
            r.setex(cache_key, SEARCH_CACHE_TTL, json.dumps(results))
    except Exception:
        pass


# ── Main search function ──────────────────────────────────────────

def search_businesses(
    message: str,
    settings: Settings,
    limit: int = 5,
    wa_id: str = "",
) -> list[dict]:
    """
    Search the businesses table based on the user's message.

    v2 improvements:
    - Multi-category support
    - Default city fallback (user profile → default)
    - Redis query cache
    - Result deduplication
    - Recency boost in sort order
    """
    categories = detect_categories(message)
    city, state = detect_city_state(message)

    # ── Location fallback chain: explicit → user profile → default ──
    if not city and not state:
        if wa_id:
            city, state = _get_user_city(wa_id, settings)
            if city:
                logger.info(f"Using user profile city: {city}, {state}")

    if not city and not state:
        city, state = DEFAULT_CITY, DEFAULT_STATE
        logger.info(f"Using default city: {city}, {state}")

    # ── Check cache ────────────────────────────────────────────────
    cat_key = ",".join(sorted(categories)) if categories else "all"
    cache_key = f"search:{city or ''}:{state or ''}:{cat_key}"
    cached = _get_cached_search(cache_key, settings)
    if cached is not None:
        logger.info(f"Search cache HIT: {cache_key} → {len(cached)} results")
        return cached[:limit]

    # ── Query Supabase ─────────────────────────────────────────────
    try:
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        query = client.table("businesses").select(
            "id, name, category, subcategory, address, city, state, "
            "phone, rating, review_count, is_featured, created_at"
        )

        # Multi-category filter
        if len(categories) == 1:
            query = query.eq("category", categories[0])
        elif len(categories) > 1:
            query = query.in_("category", categories)
        # No category filter → return all businesses in the city

        if city:
            query = query.ilike("city", f"%{city}%")
        elif state:
            query = query.eq("state", state)

        # Ranking: featured → rating → reviews → recency
        query = (
            query
            .order("is_featured", desc=True)
            .order("rating", desc=True)
            .order("review_count", desc=True)
            .order("created_at", desc=True)
        )
        # Fetch extra for dedup headroom
        query = query.limit(limit + 5)

        result = query.execute()

        # Deduplicate
        businesses = _deduplicate(result.data)[:limit]

        logger.info(
            f"Business search: categories={categories}, city={city}, state={state} "
            f"→ {len(businesses)} results (raw={len(result.data)})"
        )

        # Cache results
        _cache_search(cache_key, businesses, settings)

        return businesses

    except Exception as e:
        logger.error(f"Business search error: {e}")
        return []


# ── No-results message ─────────────────────────────────────────────

NO_RESULTS_MESSAGE = (
    "I couldn't find businesses matching your search 😅\n\n"
    "Try being more specific:\n"
    "👉 _\"Indian restaurants in Houston\"_\n"
    "👉 _\"grocery store in Plano TX\"_\n"
    "👉 _\"dentist in Columbus\"_\n\n"
    "🏪 Own a business? Reply *\"add my business\"* to get listed FREE!"
)


# ── Format for Claude prompt ───────────────────────────────────────

def format_businesses_for_prompt(businesses: list[dict]) -> str:
    """
    Format business results as context for Claude's system prompt.

    v2: sanitizes all business data to prevent prompt injection.
    """
    if not businesses:
        return ""

    lines = ["\n\n📍 **Matching businesses from our database:**\n"]
    for i, b in enumerate(businesses, 1):
        # Sanitize all user-supplied fields
        name = _sanitize(b.get("name", "Unknown"))
        address = _sanitize(b.get("address", ""))
        subcategory = _sanitize(b.get("subcategory", "")) or _sanitize(b.get("category", ""))

        featured = "⭐ FEATURED " if b.get("is_featured") else ""
        stars = f"⭐ {b['rating']}" if b.get("rating") else ""
        reviews = f"({b['review_count']} reviews)" if b.get("review_count") else ""
        phone = f"📞 {b['phone']}" if b.get("phone") else ""
        lines.append(
            f"{i}. {featured}*{name}* — {subcategory}\n"
            f"   📍 {address}\n"
            f"   {stars} {reviews} {phone}"
        )

    lines.append(
        "\nPresent these results naturally to the user. Add your own helpful commentary "
        "about the options (cuisine style, what they're known for, etc.)."
        "\n\nIMPORTANT: At the very end of your response, always add this line:"
        '\n🏪 Own a business? Reply *"add my business"* to get listed FREE!'
    )
    return "\n".join(lines)
