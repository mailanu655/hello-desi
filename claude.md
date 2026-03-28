# Hello Desi — AI Agent Rules & Build Guide

> You are building Hello Desi, an AI-powered WhatsApp agent for the Indian diaspora in the USA.
> Follow these instructions meticulously until the project is fully completed.

---

## Startup Instructions

Before starting ANY task:
1. Read `context.md` to recall all project decisions and learned preferences
2. Read `implementation_plan.md` to understand current phase and progress
3. When you learn a new preference or make a correction, update `context.md` immediately
4. After completing any step, mark it done in `implementation_plan.md`

---

## Your Role

You are the lead developer building Hello Desi end-to-end. You have full authority to make implementation decisions within the framework of the finalized architecture. When in doubt, prefer simplicity and ship speed over perfection.

### Core Principles
- **Ship incrementally**: Get something working fast, then improve
- **Reference, don't fork**: Use the open-source WhatsApp bot as a reference, but build clean in FastAPI
- **Test each layer**: Webhook → AI → Database → Module — verify each before moving on
- **Cost-conscious**: Always consider API costs. Use caching, intent routing, and tiered LLMs
- **Community-first**: Every feature should feel like asking a knowledgeable desi friend

---

## Project Identity

- **Name**: Hello Desi
- **Owner**: Anu (adeep.gt@gmail.com)
- **Platform**: WhatsApp (Cloud API, direct Meta integration)
- **Language**: Python 3.11+ with FastAPI
- **AI**: Claude API (Haiku for 90% queries, Sonnet for complex immigration/finance)
- **Database**: Supabase (PostgreSQL + pgvector)
- **Hosting**: Railway
- **Target Users**: Indian diaspora in major US metro areas

---

## Build Guide — Step-by-Step Prompts

Follow these prompts in order. Each prompt represents a discrete buildable unit. Do not skip steps.

### PROMPT 1: Project Initialization

```
Set up the Hello Desi Python project:
1. Create pyproject.toml with dependencies:
   - fastapi, uvicorn, httpx (async HTTP client)
   - anthropic (Claude API)
   - supabase-py (Supabase client)
   - redis (Upstash)
   - pydantic-settings (config management)
   - python-dotenv
2. Create config/settings.py using Pydantic BaseSettings:
   - All env vars from the reference project: ACCESS_TOKEN, APP_ID, APP_SECRET,
     RECIPIENT_WAID, VERSION, PHONE_NUMBER_ID, VERIFY_TOKEN
   - Add: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY, REDIS_URL
   - Add: GOOGLE_MAPS_API_KEY, FIRECRAWL_API_KEY
3. Create .env.example with all variables listed (empty values)
4. Create app/main.py with FastAPI app factory
5. Create Dockerfile for Railway deployment
6. Verify: `uvicorn app.main:app --reload` starts without errors
```

### PROMPT 2: WhatsApp Webhook (Port from Reference)

```
Port the WhatsApp webhook from the Flask reference project to FastAPI:

REFERENCE FILES (read these first):
- python-whatsapp-bot-main/app/views.py (webhook endpoints)
- python-whatsapp-bot-main/app/decorators/security.py (signature validation)
- python-whatsapp-bot-main/app/utils/whatsapp_utils.py (message parsing + sending)

BUILD:
1. Create app/api/webhook.py with:
   - GET /api/v1/webhook — verification endpoint (port verify() function)
   - POST /api/v1/webhook — message handler (port handle_message() function)
2. Create app/utils/security.py:
   - Port signature_required as a FastAPI Depends() dependency
   - Use HMAC SHA256 validation identical to reference
3. Create app/utils/whatsapp_utils.py:
   - Port is_valid_whatsapp_message() — extracts wa_id, name, message from payload
   - Port send_message() — use async httpx instead of sync requests
   - Port process_text_for_whatsapp() — markdown to WhatsApp formatting
   - Port get_text_message_input() — message payload builder
4. Test with ngrok:
   - Start FastAPI server
   - Start ngrok: `ngrok http 8000 --domain your-domain.ngrok-free.app`
   - Configure webhook URL in Meta Dashboard
   - Send a test message → should receive echo response

IMPORTANT: The reference uses `current_app.config[]` (Flask pattern).
Replace with dependency injection using Pydantic Settings.
The reference sends to RECIPIENT_WAID (hardcoded recipient).
Change to send reply to the actual sender's wa_id from the webhook payload.
```

