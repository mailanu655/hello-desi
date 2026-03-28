# Hello Desi — Implementation Plan

> Master plan for building the AI-powered WhatsApp agent for the Indian diaspora in the USA.
> Last Updated: March 27, 2026

---

## Open-Source Project Assessment

### Repository: `python-whatsapp-bot-main` (by daveebbelaar / Datalumina)

#### Structure Overview
```
python-whatsapp-bot-main/
├── run.py                          # Flask app entry point (port 8000)
├── app/
│   ├── __init__.py                 # Flask app factory with blueprint registration
│   ├── config.py                   # Loads env vars into Flask config
│   ├── views.py                    # Webhook GET (verify) + POST (handle message)
│   ├── decorators/
│   │   └── security.py             # HMAC SHA256 signature validation decorator
│   ├── services/
│   │   └── openai_service.py       # OpenAI Assistants API with thread management
│   └── utils/
│       └── whatsapp_utils.py       # Message parsing, response generation, send_message
├── start/
│   ├── whatsapp_quickstart.py      # Standalone message sending example
│   └── assistants_quickstart.py    # OpenAI assistant setup example
├── data/
│   └── airbnb-faq.pdf              # Example FAQ data for RAG
├── docs/
│   └── botpress_connection.md
├── example.env                     # Environment variable template
└── requirements.txt                # flask, python-dotenv, openai, aiohttp, requests
```

#### What's Reusable (Keep & Adapt)
| Component | File | Why It's Useful |
|-----------|------|-----------------|
| Webhook verification | `views.py` → `verify()` | Correct implementation of Meta's hub.challenge flow |
| Signature validation | `security.py` → `signature_required` | HMAC SHA256 validation of incoming webhook payloads |
| Message parsing | `whatsapp_utils.py` → `is_valid_whatsapp_message()` | Correctly extracts wa_id, name, message_body from nested payload |
| Message sending | `whatsapp_utils.py` → `send_message()` | Working HTTP POST to Meta Graph API with auth headers |
| Text formatting | `whatsapp_utils.py` → `process_text_for_whatsapp()` | Converts markdown bold to WhatsApp bold format |
| Thread management pattern | `openai_service.py` → `check_if_thread_exists()` | The pattern of per-user conversation threads (adapt for Supabase) |
| Environment config | `config.py` + `example.env` | All required Meta API variables identified |

#### What Must Be Replaced
| Component | Current | Hello Desi Replacement | Why |
|-----------|---------|------------------------|-----|
| Web framework | Flask | **FastAPI** | Async support, auto-docs, better performance for webhooks |
| AI/LLM | OpenAI Assistants API | **Claude API (Anthropic)** | Better cost efficiency, stronger reasoning |
| Thread storage | `shelve` (file-based) | **Supabase PostgreSQL** | Production-grade, supports vector search, multi-user |
| Response generation | `response.upper()` / OpenAI | **Custom RAG pipeline** | Need domain-specific knowledge + intent routing |
| Configuration | `python-dotenv` only | **Pydantic Settings** | Type-safe config validation |

#### What's Missing (Must Build)
| Component | Priority | Description |
|-----------|----------|-------------|
| Intent Router | P0 | Classify messages → route to correct module (directory/immigration/finance/community/classifieds) |
| RAG Pipeline | P0 | Vector search on business directory + immigration FAQs + financial content |
| User Management | P0 | User profiles with city, language preference, interests in Supabase |
| Services Directory Module | P0 | Search businesses by category, location, rating |
| Community Module | P1 | Events discovery by city and date |
| Immigration Module | P1 | USCIS data integration, visa bulletin tracking |
| Finance Module | P2 | Remittance rate comparison, exchange rate alerts |
| Classifieds Module | P2 | Post/browse classifieds via chat |
| Conversation Memory | P1 | Store last 10 messages per user for context |
| Rate Limiting | P1 | Per-user message limits (20/day free) |
| Multilingual Support | P1 | Hindi/Hinglish detection and response |
| Admin Dashboard | P3 | Analytics, user stats, content management |
| Data Seeding Pipeline | P0 | Google Maps API + Zerowork + Hexofy + Apify ingestion |

#### Verdict: USE AS FOUNDATION, REWRITE INCREMENTALLY

The open-source project provides a **solid, working WhatsApp Cloud API integration** that correctly handles webhooks, signature validation, and message flow. This saves 1-2 weeks of setup work. However, it needs a complete architecture upgrade for Hello Desi's requirements.

**Recommended approach**: Don't fork and modify. Instead, use it as a **reference implementation** while building the FastAPI version from scratch, porting the proven patterns (webhook handling, signature validation, message parsing) into the new architecture.

