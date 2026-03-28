# Hello Desi — Project Context & Memory

> This file is the persistent memory for the Hello Desi project.
> Updated: March 27, 2026

---

## Project Identity

- **Project Name**: Hello Desi
- **Owner**: Anu (adeep.gt@gmail.com)
- **Tagline**: AI-Powered WhatsApp Agent for the Indian Diaspora in the USA
- **Status**: Planning Complete → Ready to Build

---

## Finalized Decisions

### Product Decisions
- **Platform**: WhatsApp (via WhatsApp Cloud API, direct Meta integration)
- **Interaction Model**: AI Conversational (natural language, not menu-driven)
- **Target Audience**: Indian diaspora in major US metro areas (Bay Area, NYC/NJ, DFW, Chicago, Houston, Atlanta, Seattle, DC)
- **Language Support**: English, Hindi, Hinglish (code-switching)
- **Monetization**: Micro-monetization — free first, then featured business listings ($25-50/biz/mo), referral commissions ($5-20), event promotions ($50-100/event), premium subscription ($3-5/user/mo, future)

### Feature Modules (5 pillars)
1. **Community & Info Hub** — Events, temples, festivals, community orgs [MVP]
2. **Services Directory** — Restaurants, groceries, doctors, lawyers, CPAs [MVP]
3. **Immigration & Visa Help** — H-1B, EB-2/EB-3, USCIS tracking [Phase 2]
4. **Financial Services** — Remittance rates, NRE/NRO, USD-INR alerts [Phase 2]
5. **Classifieds** — Roommates, furniture, carpool, subletting [Phase 2]

### Tech Stack (Finalized)
| Component | Technology | Cost |
|-----------|-----------|------|
| Messaging | WhatsApp Cloud API (Meta direct) | ~$0.005-0.06/conv |
| Backend | FastAPI (Python) | — |
| Hosting | Railway | $20/mo |
| AI/LLM | Claude API (Haiku 90% + Sonnet 10%) | $50-80/mo MVP |
| Database | Supabase (PostgreSQL + pgvector) | Free tier |
| Caching | Redis (Upstash) | Free tier |
| RAG Engine | Firecrawl AI → pgvector | $16/mo |

### Data Pipeline (Finalized — $68-97/mo)
| Source | Tool | Target Data |
|--------|------|------------|
| Google Places API ($25/mo) | Direct API | Services directory (restaurants, groceries, temples) |
| Apify FB Groups Scraper ($0-29/mo) | Apify Cloud | Facebook group posts → classifieds + community |
| Zerowork ($15/mo) | Browser automation | Eknazar, Deshvidesh → directory + classifieds |
| Hexofy ($12/mo) | Manual Chrome extension | Sulekha professionals (lawyers, CPAs, doctors) |
| Activepieces (free, self-hosted) | Workflow orchestration | Clean, dedupe, enrich, push to Supabase |
| Firecrawl AI ($16/mo) | API scraping + RAG | USCIS data, immigration content, real-time web |

### Open-Source Base
- **Repository**: `python-whatsapp-bot-main` by daveebbelaar (Datalumina)
- **Framework**: Flask (to be migrated to FastAPI)
- **Reusable**: Webhook structure, WhatsApp message parsing, signature validation, message sending
- **Replace**: Flask→FastAPI, OpenAI→Claude, shelve→Supabase, add RAG/intent routing

---

## Cost Projections
| Stage | Users | Monthly Cost |
|-------|-------|-------------|
| MVP | ~500 | ~$220/mo |
| Growth | ~5,000 | ~$1,100/mo |
| Scale | ~50,000 | ~$8,500/mo |

## Revenue Projections
| Milestone | Users | Monthly Revenue |
|-----------|-------|----------------|
| Month 6 | 1,000 | $250-500 |
| Month 12 | 5,000 | $2,000-3,500 |
| Month 18 | 15,000 | $6,000-10,000 |
| Month 24 | 50,000 | $15,000-25,000 |

---

## Key Risks
1. **HIGH**: Meta WhatsApp policy (Jan 2026 banned general bots — position as domain-specific)
2. **HIGH**: Legal liability on immigration/finance info (disclaimers required)
3. **MEDIUM**: AI hallucination (RAG grounding required)
4. **MEDIUM**: Cold start / data freshness (pre-seed 200+ businesses per city)
5. **MEDIUM**: User acquisition in distributed community

---

## Agent Memory (Learned Preferences)
### Communication Style
- (To be updated as project progresses)

### Technical Preferences
- (To be updated as project progresses)

### Workflow Corrections
- (To be updated as project progresses)