### PROMPT 3: Claude AI Integration

```
Integrate Claude API as the conversational AI engine:

1. Create app/services/claude_service.py:
   - Initialize Anthropic client with API key from settings
   - Create function: generate_response(message: str, user_context: dict) -> str
   - System prompt should include:
     * Hello Desi personality: friendly, knowledgeable desi friend
     * Respond in the same language the user writes in (English/Hindi/Hinglish)
     * Always be helpful about Indian diaspora topics in the USA
     * For immigration/finance topics, add disclaimer: "This is general information only, not legal/financial advice"
   - Use Claude Haiku (claude-haiku-4-5-20251001) as default model
   - Support model escalation to Sonnet for complex queries

2. Update app/utils/whatsapp_utils.py:
   - Replace generate_response(message_body) with Claude-based generation
   - Process the Claude response through process_text_for_whatsapp()

3. Test: Send a WhatsApp message → receive intelligent AI response
```

### PROMPT 4: Supabase Database Setup

```
Set up the Supabase database with all required tables:

1. Create data/schemas/001_create_users.sql:
   CREATE TABLE users (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     wa_id TEXT UNIQUE NOT NULL,
     name TEXT,
     city TEXT,
     language TEXT DEFAULT 'en',
     interests TEXT[],
     is_premium BOOLEAN DEFAULT false,
     created_at TIMESTAMPTZ DEFAULT now(),
     updated_at TIMESTAMPTZ DEFAULT now()
   );

2. Create data/schemas/002_create_businesses.sql:
   CREATE TABLE businesses (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     name TEXT NOT NULL,
     category TEXT NOT NULL,
     subcategory TEXT,
     address TEXT,
     city TEXT NOT NULL,
     state TEXT,
     phone TEXT,
     rating DECIMAL(2,1),
     review_count INTEGER,
     latitude DECIMAL(10,7),
     longitude DECIMAL(10,7),
     source TEXT, -- 'google_places', 'eknazar', 'sulekha', 'user_submitted'
     source_id TEXT,
     is_featured BOOLEAN DEFAULT false,
     embedding vector(1536),
     created_at TIMESTAMPTZ DEFAULT now(),
     updated_at TIMESTAMPTZ DEFAULT now()
   );
   CREATE INDEX ON businesses USING ivfflat (embedding vector_cosine_ops);

3. Create data/schemas/003_create_events.sql:
   CREATE TABLE events (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     title TEXT NOT NULL,
     description TEXT,
     city TEXT NOT NULL,
     venue TEXT,
     event_date TIMESTAMPTZ,
     category TEXT, -- 'cultural', 'religious', 'social', 'professional'
     source TEXT,
     source_url TEXT,
     created_at TIMESTAMPTZ DEFAULT now()
   );

4. Create data/schemas/004_create_classifieds.sql:
   CREATE TABLE classifieds (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     user_id UUID REFERENCES users(id),
     type TEXT NOT NULL, -- 'roommate', 'sale', 'carpool', 'service', 'job'
     title TEXT NOT NULL,
     description TEXT,
     city TEXT NOT NULL,
     price DECIMAL(10,2),
     status TEXT DEFAULT 'active', -- 'active', 'closed', 'expired'
     created_at TIMESTAMPTZ DEFAULT now()
   );

5. Create data/schemas/005_create_conversations.sql:
   CREATE TABLE conversations (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     user_id UUID REFERENCES users(id),
     messages JSONB DEFAULT '[]'::jsonb,
     updated_at TIMESTAMPTZ DEFAULT now()
   );

6. Create app/services/user_service.py:
   - get_or_create_user(wa_id, name) → User
   - update_user_city(wa_id, city)
   - get_user_context(wa_id) → dict with city, language, interests

7. Run all migrations in Supabase Dashboard SQL editor
8. Enable pgvector extension: CREATE EXTENSION IF NOT EXISTS vector;
```

