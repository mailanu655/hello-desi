const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
        ShadingType, PageNumber, PageBreak, LevelFormat, TabStopType, TabStopPosition } = require('docx');
const fs = require('fs');

const ACCENT = "1A6B3C";    // Mira green
const DARK   = "1B1B1B";
const GRAY   = "555555";
const LIGHT  = "E8F5E9";
const WHITE  = "FFFFFF";
const BORDER = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const BORDERS = { top: BORDER, bottom: BORDER, left: BORDER, right: BORDER };
const CELL_MARGINS = { top: 80, bottom: 80, left: 120, right: 120 };

const PAGE_WIDTH = 12240;
const MARGIN = 1440;
const CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN; // 9360

function heading(text, level = HeadingLevel.HEADING_1) {
  return new Paragraph({ heading: level, spacing: { before: 300, after: 150 },
    children: [new TextRun({ text, bold: true, font: "Arial", size: level === HeadingLevel.HEADING_1 ? 32 : level === HeadingLevel.HEADING_2 ? 26 : 22, color: ACCENT })] });
}

function para(text, opts = {}) {
  return new Paragraph({ spacing: { after: 120 }, ...opts,
    children: [new TextRun({ text, font: "Arial", size: 22, color: DARK, ...opts.run })] });
}

function boldPara(label, value) {
  return new Paragraph({ spacing: { after: 100 },
    children: [
      new TextRun({ text: label, bold: true, font: "Arial", size: 22, color: DARK }),
      new TextRun({ text: value, font: "Arial", size: 22, color: GRAY }),
    ] });
}

function statusBadge(status) {
  const map = { "DONE": "2E7D32", "IN PROGRESS": "F57F17", "PENDING": "C62828", "PARTIAL": "E65100" };
  return new TextRun({ text: ` [${status}]`, bold: true, font: "Arial", size: 20, color: map[status] || GRAY });
}

function featureRow(feature, status, desc) {
  const statusColor = { "DONE": "E8F5E9", "IN PROGRESS": "FFF8E1", "PENDING": "FFEBEE", "PARTIAL": "FFF3E0" };
  return new TableRow({ children: [
    new TableCell({ borders: BORDERS, margins: CELL_MARGINS, width: { size: 2400, type: WidthType.DXA },
      shading: { fill: WHITE, type: ShadingType.CLEAR },
      children: [new Paragraph({ children: [new TextRun({ text: feature, bold: true, font: "Arial", size: 20, color: DARK })] })] }),
    new TableCell({ borders: BORDERS, margins: CELL_MARGINS, width: { size: 1200, type: WidthType.DXA },
      shading: { fill: statusColor[status] || WHITE, type: ShadingType.CLEAR },
      children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: status, bold: true, font: "Arial", size: 20, color: DARK })] })] }),
    new TableCell({ borders: BORDERS, margins: CELL_MARGINS, width: { size: 5760, type: WidthType.DXA },
      shading: { fill: WHITE, type: ShadingType.CLEAR },
      children: [new Paragraph({ children: [new TextRun({ text: desc, font: "Arial", size: 20, color: GRAY })] })] }),
  ]});
}

function headerRow(cols, widths) {
  return new TableRow({ children: cols.map((c, i) =>
    new TableCell({ borders: BORDERS, margins: CELL_MARGINS, width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: ACCENT, type: ShadingType.CLEAR },
      children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: c, bold: true, font: "Arial", size: 20, color: WHITE })] })] })
  )});
}

