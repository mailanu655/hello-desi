"""
Hello Desi — Intent Router

Classifies incoming messages into intents for routing to the correct module.
Uses keyword matching first (free, instant), falls back to Claude for ambiguous messages.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    DIRECTORY_SEARCH = "directory_search"
    COMMUNITY_EVENTS = "community_events"
    IMMIGRATION = "immigration"
    FINANCE = "finance"
    CLASSIFIEDS = "classifieds"
    GENERAL_CHAT = "general_chat"
    ONBOARDING = "onboarding"


@dataclass
class IntentResult:
    intent: Intent
    confidence: float
    entities: dict


# Keyword patterns for fast routing (no LLM call needed)
INTENT_PATTERNS: dict[Intent, list[str]] = {
    Intent.DIRECTORY_SEARCH: [
        r"restaurant", r"grocery", r"grocer", r"doctor", r"lawyer",
        r"temple", r"mandir", r"gurudwara", r"mosque", r"store",
        r"shop", r"salon", r"dentist", r"cpa", r"accountant",
        r"mechanic", r"realtor", r"real estate", r"pharmacy",
        r"find\s+(?:a|an|me)", r"where\s+(?:can|is|are)",
        r"recommend", r"suggest", r"best\s+(?:indian|desi)",
        r"nearby", r"near me",
    ],
    Intent.COMMUNITY_EVENTS: [
        r"event", r"holi", r"diwali", r"navratri", r"garba",
        r"dandiya", r"puja", r"pongal", r"onam", r"eid",
        r"festival", r"celebration", r"meetup", r"gathering",
        r"this weekend", r"happening", r"what.s going on",
        r"community", r"association",
    ],
    Intent.IMMIGRATION: [
        r"visa", r"h[- ]?1b", r"green\s*card", r"uscis",
        r"immigration", r"eb[- ]?[23]", r"i[- ]?485", r"i[- ]?140",
        r"i[- ]?130", r"ead", r"ap\b", r"advance\s*parole",
        r"opt", r"cpt", r"f[- ]?1", r"l[- ]?1", r"o[- ]?1",
        r"processing\s*time", r"priority\s*date", r"bulletin",
        r"rfe", r"noid", r"uscis", r"petition",
    ],
    Intent.FINANCE: [
        r"remittance", r"send\s*money", r"transfer\s*money",
        r"nre", r"nro", r"exchange\s*rate", r"usd.*inr", r"inr.*usd",
        r"wise", r"remitly", r"xoom", r"western\s*union",
        r"tax\s*(?:filing|return|india)", r"double\s*taxation",
        r"fbar", r"fatca",
    ],
    Intent.CLASSIFIEDS: [
        r"roommate", r"room\s*(?:for|available|needed)",
        r"selling", r"for\s*sale", r"buy(?:ing)?",
        r"carpool", r"ride\s*share", r"sublet",
        r"furniture", r"apartment", r"looking\s*for",
        r"moving\s*sale", r"free\s*stuff",
    ],
}


def classify_intent(message: str) -> IntentResult:
    """
    Classify a message into an intent using keyword matching.

    Returns the best matching intent with confidence score.
    Falls back to GENERAL_CHAT if no keywords match.
    """
    message_lower = message.lower().strip()

    # Score each intent based on keyword matches
    scores: dict[Intent, int] = {}
    for intent, patterns in INTENT_PATTERNS.items():
        matches = sum(1 for p in patterns if re.search(p, message_lower))
        if matches > 0:
            scores[intent] = matches

    if scores:
        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]
        # Confidence: normalize by number of patterns for that intent
        confidence = min(best_score / 3.0, 1.0)  # 3+ matches = 100% confidence

        logger.info(
            f"Intent classified: {best_intent.value} "
            f"(confidence={confidence:.2f}, matches={best_score})"
        )

        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            entities={},  # TODO: extract entities (city, category) in Phase 2
        )

    # No keyword match — default to general chat
    # In Phase 2, this will fall back to Claude classification (~$0.001/call)
    logger.info("No keyword match — defaulting to GENERAL_CHAT")
    return IntentResult(
        intent=Intent.GENERAL_CHAT,
        confidence=0.5,
        entities={},
    )