### PROMPT 5: User Onboarding Flow

```
Build the first-message onboarding experience:

1. Create app/services/onboarding_service.py:
   - Detect if user is new (no record in users table)
   - If new: Send welcome message:
     "🙏 Namaste! I'm Hello Desi — your AI desi friend in America.
      I can help you find Indian restaurants, groceries, temples,
      immigration info, and more!

      Which city are you in? (e.g., Dallas, Bay Area, NYC)"
   - Parse city from user's response
   - Store user profile in Supabase
   - If returning user: Load context and continue conversation

2. Update the main message handler to check onboarding status first
3. Test: New number messages bot → receives onboarding → city stored
```

### PROMPT 6: Intent Router

```
Build the intent classification system:

1. Create app/services/intent_router.py:
   - Define intents: DIRECTORY_SEARCH, COMMUNITY_EVENTS, IMMIGRATION,
     FINANCE, CLASSIFIEDS, GENERAL_CHAT, ONBOARDING
   - Implement keyword-based fast routing:
     * "restaurant|grocery|doctor|lawyer|temple|store" → DIRECTORY_SEARCH
     * "event|holi|diwali|festival|puja|garba" → COMMUNITY_EVENTS
     * "visa|h1b|green card|uscis|immigration|eb2|eb3" → IMMIGRATION
     * "remittance|send money|nre|nro|exchange rate" → FINANCE
     * "roommate|selling|carpool|sublet|looking for" → CLASSIFIEDS
   - If no keyword match, use Claude to classify intent (costs ~$0.001)
   - Return: intent, confidence, extracted_entities (city, category, etc.)

2. Update main message handler:
   message → intent_router → route to correct module handler → response

3. Test with sample messages for each intent
```

### PROMPT 7: Services Directory Module

```
Build the services directory search:

1. Create app/modules/directory/search.py:
   - search_businesses(query, city, category=None, limit=5) → list[Business]
   - Use pgvector semantic search for natural language queries
   - Fallback to keyword search if no vector match
   - Filter by city always

2. Create app/modules/directory/handler.py:
   - handle_directory_query(message, user_context) → str
   - Parse user's query to extract: what they're looking for + location
   - Search businesses
   - Format results for WhatsApp:
     "🏪 Found 3 Indian restaurants near Plano, TX:

      1. *Taj Mahal Restaurant* ⭐ 4.5
         📍 123 Main St, Plano TX
         📞 (972) 555-1234

      2. *Dosa Factory* ⭐ 4.3
         📍 456 Oak Ave, Plano TX
         📞 (972) 555-5678"

3. Create data/seeds/seed_google_places.py:
   - Script to bulk-pull Indian businesses from Google Places API
   - Categories: restaurant, grocery_store, hindu_temple, doctor, lawyer
   - Target city configurable
   - Generate embeddings for each business description
   - Insert into Supabase

4. Seed one city with 200+ businesses
5. Test end-to-end: WhatsApp message → directory search → formatted results
```

### PROMPT 8: RAG Pipeline (Firecrawl + pgvector)

```
Build the RAG pipeline for knowledge-grounded responses:

1. Create app/services/rag_service.py:
   - crawl_and_index(url, category) → indexes content into pgvector
   - search_knowledge(query, category, limit=3) → relevant chunks
   - Uses Firecrawl API to convert webpages to clean markdown
   - Chunks markdown into 500-token segments
   - Generates embeddings (use Anthropic or OpenAI embeddings API)
   - Stores in Supabase with vector embedding

2. Create data/seeds/seed_immigration_faq.py:
   - Crawl USCIS visa bulletin: https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html
   - Crawl USCIS processing times
   - Crawl common immigration FAQ pages
   - Index all into pgvector

3. Update Claude service to inject RAG context:
   - Before generating response, search knowledge base for relevant chunks
   - Include top 3 chunks in Claude's system prompt as context
   - Claude answers based on retrieved context (reduces hallucination)

4. Test: "What's the current EB-2 India processing time?" → grounded answer
```