function simpleRow(cells, widths) {
  return new TableRow({ children: cells.map((c, i) =>
    new TableCell({ borders: BORDERS, margins: CELL_MARGINS, width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: WHITE, type: ShadingType.CLEAR },
      children: [new Paragraph({ children: [new TextRun({ text: c, font: "Arial", size: 20, color: DARK })] })] })
  )});
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 300, after: 150 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 2 } },
    ]
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ]
  },
  sections: [
    // ── COVER PAGE ──
    {
      properties: {
        page: { size: { width: PAGE_WIDTH, height: 15840 }, margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN } }
      },
      children: [
        new Paragraph({ spacing: { before: 3000 } }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
          children: [new TextRun({ text: "MIRA", font: "Arial", size: 72, bold: true, color: ACCENT })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
          children: [new TextRun({ text: "Your smart desi friend on WhatsApp", font: "Arial", size: 28, italics: true, color: GRAY })] }),
        new Paragraph({ spacing: { before: 600 }, alignment: AlignmentType.CENTER, border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 1 } },
          children: [] }),
        new Paragraph({ spacing: { before: 400 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Project Status Report", font: "Arial", size: 36, bold: true, color: DARK })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
          children: [new TextRun({ text: "March 29, 2026", font: "Arial", size: 24, color: GRAY })] }),
        new Paragraph({ spacing: { before: 800 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Prepared by: Anu Marella", font: "Arial", size: 22, color: GRAY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "adeep.gt@gmail.com", font: "Arial", size: 22, color: GRAY })] }),
      ]
    },

    // ── MAIN CONTENT ──
    {
      properties: {
        page: { size: { width: PAGE_WIDTH, height: 15840 }, margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN } }
      },
      headers: {
        default: new Header({ children: [new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          children: [
            new TextRun({ text: "Mira \u2014 Project Status", font: "Arial", size: 18, color: ACCENT, bold: true }),
            new TextRun({ text: "\tMarch 29, 2026", font: "Arial", size: 18, color: GRAY }),
          ] })] })
      },
      footers: {
        default: new Footer({ children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Page ", font: "Arial", size: 18, color: GRAY }), new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: GRAY })] })] })
      },
      children: [
        // ── 1. EXECUTIVE SUMMARY ──
        heading("1. Executive Summary"),
        para("Mira is an AI-powered WhatsApp bot that helps the Indian diaspora in the USA find local desi businesses, groceries, tiffin services, babysitters, deals, and more. Built on FastAPI with Claude AI (Anthropic) as the intelligence layer, Supabase (PostgreSQL) for data, and deployed on Render.com."),
        para("The platform has been fully rebranded from \"Hello Desi\" to \"Mira\" with a complete brand identity overhaul including personality, voice, signature phrases, and visual tone applied across every user-facing message."),

        // ── 2. INFRASTRUCTURE ──
        heading("2. Infrastructure & Tech Stack"),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [3000, 6360], rows: [
          headerRow(["Component", "Details"], [3000, 6360]),
          simpleRow(["Backend", "FastAPI (Python 3.11) with async support"], [3000, 6360]),
          simpleRow(["AI Engine", "Claude Haiku (90% queries) + Sonnet (complex: immigration, finance)"], [3000, 6360]),
          simpleRow(["Database", "Supabase PostgreSQL \u2014 9 tables, 4,312+ business listings"], [3000, 6360]),
          simpleRow(["Hosting", "Render.com (Free tier, Docker deploy, ~50s cold start)"], [3000, 6360]),
          simpleRow(["WhatsApp", "Meta Cloud API via WhatsApp Business Platform"], [3000, 6360]),
          simpleRow(["Payments", "Stripe Payment Links (env var based, not yet configured)"], [3000, 6360]),
          simpleRow(["Scheduling", "Claude Desktop scheduled-tasks MCP (daily digest + weekly proof)"], [3000, 6360]),
          simpleRow(["Repo", "github.com/mailanu655/hello-desi (main branch)"], [3000, 6360]),
          simpleRow(["Live URL", "https://hello-desi.onrender.com"], [3000, 6360]),
        ]}),

        // ── 3. DATABASE ──
        heading("3. Database Schema"),
        para("Supabase project ID: atprqakojjlclwviaaii"),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [2200, 2200, 4960], rows: [
          headerRow(["Table", "Rows (approx)", "Purpose"], [2200, 2200, 4960]),
          simpleRow(["businesses", "4,312+", "Business directory \u2014 name, category, city, state, phone, is_featured, source_id"], [2200, 2200, 4960]),
          simpleRow(["deals", "Variable", "Time-limited promotions posted by businesses"], [2200, 2200, 4960]),
          simpleRow(["inquiry_logs", "Growing", "Every business view/search logged with user, type, city"], [2200, 2200, 4960]),
          simpleRow(["subscriptions", "Growing", "Business subscription tiers (free/featured/premium)"], [2200, 2200, 4960]),
          simpleRow(["digest_subscribers", "Growing", "Opt-in daily digest subscribers by city"], [2200, 2200, 4960]),
          simpleRow(["events", "\u2014", "Community event listings (schema ready)"], [2200, 2200, 4960]),
          simpleRow(["classifieds", "\u2014", "Buy/sell marketplace (schema ready)"], [2200, 2200, 4960]),
          simpleRow(["conversations", "Growing", "Message history for context"], [2200, 2200, 4960]),
          simpleRow(["users", "Growing", "User profiles and preferences"], [2200, 2200, 4960]),
        ]}),

        // ── 4. API ENDPOINTS ──
        heading("4. API Endpoints"),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [1200, 4160, 4000], rows: [
          headerRow(["Method", "Endpoint", "Description"], [1200, 4160, 4000]),
          simpleRow(["POST", "/api/v1/webhook", "WhatsApp webhook \u2014 receives and processes all messages"], [1200, 4160, 4000]),
          simpleRow(["GET", "/api/v1/webhook", "Meta verification challenge for webhook setup"], [1200, 4160, 4000]),
          simpleRow(["POST", "/api/v1/tasks/proof-messages", "Cron trigger: weekly business proof messages"], [1200, 4160, 4000]),
          simpleRow(["POST", "/api/v1/tasks/digest", "Cron trigger: daily metro digest"], [1200, 4160, 4000]),
          simpleRow(["GET", "/api/v1/tasks/analytics", "Live dashboard: business count, inquiries, subscribers"], [1200, 4160, 4000]),
          simpleRow(["GET", "/health", "Health check (returns service: mira)"], [1200, 4160, 4000]),
        ]}),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 5. FEATURE STATUS ──
        heading("5. Feature Status Overview"),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [2400, 1200, 5760], rows: [
          headerRow(["Feature", "Status", "Details"], [2400, 1200, 5760]),
          featureRow("Business Directory", "DONE", "4,312+ listings across 49 states. Search by category, city, name. AI-powered natural language queries."),
          featureRow("Business Registration", "DONE", "Multi-step WhatsApp flow: name \u2192 category \u2192 city \u2192 phone \u2192 confirm. Mira voice: \"Got you \ud83d\udc4d Let's get you listed!\""),
          featureRow("Deals & Promotions", "DONE", "Businesses post time-limited deals via WhatsApp. City-based browsing. Mira voice confirmation."),
          featureRow("AI Chat (Claude)", "DONE", "Full system prompt with Mira personality. Haiku for fast queries, Sonnet for complex topics. Signature phrases integrated."),
          featureRow("Monetization Engine", "DONE", "3-tier plans (Free/$15/$30). Upgrade flow, plan status, business analytics. Stripe payment link env vars wired."),
          featureRow("Inquiry Tracking", "DONE", "Every business search logged to inquiry_logs. Powers proof messages and analytics."),
          featureRow("Lead Notifications", "DONE", "Instant WhatsApp alert to business owners when searched. Rate-limited 1/hour/business. Fire-and-forget async."),
          featureRow("Weekly Proof Messages", "DONE", "Monday 10am \u2014 sends business owners their inquiry count + trend. Three message variants. Upgrade CTA for free tier."),
          featureRow("Daily Metro Digest", "DONE", "8am daily \u2014 opt-in city digest with new businesses, deals, featured sponsors. Subscribe/unsubscribe via WhatsApp."),
          featureRow("First-Time Onboarding", "DONE", "In-memory seen_users set. Greetings trigger welcome message showing what Mira can do."),
          featureRow("Analytics Endpoint", "DONE", "GET /tasks/analytics \u2014 live counts for businesses, inquiries (today/week/all), subscriptions, digest subs, deals."),
          featureRow("Mira Brand Voice", "DONE", "Complete rebrand across 7 files: welcome, system prompt, digest, proof, registration, deals, monetization."),
          featureRow("Cron Scheduling", "DONE", "Claude Desktop scheduled-tasks MCP configured. IDs: hello-desi-daily-digest, hello-desi-weekly-proof."),
          featureRow("Stripe Integration", "PARTIAL", "Env vars (STRIPE_FEATURED_LINK, STRIPE_PREMIUM_LINK) wired in code. Payment links NOT yet created in Stripe dashboard or set in Render."),
          featureRow("Render Cron Jobs", "PENDING", "Currently using Claude Desktop MCP. Need Render cron or external scheduler for production reliability."),
          featureRow("Lead Classification", "PENDING", "No hot/warm/cold scoring. All inquiries treated equally. Need intent-based classification."),
          featureRow("Favorites & Saved Lists", "PENDING", "Users can't save preferred businesses for quick access."),
          featureRow("Event Listings", "PENDING", "Schema exists. No WhatsApp flow or browsing built yet."),
          featureRow("Classifieds/Marketplace", "PENDING", "Schema exists. No WhatsApp flow built yet."),
          featureRow("City Auto-Detection", "PENDING", "Users must specify city. Could auto-detect from phone area code or first message."),
          featureRow("User Acquisition", "PENDING", "Zero real users. Bot not shared in any WhatsApp groups yet."),
        ]}),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 6. CODEBASE ──
        heading("6. Codebase Structure"),
        heading("6.1 Core Services", HeadingLevel.HEADING_2),

        heading("webhook.py (app/api/)", HeadingLevel.HEADING_3),
        para("Main WhatsApp message handler. Routes incoming messages through intent detection, session management, and AI response. Includes first-time onboarding with in-memory seen_users set, digest subscribe/unsubscribe flow, monetization session handling, and weekly report command."),

        heading("claude_service.py", HeadingLevel.HEADING_3),
        para("AI engine with complete Mira personality system prompt. Dual-model routing: Haiku for 90% of queries (fast, cheap), Sonnet for complex topics (immigration, finance, legal). Signature phrases: \"Got you \ud83d\udc4d\", \"Here are some good options \ud83d\udc47\", \"Want more like this?\". Enforces short WhatsApp-style messages."),

        heading("monetization_service.py", HeadingLevel.HEADING_3),
        para("Full upgrade flow with in-memory session state (10-min timeout). Business lookup \u2192 plan selection \u2192 confirmation \u2192 Stripe link. Lead notifications with async fire-and-forget and 1-hour rate limiting per business. Stats and plan status commands."),

        heading("proof_message_service.py", HeadingLevel.HEADING_3),
        para("Weekly proof messages to business owners. Three variants: active (with inquiry count + trend), slowing down, and brand new. Business-facing tone: \"Hi! This is Mira \ud83d\udc4b\". Upgrade CTA for free-tier businesses."),

        heading("digest_service.py", HeadingLevel.HEADING_3),
        para("Daily metro digest system. City-based content curation: new businesses, active deals, featured sponsors. Subscribe/unsubscribe via WhatsApp. Format: \"\ud83c\udf06 {city} with Mira\". Digest cached per city to avoid duplicate builds."),

        heading("business_registration.py", HeadingLevel.HEADING_3),
        para("Multi-step WhatsApp conversation flow for adding businesses. Steps: name \u2192 category \u2192 city/state \u2192 phone \u2192 confirmation. Stores to Supabase with source_id linking owner."),

        heading("deals_service.py", HeadingLevel.HEADING_3),
        para("Deal posting and browsing. Businesses create time-limited promotions. Users browse deals by city. Confirmation in Mira voice."),

        heading("business_service.py", HeadingLevel.HEADING_3),
        para("Core business lookup service. Searches Supabase by category, city, name. Returns formatted WhatsApp results with featured businesses prioritized."),

        heading("intent_router.py", HeadingLevel.HEADING_3),
        para("Message classification layer. Detects user intent (search, add business, deals, monetization, digest) and routes to appropriate service handler."),

        heading("tasks.py (app/api/)", HeadingLevel.HEADING_3),
        para("Cron-callable API endpoints with secret-based auth. Endpoints for proof messages, daily digest, and analytics dashboard."),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 7. BRAND IDENTITY ──
        heading("7. Mira Brand Identity"),
        boldPara("Positioning: ", "\"Your smart desi friend on WhatsApp\""),
        boldPara("Personality: ", "Friendly, helpful, slightly desi, clear & quick"),
        boldPara("Tone: ", "Short WhatsApp messages, light emoji use, always gives options"),

        heading("7.1 Signature Phrases", HeadingLevel.HEADING_2),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "\"Got you \ud83d\udc4d\" \u2014 acknowledgment", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "\"Here are some good options \ud83d\udc47\" \u2014 search results", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "\"Want more like this?\" \u2014 engagement", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "\"Try this \ud83d\udc49 ...\" \u2014 suggestion", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 120 }, children: [new TextRun({ text: "\"Found something useful? Share with your group \ud83d\ude4c\" \u2014 growth hook", font: "Arial", size: 22 })] }),

        heading("7.2 Voice Applied Across", HeadingLevel.HEADING_2),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [3000, 6360], rows: [
          headerRow(["Touchpoint", "Mira Voice Example"], [3000, 6360]),
          simpleRow(["Welcome Message", "\"Hi {name}! I'm Mira \ud83d\ude0a I can help you find: groceries, food, babysitters, deals...\""], [3000, 6360]),
          simpleRow(["Business Registration", "\"Got you \ud83d\udc4d Let's get you listed!\" \u2192 \"{name} is now listed! \ud83c\udf89\""], [3000, 6360]),
          simpleRow(["Deal Confirmation", "\"Your deal is live! \ud83c\udf89 People in your area will see this \ud83d\udc4d\""], [3000, 6360]),
          simpleRow(["Upgrade Flow", "\"Got you \ud83d\udc4d Let's get you upgraded!\" \u2192 concise plan cards"], [3000, 6360]),
          simpleRow(["Daily Digest", "\"\ud83c\udf06 Columbus with Mira\" \u2014 clean sections for groceries, deals, sponsored"], [3000, 6360]),
          simpleRow(["Proof Messages", "\"Hi! This is Mira \ud83d\udc4b This week for {business}: X people searched...\""], [3000, 6360]),
          simpleRow(["Lead Notification", "\"\ud83d\udd14 New customer interest! Someone searched for... Your business was shown \ud83d\udc4d\""], [3000, 6360]),
        ]}),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 8. COMMIT HISTORY ──
        heading("8. Commit History"),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [1500, 7860], rows: [
          headerRow(["Hash", "Description"], [1500, 7860]),
          simpleRow(["a15ddba", "feat: apply Mira brand voice across all user-facing messages"], [1500, 7860]),
          simpleRow(["e709eee", "rebrand: Hello Desi \u2192 Mira (file names, config, health check)"], [1500, 7860]),
          simpleRow(["9d0f37c", "feat: launch-ready \u2014 lead notifications, onboarding, analytics, Stripe env vars"], [1500, 7860]),
          simpleRow(["1977e26", "feat: add weekly proof messages + daily metro digest"], [1500, 7860]),
          simpleRow(["2ff53db", "Add monetization: featured listings, inquiry tracking, subscription plans"], [1500, 7860]),
          simpleRow(["b370728", "Fix f-string backslash SyntaxError for Python 3.11 compatibility"], [1500, 7860]),
          simpleRow(["f017ad3", "Add Deals & Promotions feature \u2014 WhatsApp deal posting and browsing"], [1500, 7860]),
          simpleRow(["03d84e1", "feat: add business registration & update via WhatsApp chat"], [1500, 7860]),
          simpleRow(["422c611", "feat: add 4 new categories + Round 5 city hints \u2014 592 total businesses"], [1500, 7860]),
          simpleRow(["63d5665", "feat: add Round 4 city hints \u2014 25 new cities"], [1500, 7860]),
          simpleRow(["dd23f8d", "feat: expand business categories and city coverage"], [1500, 7860]),
          simpleRow(["f1c0dd2", "feat: add business lookup from Supabase + seed script"], [1500, 7860]),
          simpleRow(["082236e", "Enforce strict English-only responses"], [1500, 7860]),
          simpleRow(["c4c325e", "Initial commit: Hello Desi WhatsApp bot"], [1500, 7860]),
        ]}),

        new Paragraph({ children: [new PageBreak()] }),

        // ── 9. WHAT'S PENDING ──
        heading("9. Pending / Next Steps"),

        heading("9.1 Critical (Launch Blockers)", HeadingLevel.HEADING_2),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Get Real Users: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "Share Mira in Columbus-area WhatsApp groups (desi community, temple groups, apartment complexes). Zero real users currently.", font: "Arial", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Create Stripe Payment Links: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "Set up Featured ($15/mo) and Premium ($30/mo) payment links in Stripe dashboard. Add to Render env vars: STRIPE_FEATURED_LINK, STRIPE_PREMIUM_LINK.", font: "Arial", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Production Cron Jobs: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "Move from Claude Desktop MCP to Render cron jobs or external scheduler (cron-job.org) for reliable daily digest (8am) and weekly proof (Monday 10am).", font: "Arial", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Focus on Columbus: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "Go deep in one metro. Seed more Columbus businesses, target Columbus desi WhatsApp groups, and validate product-market fit before expanding.", font: "Arial", size: 22 }),
        ]}),

        heading("9.2 High Priority (Post-Launch)", HeadingLevel.HEADING_2),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Lead Classification: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "Score inquiries as hot/warm/cold based on message intent. \"I need a caterer for Saturday\" vs \"just browsing\" should trigger different business notifications.", font: "Arial", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "City Auto-Detection: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "Detect user city from phone area code or first message context. Eliminate the \"which city?\" friction.", font: "Arial", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Stripe Webhook Integration: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "Currently payment links are fire-and-forget. Need webhook to auto-activate plans on successful payment and handle expirations.", font: "Arial", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Persistent User State: ", bold: true, font: "Arial", size: 22 }),
          new TextRun({ text: "seen_users set and session state are in-memory \u2014 lost on every restart. Move to Supabase or Redis for durability.", font: "Arial", size: 22 }),
        ]}),

        heading("9.3 Future Features (Backlog)", HeadingLevel.HEADING_2),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "Favorites & Saved Lists \u2014 let users save preferred businesses", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "Event Listings \u2014 community events with RSVP via WhatsApp", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "Classifieds/Marketplace \u2014 buy/sell within community", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "Business Reviews \u2014 ratings and feedback loop", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: "Multi-language Support \u2014 Hindi, Telugu, Tamil message detection", font: "Arial", size: 22 })] }),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 120 }, children: [new TextRun({ text: "Referral Program \u2014 \"Share with 3 friends\" viral loop", font: "Arial", size: 22 })] }),

        // ── 10. REVENUE MODEL ──
        heading("10. Revenue Model"),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [2000, 1500, 5860], rows: [
          headerRow(["Plan", "Price", "Features"], [2000, 1500, 5860]),
          simpleRow(["Free", "$0/mo", "Basic listing, 1 deal/month, no analytics"], [2000, 1500, 5860]),
          simpleRow(["\u2b50 Featured", "$15/mo", "Featured badge (appear first), 5 deals/month, business analytics, inquiry stats"], [2000, 1500, 5860]),
          simpleRow(["\ud83d\udc51 Premium", "$30/mo", "All Featured perks + unlimited deals, priority placement, premium support"], [2000, 1500, 5860]),
        ]}),
        para("Revenue streams: Featured Listings (recurring), Lead Notifications (value proof \u2192 upgrade driver), Daily Digest Sponsorship (future), Pay-per-Inquiry (future)."),

        // ── 11. KEY IDENTIFIERS ──
        heading("11. Key Identifiers & Access"),
        new Table({ width: { size: CONTENT_WIDTH, type: WidthType.DXA }, columnWidths: [3200, 6160], rows: [
          headerRow(["Resource", "Value"], [3200, 6160]),
          simpleRow(["GitHub Repo", "github.com/mailanu655/hello-desi"], [3200, 6160]),
          simpleRow(["Render Service URL", "https://hello-desi.onrender.com"], [3200, 6160]),
          simpleRow(["Render Service ID", "srv-d73vdb7pm1nc738md8e0"], [3200, 6160]),
          simpleRow(["Supabase Project", "atprqakojjlclwviaaii"], [3200, 6160]),
          simpleRow(["WhatsApp Business Acct", "959327453324618"], [3200, 6160]),
          simpleRow(["Phone Number ID", "1045027728691211"], [3200, 6160]),
          simpleRow(["Latest Commit", "a15ddba (Mira brand voice)"], [3200, 6160]),
          simpleRow(["Scheduled Task: Digest", "hello-desi-daily-digest (8am daily)"], [3200, 6160]),
          simpleRow(["Scheduled Task: Proof", "hello-desi-weekly-proof (Monday 10am)"], [3200, 6160]),
        ]}),
      ]
    }
  ]
});

Packer.toBuffer(doc).then(buffer => {
  const outPath = "/sessions/youthful-exciting-archimedes/mnt/python-whatsapp-bot-main/Hello Desi/Mira_Project_Status.docx";
  fs.writeFileSync(outPath, buffer);
  console.log("Created: " + outPath);
});