---

## Phase-by-Phase Implementation Plan

### Phase 1: Foundation & MVP Core (Weeks 1-4)
**Goal**: Working WhatsApp bot with Services Directory in one city.

#### Step 1.1: Project Setup & Infrastructure
- [ ] Initialize Python project with `pyproject.toml` / `uv` package manager
- [ ] Set up FastAPI application structure
- [ ] Configure Pydantic Settings for environment variables
- [ ] Set up Supabase project (database + pgvector extension)
- [ ] Set up Redis on Upstash (free tier)
- [ ] Deploy skeleton app to Railway
- [ ] Set up ngrok for local webhook development

#### Step 1.2: WhatsApp Cloud API Integration
- [ ] Create Meta Business account and WhatsApp Business app
- [ ] Port webhook verification from Flask reference to FastAPI
- [ ] Port signature validation decorator to FastAPI dependency
- [ ] Port message parsing utilities to FastAPI
- [ ] Port message sending function (adapt for async httpx)
- [ ] Test end-to-end: receive message → echo back
- [ ] Set up permanent system user token (not 24-hour token)

#### Step 1.3: Claude AI Integration
- [ ] Set up Anthropic API client
- [ ] Create base conversation handler with Claude Haiku
- [ ] Implement conversation memory (store last 10 messages in Supabase)
- [ ] Create system prompt with Hello Desi personality and instructions
- [ ] Implement text formatting for WhatsApp (markdown → WhatsApp bold/italic)
- [ ] Test conversational responses

#### Step 1.4: Database Schema & Models
- [ ] Design and create Supabase schema:
  - `users` — wa_id, name, city, language, preferences, created_at
  - `businesses` — name, category, address, city, phone, rating, lat/lng, source
  - `conversations` — user_id, messages (JSONB), updated_at
  - `events` — title, date, city, venue, source, category
  - `classifieds` — user_id, type, title, description, city, status
- [ ] Enable pgvector extension
- [ ] Create embedding columns for business semantic search
- [ ] Set up Row Level Security policies
- [ ] Create SQLAlchemy / Supabase-py models

#### Step 1.5: User Onboarding Flow
- [ ] First-message detection (new user vs returning)
- [ ] Onboarding conversation: "Hi! I'm Hello Desi 🙏 Which city are you in?"
- [ ] Store user profile in Supabase
- [ ] Language detection (English/Hindi/Hinglish)

#### Step 1.6: Services Directory Module (MVP)
- [ ] Seed data: Run Google Places API for one city (Bay Area or DFW)
- [ ] Create business search function (by category + city)
- [ ] Implement semantic search with pgvector embeddings
- [ ] Format business results for WhatsApp (name, rating, address, phone)
- [ ] Test: "Find Indian restaurants near Plano TX"

#### Step 1.7: Data Seeding Pipeline (Initial)
- [ ] Google Places API script to bulk-pull Indian businesses
- [ ] Set up Zerowork TaskBot for Eknazar (one city)
- [ ] Set up Activepieces workflow: Sheets → clean → Supabase
- [ ] Seed 200+ businesses for launch city
- [ ] Data quality validation

#### Step 1.8: Deploy & Beta Test
- [ ] Deploy to Railway with production env vars
- [ ] Set up webhook with permanent URL (Railway or ngrok static domain)
- [ ] Test with 10-20 beta users
- [ ] Collect feedback, fix bugs
- [ ] Monitor logs and costs

---

### Phase 2: Community + Immigration Modules (Weeks 5-8)
**Goal**: Add two highest-demand features. Expand to 2 cities.

#### Step 2.1: Intent Router
- [ ] Build lightweight intent classifier (keyword + Claude-based)
- [ ] Route messages to: directory_search | community_events | immigration_info | finance_info | classifieds | general_chat
- [ ] Reduce unnecessary LLM calls for simple lookups

#### Step 2.2: Community & Events Module
- [ ] Set up Firecrawl to crawl community event sites
- [ ] Build events database with city/date/category indexing
- [ ] Event search by city, date range, category
- [ ] Weekly event digest feature ("What's happening this weekend in Dallas?")
- [ ] Scrape Deshvidesh events with Zerowork

#### Step 2.3: Immigration Info Module
- [ ] Set up Firecrawl to crawl USCIS visa bulletin page (scheduled weekly)
- [ ] Build immigration FAQ knowledge base (RAG)
- [ ] Index common questions: EB-2/EB-3 processing times, H-1B lottery, AP/EAD
- [ ] Implement legal disclaimers on every immigration response
- [ ] Test with common diaspora immigration questions