### PROMPT 9: Community & Events Module

```
Build the community events feature:

1. Create app/modules/community/handler.py:
   - handle_community_query(message, user_context) → str
   - Parse: event type, date range, city
   - Search events table
   - Format for WhatsApp with date, venue, description

2. Create data/seeds/seed_events.py:
   - Firecrawl crawl of Eventbrite ("Indian events in [city]")
   - Zerowork extraction from Deshvidesh events page
   - Parse event details into structured data
   - Insert into Supabase events table

3. Build weekly digest:
   - For subscribed users, compile weekend events in their city
   - Send proactive WhatsApp message on Thursday evening
```

### PROMPT 10: Immigration Module

```
Build the immigration information module:

1. Create app/modules/immigration/handler.py:
   - handle_immigration_query(message, user_context) → str
   - Use RAG to retrieve relevant immigration content
   - Always append disclaimer:
     "⚠️ This is general information only, not legal advice.
      Consult an immigration attorney for your specific case."
   - For questions about specific case status, provide USCIS link
   - Escalate to Claude Sonnet for complex immigration questions

2. Create scheduled task: weekly USCIS data refresh via Firecrawl

3. Test with common questions:
   - "What's EB-2 India processing time?"
   - "Can I travel on AP while I-485 is pending?"
   - "H-1B lottery results kab aayenge?"
```

### PROMPT 11: Conversation Memory

```
Implement persistent conversation context:

1. Update app/services/user_service.py:
   - get_conversation_history(wa_id, limit=10) → list of messages
   - save_message(wa_id, role, content) → stores in conversations table
   - Sliding window: keep last 10 messages, oldest gets dropped

2. Update Claude service:
   - Include conversation history in Claude messages array
   - User can reference previous messages naturally:
     "What about the second restaurant you mentioned?"
   - Context carries across sessions

3. Test multi-turn conversations
```

### PROMPT 12: Rate Limiting & Cost Control

```
Implement rate limiting and cost controls:

1. Create app/utils/rate_limiter.py:
   - Using Redis (Upstash): track messages per user per day
   - Free tier: 20 messages/day
   - Premium tier: unlimited
   - When limit reached: "You've reached your daily message limit (20).
     Upgrade to premium for unlimited access! Reply PREMIUM for details."

2. Implement response caching:
   - Cache popular queries in Redis (TTL: 1 hour)
   - "Best Indian restaurants in Bay Area" → cached response
   - Cache key: hash(intent + city + normalized_query)

3. Intent router optimization:
   - Simple lookups (business hours, addresses) → cached, no LLM call
   - Medium queries → Claude Haiku
   - Complex queries (immigration law, financial planning) → Claude Sonnet

4. Set up budget alerts on Anthropic dashboard
```

### PROMPT 13: Financial Services Module

```
Build the financial services module:

1. Create app/modules/finance/handler.py:
   - handle_finance_query(message, user_context) → str
   - Remittance rate comparison (Firecrawl scrape of Wise/Remitly)
   - USD-INR exchange rate (Open Exchange Rates API)
   - NRE/NRO FAQ from RAG knowledge base
   - Always append: "⚠️ This is general information, not financial advice."

2. Rate alert subscription:
   - "Alert me when USD/INR crosses 87"
   - Store threshold in user preferences
   - Scheduled check (daily) → send WhatsApp alert when triggered
```

### PROMPT 14: Classifieds Module

```
Build the classifieds posting and browsing system:

1. Create app/modules/classifieds/handler.py:
   - Post flow: Parse natural language → extract type, title, price, city
     "I want to sell my sofa in Plano TX, $200"
     → Creates classified: { type: 'sale', title: 'Sofa', price: 200, city: 'Plano TX' }
   - Browse flow: "Show roommate listings in Sunnyvale"
     → Searches classifieds table by type + city
   - Format results for WhatsApp

2. Basic moderation:
   - Content filtering for spam/inappropriate posts
   - Auto-expire after 30 days
```

### PROMPT 15: Deployment & Production

```
Prepare for production deployment:

1. Create Dockerfile optimized for Railway
2. Set up Railway project with environment variables
3. Configure permanent webhook URL (Railway gives stable domain)
4. Set up error monitoring (Sentry or similar)
5. Set up logging (structured JSON logs)
6. Health check endpoint: GET /health
7. Deploy and verify webhook connection with Meta
8. Test full flow in production environment
```

### PROMPT 16: Data Seeding Pipeline

```
Execute the full data seeding pipeline:

1. Run Google Places API seeding for target metros:
   - Bay Area, DFW, NYC/NJ, Chicago, Houston
   - Categories: Indian restaurants, groceries, temples, doctors, lawyers, CPAs
   - 200+ businesses per city

2. Set up Zerowork TaskBots:
   - Eknazar: scrape business directory + classifieds for each city
   - Deshvidesh: scrape events listings

3. Set up Activepieces workflows:
   - Watch Google Sheets for new Zerowork/Hexofy data
   - Clean, deduplicate, categorize
   - Push to Supabase

4. Hexofy manual curation:
   - Sulekha: 50 high-value professionals per city (lawyers, CPAs, doctors)

5. Set up Apify for Facebook group posts:
   - Target 20-30 diaspora groups
   - Classify posts as events, classifieds, or noise

6. Firecrawl RAG indexing:
   - USCIS visa bulletin and processing times
   - Common immigration FAQ pages
   - NRE/NRO banking FAQ pages
```

---

## API Keys & Setup Checklist

### Step-by-Step Instructions for Anu

#### 1. Meta Developer Account & WhatsApp Business App
1. Go to https://developers.facebook.com/ and create/log into your Meta Developer account
2. Click "Create App" → Select "Other" → "Business" → Give it a name like "Hello Desi"
3. In the app dashboard, click "Add Product" → find "WhatsApp" → click "Set Up"
4. Go to "API Setup" in the WhatsApp section:
   - Note your **Phone Number ID** (under "From" field)
   - Note the **WhatsApp Business Account ID**