#### Step 2.4: Conversation Memory Enhancement
- [ ] Implement 10-message sliding window per user
- [ ] Context injection into Claude prompts
- [ ] User preference tracking (favorite city, language preference)

#### Step 2.5: Data Pipeline Expansion
- [ ] Expand Google Places scraping to 2nd city
- [ ] Set up Apify for Facebook group posts extraction
- [ ] Hexofy curation of Sulekha professionals
- [ ] Activepieces pipeline: classify FB posts → events vs classifieds vs noise

#### Step 2.6: Beta Expansion
- [ ] Expand beta to 100-200 users, 2 cities
- [ ] Feedback collection and iteration
- [ ] Performance optimization (response time < 3 seconds)

---

### Phase 3: Full Feature Set + Scale (Weeks 9-16)
**Goal**: All 5 modules live. 5+ metros. Public launch.

#### Step 3.1: Financial Services Module
- [ ] Remittance rate comparison (Firecrawl scraping Wise, Remitly pages)
- [ ] USD-INR exchange rate tracking (Open Exchange Rates API)
- [ ] NRE/NRO account FAQ knowledge base
- [ ] Rate alert subscriptions ("Alert me when USD/INR > 87")
- [ ] Financial disclaimers

#### Step 3.2: Classifieds Module
- [ ] Post classifieds via chat ("I want to sell my sofa in Plano TX, $200")
- [ ] Browse classifieds by city and category
- [ ] Classifieds moderation (basic content filtering)
- [ ] Connect buyers and sellers via WhatsApp

#### Step 3.3: Multilingual Support
- [ ] Language detection from user messages
- [ ] Hindi/Hinglish response generation (Claude handles natively)
- [ ] System prompts with multilingual instructions
- [ ] Voice message transcription (Whisper API or similar)

#### Step 3.4: Scale Data Pipeline
- [ ] Expand to 5+ metro areas
- [ ] 1000+ business listings
- [ ] Automated weekly data refresh via Zerowork + Firecrawl
- [ ] Data quality monitoring

#### Step 3.5: Public Launch
- [ ] Production deployment on Railway (auto-scaling)
- [ ] Error monitoring (Sentry)
- [ ] Cost monitoring and budget alerts
- [ ] Launch to 1,000 users

---

### Phase 4: Growth & Monetization (Weeks 17-24)
**Goal**: 5,000+ users. First revenue streams.

#### Step 4.1: Featured Business Listings
- [ ] Business claiming flow ("Is this your business? Verify ownership")
- [ ] Premium listing placement in search results
- [ ] Business dashboard (simple web portal)
- [ ] Stripe integration for $25-50/mo payments

#### Step 4.2: Growth Mechanics
- [ ] Referral program via WhatsApp sharing
- [ ] Community partnerships (temples, student associations)
- [ ] Content marketing (Instagram, diaspora blogs)
- [ ] WhatsApp group integration

#### Step 4.3: Analytics Dashboard
- [ ] User engagement metrics (DAU, messages/user, retention)
- [ ] Module usage breakdown
- [ ] Search query analytics (what are people looking for?)
- [ ] Revenue tracking

---

## Required External Resources

See the "API Keys & Setup Checklist" section in claude.md for detailed step-by-step instructions on obtaining each required resource.

| Resource | Purpose | How to Get |
|----------|---------|-----------|
| Meta Developer Account | WhatsApp Cloud API | developers.facebook.com |
| Meta Business Account | Business verification | business.facebook.com |
| WhatsApp Business App | API access | Created in Meta Developer Dashboard |
| System User Access Token | Permanent API auth | Meta Business Settings → System Users |
| Phone Number for Bot | WhatsApp bot identity | New SIM or virtual number |
| Anthropic API Key | Claude AI responses | console.anthropic.com |
| Supabase Project | Database + vector search | supabase.com |
| Upstash Redis | Caching + rate limiting | upstash.com |
| Railway Account | Hosting | railway.app |
| ngrok Account + Domain | Local development webhook | ngrok.com |
| Google Maps API Key | Places API for directory | console.cloud.google.com |
| Apify Account | Facebook group scraping | apify.com |
| Zerowork Account | Desi site scraping | zerowork.io |
| Hexofy Subscription | Manual curation | hexofy.com |
| Firecrawl API Key | RAG content pipeline | firecrawl.dev |
| Open Exchange Rates Key | USD-INR rates | openexchangerates.org |

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                     USER ON WHATSAPP                         │
│              (English / Hindi / Hinglish)                     │
└──────────────────────┬───────────────────────────────────────┘
                       │ Message
                       ▼
┌──────────────────────────────────────────────────────────────┐
│              WHATSAPP CLOUD API (Meta)                        │
│           Webhook → POST /api/v1/webhook                     │
└──────────────────────┬───────────────────────────────────────┘
                       │ Validated payload
                       ▼