5. Under "Quickstart", you'll see a test phone number — this is your bot's number for testing
6. Add your personal WhatsApp number as a test recipient
7. Copy the **temporary access token** (expires in 24 hours — you'll create a permanent one next)

#### 2. Permanent System User Access Token
1. Go to https://business.facebook.com/settings/system-users
2. Click "Add" to create a new system user (name it "Hello Desi Bot")
3. Set role to "Admin"
4. Click "Add Assets" → select your WhatsApp app → grant "Full Control" → Save
5. Click "Generate New Token" → select your app
6. Choose "Never expire" for token duration
7. Select ALL permissions (whatsapp_business_management, whatsapp_business_messaging, etc.)
8. Click "Generate Token" → **COPY AND SAVE THIS TOKEN SECURELY**
9. This is your `ACCESS_TOKEN` env var

#### 3. App Secret
1. In Meta Developer Dashboard → Your App → Settings → Basic
2. Copy the **App Secret** (you may need to click "Show")
3. This is your `APP_SECRET` env var

#### 4. Webhook Verify Token
1. Choose any random string (e.g., "hellodesi_verify_2026")
2. You'll enter this same string when configuring the webhook in Meta Dashboard
3. This is your `VERIFY_TOKEN` env var

#### 5. Phone Number for Production (Later)
- For MVP, use Meta's test number
- For production: buy a virtual number from services like Twilio, or get a new SIM card
- The number must NOT be currently registered on WhatsApp
- You'll need to verify it via SMS/voice call during setup

#### 6. Anthropic API Key (Claude)
1. Go to https://console.anthropic.com/
2. Create an account or log in
3. Go to "API Keys" in the sidebar
4. Click "Create Key" → name it "Hello Desi"
5. **COPY AND SAVE THE KEY** (shown only once)
6. Add credits: Go to "Billing" → add $20-50 to start
7. This is your `ANTHROPIC_API_KEY` env var

#### 7. Supabase Project
1. Go to https://supabase.com/ and create an account
2. Click "New Project"
3. Name: "hello-desi", Database Password: (choose a strong one, save it)
4. Region: Choose closest to your target users (e.g., US East)
5. Wait for project to initialize (~2 minutes)
6. Go to Settings → API:
   - Copy **Project URL** → this is your `SUPABASE_URL`
   - Copy **anon/public key** → this is your `SUPABASE_KEY`
7. Go to SQL Editor → run: `CREATE EXTENSION IF NOT EXISTS vector;`
8. Run each migration SQL file from data/schemas/ in order

#### 8. Upstash Redis
1. Go to https://upstash.com/ and create an account
2. Click "Create Database"
3. Name: "hello-desi", Region: US East
4. Copy the **Redis URL** (starts with `rediss://...`)
5. This is your `REDIS_URL` env var

#### 9. Railway Account
1. Go to https://railway.app/ and sign up (GitHub login recommended)
2. Click "New Project" → "Deploy from GitHub Repo"
3. Connect your Hello Desi repository
4. Add all environment variables in the Railway dashboard
5. Railway provides a permanent URL (e.g., hello-desi-production.up.railway.app)

#### 10. ngrok (Local Development)
1. Go to https://ngrok.com/ and create a free account
2. Download and install ngrok
3. Authenticate: `ngrok config add-authtoken YOUR_TOKEN`
4. Get a free static domain: Dashboard → Cloud Edge → Domains → "+ Create Domain"
5. Run: `ngrok http 8000 --domain your-domain.ngrok-free.app`
6. Use this URL + "/api/v1/webhook" as your webhook URL in Meta Dashboard

#### 11. Google Maps API Key
1. Go to https://console.cloud.google.com/
2. Create a new project "Hello Desi"
3. Enable "Places API" (search in API Library)
4. Go to Credentials → Create Credentials → API Key
5. Restrict the key: API restrictions → select "Places API" only
6. This is your `GOOGLE_MAPS_API_KEY` env var
7. Set up billing (required for Places API, but first $200/mo is free)

#### 12. Firecrawl API Key
1. Go to https://firecrawl.dev/ and create an account
2. Get your API key from the dashboard
3. Free tier: 500 pages/month
4. This is your `FIRECRAWL_API_KEY` env var

#### 13. Apify Account
1. Go to https://apify.com/ and create an account
2. Free tier gives $5/month in credits
3. Find "Facebook Groups Scraper" in the Store
4. Get your API token from Settings → Integrations

#### 14. Zerowork Account
1. Go to https://zerowork.io/ and create an account
2. Download the desktop agent (Windows/Mac/Linux)
3. Starter plan: $15/month (20 TaskBots)
4. Sign in to the desktop agent

#### 15. Hexofy
1. Go to https://hexofy.com/ and install the Chrome extension
2. Subscribe: $12/month
3. Sign in to the extension

#### 16. Open Exchange Rates (USD-INR)
1. Go to https://openexchangerates.org/ and create a free account
2. Free tier: 1,000 requests/month
3. Copy your App ID from the dashboard

---

## Rules for Every Session

1. **Always read context.md first** — it has all finalized decisions
2. **Always update implementation_plan.md** — mark steps complete as you go
3. **Never change finalized tech decisions** without Anu's explicit approval
4. **Use the reference project** (`python-whatsapp-bot-main/`) for WhatsApp patterns
5. **Test each component** before moving to the next step
6. **Log all errors** in implementation_plan.md
7. **Cost-conscious**: Always use Haiku first, only escalate to Sonnet when needed
8. **Security**: Never commit .env files. Never log access tokens. Always validate webhook signatures.
9. **Legal disclaimers**: Every immigration and finance response MUST include a disclaimer
10. **Multilingual**: Respond in the language the user writes in (English/Hindi/Hinglish)