┌──────────────────────────────────────────────────────────────┐
│              FASTAPI BACKEND (Railway)                        │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐      │
│  │ Webhook      │  │ Intent       │  │ Rate Limiter   │      │
│  │ Handler      │→ │ Router       │→ │ (Redis)        │      │
│  └─────────────┘  └──────┬───────┘  └────────────────┘      │
│                          │                                    │
│         ┌────────────────┼────────────────┐                  │
│         ▼                ▼                ▼                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ Directory   │  │ Community   │  │ Immigration │          │
│  │ Module      │  │ Module      │  │ Module      │          │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘          │
│         │                │                │                   │
│         └────────────────┼────────────────┘                  │
│                          ▼                                    │
│                  ┌──────────────┐                             │
│                  │ Claude AI    │                             │
│                  │ (RAG + LLM)  │                             │
│                  └──────┬───────┘                             │
│                         │                                    │
│                         ▼                                    │
│                  ┌──────────────┐                             │
│                  │ Response     │                             │
│                  │ Formatter    │→ WhatsApp Cloud API → User │
│                  └──────────────┘                             │
└──────────────────────────────────────────────────────────────┘
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
     ┌───────────┐ ┌────────┐ ┌──────────┐
     │ Supabase  │ │ Redis  │ │ Firecrawl│
     │ (PG +     │ │ Cache  │ │ (RAG     │
     │  pgvector)│ │        │ │  content)│
     └───────────┘ └────────┘ └──────────┘
```

---

## File Structure (Target)

```
Hello Desi/
├── claude.md                          # AI agent instructions & build guide
├── context.md                         # Project memory & decisions
├── implementation_plan.md             # This file
├── .env.example                       # Environment variable template
├── pyproject.toml                     # Python project config (uv/pip)
├── Dockerfile                         # Container for Railway deployment
├── app/
│   ├── __init__.py                    # FastAPI app factory
│   ├── main.py                        # App entry point
│   ├── api/
│   │   ├── __init__.py
│   │   ├── webhook.py                 # WhatsApp webhook endpoints
│   │   └── deps.py                    # Shared dependencies (auth, rate limit)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── claude_service.py          # Claude API integration
│   │   ├── whatsapp_service.py        # WhatsApp message sending
│   │   ├── intent_router.py           # Message intent classification
│   │   ├── rag_service.py             # RAG pipeline (Firecrawl + pgvector)
│   │   └── user_service.py            # User management
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py                    # User model
│   │   ├── business.py                # Business directory model
│   │   ├── event.py                   # Community event model
│   │   ├── classified.py              # Classifieds model
│   │   └── conversation.py            # Conversation history model
│   ├── modules/
│   │   ├── directory/                 # Services directory module
│   │   │   ├── __init__.py
│   │   │   ├── handler.py
│   │   │   └── search.py
│   │   ├── community/                 # Community & events module
│   │   │   ├── __init__.py
│   │   │   └── handler.py
│   │   ├── immigration/               # Immigration info module
│   │   │   ├── __init__.py
│   │   │   └── handler.py
│   │   ├── finance/                   # Financial services module
│   │   │   ├── __init__.py
│   │   │   └── handler.py
│   │   └── classifieds/               # Classifieds module
│   │       ├── __init__.py
│   │       └── handler.py
│   └── utils/
│       ├── __init__.py
│       ├── whatsapp_utils.py          # Message parsing & formatting
│       ├── security.py                # Signature validation
│       └── formatters.py              # WhatsApp text formatting
├── config/
│   ├── __init__.py
│   └── settings.py                    # Pydantic Settings (env vars)
├── data/
│   ├── seeds/                         # Data seeding scripts
│   │   ├── seed_google_places.py
│   │   ├── seed_eknazar.py
│   │   └── seed_immigration_faq.py
│   └── schemas/                       # Supabase migration SQL
│       ├── 001_create_users.sql
│       ├── 002_create_businesses.sql
│       ├── 003_create_events.sql
│       ├── 004_create_classifieds.sql
│       └── 005_create_conversations.sql
├── scripts/
│   ├── setup_supabase.py              # Database setup script
│   └── test_webhook.py                # Webhook testing utility
├── tests/
│   ├── test_webhook.py
│   ├── test_intent_router.py
│   └── test_directory_search.py
├── docs/
│   ├── api-keys-setup.md              # Step-by-step API key instructions
│   └── deployment.md                  # Railway deployment guide
└── .claude/
    └── skills/
        ├── data-seeding.md            # Skill for running data seeding pipeline
        └── deploy.md                  # Skill for deployment process
```
