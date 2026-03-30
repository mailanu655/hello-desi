const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
        ShadingType, PageNumber, PageBreak, LevelFormat, TabStopType, TabStopPosition } = require('docx');
const fs = require('fs');

const ACCENT = "1A6B3C";
const DARK   = "1B1B1B";
const GRAY   = "555555";
const WHITE  = "FFFFFF";
const RED    = "C62828";
const AMBER  = "E65100";
const BORDER = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const BORDERS = { top: BORDER, bottom: BORDER, left: BORDER, right: BORDER };
const CELL_MARGINS = { top: 80, bottom: 80, left: 120, right: 120 };
const PAGE_WIDTH = 12240;
const MARGIN = 1440;
const CW = PAGE_WIDTH - 2 * MARGIN; // 9360

// ── Helpers ──
function h1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 300, after: 150 },
    children: [new TextRun({ text, bold: true, font: "Arial", size: 32, color: ACCENT })] });
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 },
    children: [new TextRun({ text, bold: true, font: "Arial", size: 26, color: ACCENT })] });
}
function h3(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_3, spacing: { before: 180, after: 100 },
    children: [new TextRun({ text, bold: true, font: "Arial", size: 22, color: ACCENT })] });
}
function p(text, opts = {}) {
  return new Paragraph({ spacing: { after: 120 }, ...opts,
    children: [new TextRun({ text, font: "Arial", size: 22, color: DARK, ...opts.run })] });
}
function bp(label, value) {
  return new Paragraph({ spacing: { after: 100 },
    children: [
      new TextRun({ text: label, bold: true, font: "Arial", size: 22, color: DARK }),
      new TextRun({ text: value, font: "Arial", size: 22, color: GRAY }),
    ] });
}
function bullet(text) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 },
    children: [new TextRun({ text, font: "Arial", size: 22 })] });
}
function numberedItem(label, value) {
  return new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 },
    children: [
      new TextRun({ text: label, bold: true, font: "Arial", size: 22 }),
      new TextRun({ text: value, font: "Arial", size: 22 }),
    ] });
}
function calloutBox(text, color = RED) {
  return new Paragraph({ spacing: { before: 120, after: 120 },
    border: { left: { style: BorderStyle.SINGLE, size: 12, color, space: 8 } },
    indent: { left: 200 },
    children: [new TextRun({ text, font: "Arial", size: 22, color: DARK, italics: true })] });
}

function hdr(cols, widths) {
  return new TableRow({ children: cols.map((c, i) =>
    new TableCell({ borders: BORDERS, margins: CELL_MARGINS, width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: ACCENT, type: ShadingType.CLEAR },
      children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: c, bold: true, font: "Arial", size: 20, color: WHITE })] })] })
  )});
}
function row(cells, widths, opts = {}) {
  return new TableRow({ children: cells.map((c, i) =>
    new TableCell({ borders: BORDERS, margins: CELL_MARGINS, width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: opts.fills ? opts.fills[i] : WHITE, type: ShadingType.CLEAR },
      children: [new Paragraph({ children: Array.isArray(c)
        ? c
        : [new TextRun({ text: c, font: "Arial", size: 20, color: DARK, ...(opts.runOpts && opts.runOpts[i] || {}) })]
      })] })
  )});
}
function featureRow(feature, status, desc) {
  const sc = { "DONE": "E8F5E9", "PARTIAL": "FFF3E0", "PENDING": "FFEBEE", "NOT ENFORCED": "FFF3E0" };
  return row([feature, status, desc], [2200, 1200, 5960], {
    fills: [WHITE, sc[status] || WHITE, WHITE],
    runOpts: [{ bold: true }, { bold: true }, {}]
  });
}
function tbl(colWidths, rows) {
  return new Table({ width: { size: CW, type: WidthType.DXA }, columnWidths: colWidths, rows });
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
      { reference: "numbers2", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers3", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers4", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ]
  },
  sections: [
    // ═══════════════ COVER PAGE ═══════════════
    {
      properties: { page: { size: { width: PAGE_WIDTH, height: 15840 }, margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN } } },
      children: [
        new Paragraph({ spacing: { before: 3000 } }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
          children: [new TextRun({ text: "MIRA", font: "Arial", size: 72, bold: true, color: ACCENT })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
          children: [new TextRun({ text: "Your smart desi friend on WhatsApp", font: "Arial", size: 28, italics: true, color: GRAY })] }),
        new Paragraph({ spacing: { before: 600 }, alignment: AlignmentType.CENTER,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 1 } }, children: [] }),
        new Paragraph({ spacing: { before: 400 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Project Status Report", font: "Arial", size: 36, bold: true, color: DARK })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
          children: [new TextRun({ text: "Version 2.0 \u2014 March 29, 2026", font: "Arial", size: 24, color: GRAY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
          children: [new TextRun({ text: "INTERNAL ONLY \u2014 Contains infrastructure identifiers", font: "Arial", size: 20, bold: true, color: RED })] }),
        new Paragraph({ spacing: { before: 800 }, alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Prepared by: Anu Marella", font: "Arial", size: 22, color: GRAY })] }),
        new Paragraph({ alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "adeep.gt@gmail.com", font: "Arial", size: 22, color: GRAY })] }),
      ]
    },

    // ═══════════════ MAIN CONTENT ═══════════════
    {
      properties: { page: { size: { width: PAGE_WIDTH, height: 15840 }, margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN } } },
      headers: {
        default: new Header({ children: [new Paragraph({
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
          children: [
            new TextRun({ text: "Mira \u2014 Project Status (Internal)", font: "Arial", size: 18, color: ACCENT, bold: true }),
            new TextRun({ text: "\tv2.0 \u2014 March 29, 2026", font: "Arial", size: 18, color: GRAY }),
          ] })] })
      },
      footers: {
        default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Page ", font: "Arial", size: 18, color: GRAY }), new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: GRAY })] })] })
      },
      children: [

// ── 1. EXECUTIVE SUMMARY ──
h1("1. Executive Summary"),
p("Mira is an AI-powered WhatsApp bot helping the Indian diaspora in the USA find local desi businesses, groceries, tiffin services, babysitters, deals, and community services. Built on FastAPI with Claude AI (Anthropic) for intelligence, Supabase (PostgreSQL) for data, and deployed on Render.com free tier."),
p("The platform has been fully rebranded from \"Hello Desi\" to \"Mira\" with a complete brand identity overhaul. All user-facing messages now use Mira's voice: concise, warm, slightly desi, and conversational. However, several infrastructure-level identifiers still reference the legacy \"hello-desi\" name (see Section 12: Legacy Naming Cleanup)."),
p("Current state: feature-complete for MVP, zero real users. The next milestone is user acquisition in Columbus, OH as the initial target metro."),

// ── 2. INFRASTRUCTURE ──
h1("2. Infrastructure & Tech Stack"),
tbl([3000, 6360], [
  hdr(["Component", "Details"], [3000, 6360]),
  row(["Backend", "FastAPI (Python 3.11) with async support"], [3000, 6360]),
  row(["AI Engine", "Claude Haiku (90% queries) + Sonnet (complex: immigration, finance)"], [3000, 6360]),
  row(["Database", "Supabase PostgreSQL \u2014 9 tables, 4,312+ business listings"], [3000, 6360]),
  row(["Hosting", "Render.com Free Tier \u2014 Docker deploy, ~50s cold start, spins down on idle"], [3000, 6360]),
  row(["WhatsApp", "Meta Cloud API via WhatsApp Business Platform"], [3000, 6360]),
  row(["Payments", "Stripe Payment Links (env var based \u2014 not yet configured in Stripe/Render)"], [3000, 6360]),
  row(["Scheduling", "Claude Desktop scheduled-tasks MCP \u2014 not production-grade (see Section 9)"], [3000, 6360]),
  row(["Secrets Mgmt", "Render environment variables for production; local .env for development"], [3000, 6360]),
  row(["Monitoring", "None configured \u2014 no alerting, APM, or error tracking (see Section 11)"], [3000, 6360]),
  row(["Repo", "github.com/mailanu655/hello-desi (legacy name \u2014 see Section 12)"], [3000, 6360]),
  row(["Live URL", "https://hello-desi.onrender.com (legacy name \u2014 see Section 12)"], [3000, 6360]),
]),

calloutBox("Known limitation: Render free tier spins down after 15 min of inactivity. Cold starts take ~50s. All in-memory state (sessions, seen_users, rate-limit cache) is lost on every restart or deploy. This is the single biggest reliability risk for user experience.", AMBER),

// ── 3. DATABASE ──
h1("3. Database Schema"),
tbl([2000, 1200, 6160], [
  hdr(["Table", "Rows", "Purpose & Key Columns"], [2000, 1200, 6160]),
  row(["businesses", "4,312+", "Directory: name, category, city, state, phone, is_featured, source_id (owner wa_id)"], [2000, 1200, 6160]),
  row(["deals", "~0", "Time-limited promotions: business_id, title, description, expires_at, city"], [2000, 1200, 6160]),
  row(["inquiry_logs", "Growing", "Every search logged: business_id, user_wa_id, inquiry_type, message_snippet, city"], [2000, 1200, 6160]),
  row(["subscriptions", "~0", "Subscription tiers: business_id, wa_id, plan, status, deals_per_month, expires_at"], [2000, 1200, 6160]),
  row(["digest_subscribers", "~0", "Opt-in daily digest: wa_id, city, status, created_at"], [2000, 1200, 6160]),
  row(["events", "0", "Community events (schema ready, no WhatsApp flow built)"], [2000, 1200, 6160]),
  row(["classifieds", "0", "Buy/sell marketplace (schema ready, no WhatsApp flow built)"], [2000, 1200, 6160]),
  row(["conversations", "~0", "Message history for AI context"], [2000, 1200, 6160]),
  row(["users", "~0", "User profiles and preferences"], [2000, 1200, 6160]),
]),
p("Note: \"~0\" indicates the table exists and is functional but has no production data because the bot has zero real users."),

// ── 4. API ENDPOINTS ──
h1("4. API Endpoints"),
tbl([1000, 3360, 3200, 1800], [
  hdr(["Method", "Endpoint", "Description", "Auth"], [1000, 3360, 3200, 1800]),
  row(["POST", "/api/v1/webhook", "WhatsApp message handler", "Meta signature"], [1000, 3360, 3200, 1800]),
  row(["GET", "/api/v1/webhook", "Meta verification challenge", "Verify token"], [1000, 3360, 3200, 1800]),
  row(["POST", "/api/v1/tasks/proof-messages", "Trigger weekly proof messages", "CRON_SECRET header"], [1000, 3360, 3200, 1800]),
  row(["POST", "/api/v1/tasks/digest", "Trigger daily metro digest", "CRON_SECRET header"], [1000, 3360, 3200, 1800]),
  row(["GET", "/api/v1/tasks/analytics", "Live dashboard stats", "CRON_SECRET header"], [1000, 3360, 3200, 1800]),
  row(["GET", "/health", "Health check (returns service: mira)", "None (public)"], [1000, 3360, 3200, 1800]),
]),

new Paragraph({ children: [new PageBreak()] }),

// ── 5. FEATURE STATUS ──
h1("5. Feature Status Overview"),
tbl([2200, 1200, 5960], [
  hdr(["Feature", "Status", "Details"], [2200, 1200, 5960]),
  featureRow("Business Directory", "DONE", "4,312+ listings across 49 states. AI-powered natural language search by category, city, name."),
  featureRow("Business Registration", "DONE", "Multi-step WhatsApp flow: name \u2192 category \u2192 city \u2192 phone \u2192 confirm. In-memory session (lost on restart)."),
  featureRow("Deals & Promotions", "DONE", "Businesses post time-limited deals. City-based browsing. Confirmation in Mira voice."),
  featureRow("AI Chat (Claude)", "DONE", "Full Mira personality system prompt. Dual-model routing. Signature phrases integrated."),
  featureRow("Monetization Engine", "DONE", "3-tier plans (Free/$15/$30). Upgrade flow, plan status, business analytics commands."),
  featureRow("Inquiry Tracking", "DONE", "Every business search logged to inquiry_logs. Powers proof messages and analytics."),
  featureRow("Lead Notifications", "DONE", "Instant WhatsApp alert to owners. Rate-limited 1/hr/business. No retry on failure (see Section 11)."),
  featureRow("Weekly Proof Messages", "DONE", "Monday 10am. Inquiry count + trend. Three message variants. Upgrade CTA for free tier."),
  featureRow("Daily Metro Digest", "DONE", "8am daily. Opt-in city digest with new businesses, deals, featured sponsors."),
  featureRow("First-Time Onboarding", "DONE", "In-memory seen_users set. Resets on every restart \u2014 returning users see welcome again."),
  featureRow("Analytics Endpoint", "DONE", "GET /tasks/analytics \u2014 live counts for businesses, inquiries, subs, deals."),
  featureRow("Mira Brand Voice", "DONE", "Complete rebrand across 7 service files. All user-facing messages match brand guide."),
  featureRow("Cron Scheduling", "PARTIAL", "Claude Desktop MCP configured (not production-grade). No Render cron or external scheduler."),
  featureRow("Stripe Integration", "PARTIAL", "Env vars wired in code. Payment links NOT created in Stripe. No webhook for auto-activation."),
  featureRow("Deal Limits", "NOT ENFORCED", "PLANS dict defines 1/5/999 deals_per_month. No code checks count before allowing new deal posts."),
  featureRow("Session Persistence", "PENDING", "All in-memory: upgrade sessions, seen_users, rate-limit cache. Lost on restart."),
  featureRow("Lead Classification", "PENDING", "No hot/warm/cold scoring. All inquiries treated equally."),
  featureRow("City Auto-Detection", "PENDING", "Users must specify city. Could auto-detect from phone area code."),
  featureRow("Favorites & Saved Lists", "PENDING", "Users cannot save preferred businesses."),
  featureRow("Event Listings", "PENDING", "Schema exists. No WhatsApp flow or browsing built."),
  featureRow("Classifieds", "PENDING", "Schema exists. No WhatsApp flow built."),
  featureRow("User Acquisition", "PENDING", "Zero real users. Bot not shared in any WhatsApp groups yet."),
]),

new Paragraph({ children: [new PageBreak()] }),

// ── 6. CODEBASE ──
h1("6. Codebase Structure"),

h2("6.1 Project Tree"),
p("Hello Desi/", { run: { bold: true } }),
p("  app/", { run: { font: "Courier New", size: 20 } }),
p("    api/webhook.py          \u2014 WhatsApp message handler & routing", { run: { font: "Courier New", size: 20 } }),
p("    api/tasks.py            \u2014 Cron endpoints (proof, digest, analytics)", { run: { font: "Courier New", size: 20 } }),
p("    api/deps.py             \u2014 Dependency injection (settings)", { run: { font: "Courier New", size: 20 } }),
p("    services/claude_service.py         \u2014 AI engine + Mira system prompt", { run: { font: "Courier New", size: 20 } }),
p("    services/monetization_service.py   \u2014 Upgrade flow, lead notifs, stats", { run: { font: "Courier New", size: 20 } }),
p("    services/proof_message_service.py  \u2014 Weekly business proof messages", { run: { font: "Courier New", size: 20 } }),
p("    services/digest_service.py         \u2014 Daily metro digest system", { run: { font: "Courier New", size: 20 } }),
p("    services/business_registration.py  \u2014 Multi-step add-business flow", { run: { font: "Courier New", size: 20 } }),
p("    services/business_service.py       \u2014 Core business lookup/search", { run: { font: "Courier New", size: 20 } }),
p("    services/deals_service.py          \u2014 Deal posting & browsing", { run: { font: "Courier New", size: 20 } }),
p("    services/intent_router.py          \u2014 Message intent classification", { run: { font: "Courier New", size: 20 } }),
p("    services/whatsapp_service.py       \u2014 WhatsApp API client (send/receive)", { run: { font: "Courier New", size: 20 } }),
p("    main.py                 \u2014 FastAPI app init & router registration", { run: { font: "Courier New", size: 20 } }),
p("  config/settings.py        \u2014 Pydantic settings (env vars)", { run: { font: "Courier New", size: 20 } }),
p("  scripts/seed_businesses.py \u2014 Business data seeding script", { run: { font: "Courier New", size: 20 } }),
p("  tests/                    \u2014 Empty (no tests written)", { run: { font: "Courier New", size: 20 } }),
p("  Dockerfile                \u2014 Docker build for Render deploy", { run: { font: "Courier New", size: 20 } }),
p("  pyproject.toml            \u2014 Python deps (name: mira)", { run: { font: "Courier New", size: 20 } }),
p("  render.yaml               \u2014 Render service config (name: mira)", { run: { font: "Courier New", size: 20 } }),

h2("6.2 Key Service Details"),
h3("webhook.py (app/api/)"),
p("Main message handler. Routes through intent detection, session management, AI response. Contains in-memory seen_users set for first-time onboarding, digest subscribe/unsubscribe, monetization session handling, and \"my weekly report\" command trigger."),

h3("claude_service.py"),
p("AI engine with complete Mira personality system prompt. Dual-model routing: Haiku for 90% of queries (fast, cheap), Sonnet for complex topics (immigration, finance, legal). Enforces short WhatsApp-style messages. Error handling: catches API errors with generic fallback message."),

h3("monetization_service.py"),
p("Full upgrade flow with in-memory session state (10-min timeout). Business lookup \u2192 plan selection \u2192 confirmation \u2192 Stripe link. Lead notifications use async fire-and-forget with 1-hour rate limiting per business. Failures are logged but not retried or persisted."),

h3("whatsapp_service.py"),
p("HTTP client for Meta WhatsApp Cloud API. Handles send_text_message with error handling for timeouts, HTTP status errors, and request errors. All failures are logged. No retry logic or dead-letter queue for failed sends."),

new Paragraph({ children: [new PageBreak()] }),

// ── 7. BRAND IDENTITY ──
h1("7. Mira Brand Identity"),
bp("Positioning: ", "\"Your smart desi friend on WhatsApp\""),
bp("Personality: ", "Friendly, helpful, slightly desi, clear & quick"),
bp("Tone: ", "Short WhatsApp messages, light emoji use, always gives options"),
bp("Business-facing: ", "Slightly more professional, data-driven, still warm"),

h2("7.1 Signature Phrases"),
bullet("\"Got you \ud83d\udc4d\" \u2014 acknowledgment"),
bullet("\"Here are some good options \ud83d\udc47\" \u2014 search results"),
bullet("\"Want more like this?\" \u2014 engagement"),
bullet("\"Try this \ud83d\udc49 ...\" \u2014 suggestion"),
bullet("\"Found something useful? Share with your group \ud83d\ude4c\" \u2014 growth hook"),

h2("7.2 Voice Applied Across All Touchpoints"),
tbl([2800, 6560], [
  hdr(["Touchpoint", "Mira Voice Example"], [2800, 6560]),
  row(["Welcome", "\"Hi {name}! I'm Mira \ud83d\ude0a I can help you find: groceries, food, babysitters, deals...\""], [2800, 6560]),
  row(["Registration", "\"Got you \ud83d\udc4d Let's get you listed!\" \u2192 \"{name} is now listed! \ud83c\udf89\""], [2800, 6560]),
  row(["Deal Confirm", "\"Your deal is live! \ud83c\udf89 People in your area will see this \ud83d\udc4d\""], [2800, 6560]),
  row(["Upgrade Flow", "\"Got you \ud83d\udc4d Let's get you upgraded!\" \u2192 concise plan cards"], [2800, 6560]),
  row(["Daily Digest", "\"\ud83c\udf06 Columbus with Mira\" \u2014 clean sections for groceries, deals, sponsored"], [2800, 6560]),
  row(["Proof Message", "\"Hi! This is Mira \ud83d\udc4b This week for {biz}: X people searched...\""], [2800, 6560]),
  row(["Lead Alert", "\"\ud83d\udd14 New customer interest! Someone searched for... Your business was shown \ud83d\udc4d\""], [2800, 6560]),
]),

h2("7.3 Conversion Funnel"),
p("The upgrade funnel works through a proof-driven loop: a user searches for a category, their search is logged as an inquiry, the business owner gets an instant lead notification (\"Someone searched for your category! Your business was shown\"), and on Monday the owner gets a weekly proof message with their total inquiry count + trend. Free-tier owners see an upgrade CTA on every proof message and lead notification. The intent is to demonstrate value (\"people are finding you\") before asking for money."),
calloutBox("Gap: There is no tracking of conversion rates (lead notification \u2192 upgrade click \u2192 payment completion). This should be instrumented before spending on user acquisition.", AMBER),

// ── 8. COMMIT HISTORY ──
h1("8. Commit History"),
tbl([1500, 7860], [
  hdr(["Hash", "Description"], [1500, 7860]),
  row(["a15ddba", "feat: apply Mira brand voice across all user-facing messages"], [1500, 7860]),
  row(["e709eee", "rebrand: Hello Desi \u2192 Mira (file names, config, health check)"], [1500, 7860]),
  row(["9d0f37c", "feat: launch-ready \u2014 lead notifications, onboarding, analytics, Stripe env vars"], [1500, 7860]),
  row(["1977e26", "feat: add weekly proof messages + daily metro digest"], [1500, 7860]),
  row(["2ff53db", "Add monetization: featured listings, inquiry tracking, subscription plans"], [1500, 7860]),
  row(["b370728", "Fix f-string backslash SyntaxError for Python 3.11 compatibility"], [1500, 7860]),
  row(["f017ad3", "Add Deals & Promotions \u2014 WhatsApp deal posting and browsing"], [1500, 7860]),
  row(["03d84e1", "feat: add business registration & update via WhatsApp chat"], [1500, 7860]),
  row(["422c611", "feat: 4 new categories + Round 5 city hints \u2014 592 total businesses"], [1500, 7860]),
  row(["63d5665", "feat: Round 4 city hints \u2014 25 new cities"], [1500, 7860]),
  row(["dd23f8d", "feat: expand business categories and city coverage"], [1500, 7860]),
  row(["f1c0dd2", "feat: add business lookup from Supabase + seed script"], [1500, 7860]),
  row(["082236e", "Enforce strict English-only responses"], [1500, 7860]),
  row(["c4c325e", "Initial commit: Hello Desi WhatsApp bot"], [1500, 7860]),
]),

new Paragraph({ children: [new PageBreak()] }),

// ── 9. PENDING ──
h1("9. Pending / Next Steps"),

h2("9.1 Launch Blockers"),
new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Get Real Users: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "Share Mira in Columbus-area WhatsApp groups (desi community, temple groups, apartment complexes). Zero real users currently. This is the single most important action item.", font: "Arial", size: 22 }),
]}),
new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Persist User State: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "The in-memory seen_users set and upgrade session state are wiped on every Render restart (~50s cold start). A user mid-registration loses their progress. Every returning user gets \"first time\" welcome again. Fix: add a Supabase user_sessions table with a TTL column. No Redis needed.", font: "Arial", size: 22 }),
]}),
new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Create Stripe Payment Links: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "Set up Featured ($15/mo) and Premium ($30/mo) payment links in Stripe dashboard. Add STRIPE_FEATURED_LINK and STRIPE_PREMIUM_LINK to Render env vars.", font: "Arial", size: 22 }),
]}),
new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Production Cron Jobs: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "Replace Claude Desktop MCP with Render cron or external scheduler (cron-job.org) for reliable daily digest (8am) and weekly proof (Monday 10am).", font: "Arial", size: 22 }),
]}),

h2("9.2 High Priority (Post-Launch)"),
new Paragraph({ numbering: { reference: "numbers2", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Stripe Webhook Integration: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "Payment links are fire-and-forget. Need webhook to auto-activate plans on successful payment and handle expirations/cancellations.", font: "Arial", size: 22 }),
]}),
new Paragraph({ numbering: { reference: "numbers2", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Enforce Deal Limits: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "The PLANS dict defines 1/5/999 deals_per_month but no code checks the count before allowing a new deal post. Need validation in deals_service.py.", font: "Arial", size: 22 }),
]}),
new Paragraph({ numbering: { reference: "numbers2", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Lead Classification: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "Score inquiries as hot/warm/cold based on intent. \"I need a caterer for Saturday\" vs \"just browsing\" should trigger different notifications.", font: "Arial", size: 22 }),
]}),
new Paragraph({ numbering: { reference: "numbers2", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "City Auto-Detection: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "Detect user city from phone area code or first message context. Eliminate \"which city?\" friction.", font: "Arial", size: 22 }),
]}),
new Paragraph({ numbering: { reference: "numbers2", level: 0 }, spacing: { after: 80 }, children: [
  new TextRun({ text: "Lead Notification Reliability: ", bold: true, font: "Arial", size: 22 }),
  new TextRun({ text: "Add retry logic or a notification_log table. Currently, if WhatsApp API returns 429 or times out, the business owner silently misses the alert.", font: "Arial", size: 22 }),
]}),

h2("9.3 Backlog"),
bullet("Favorites & Saved Lists \u2014 let users save preferred businesses"),
bullet("Event Listings \u2014 community events with RSVP via WhatsApp"),
bullet("Classifieds/Marketplace \u2014 buy/sell within community"),
bullet("Business Reviews \u2014 ratings and feedback loop"),
bullet("Multi-language Support \u2014 Hindi, Telugu, Tamil message detection"),
bullet("Referral Program \u2014 \"Share with 3 friends\" viral loop"),
bullet("Daily Digest Sponsorship \u2014 paid placement in the digest"),
bullet("Pay-per-Inquiry model \u2014 charge per qualified lead"),

new Paragraph({ children: [new PageBreak()] }),

// ── 10. REVENUE MODEL ──
h1("10. Revenue Model"),
tbl([2000, 1500, 5860], [
  hdr(["Plan", "Price", "Features"], [2000, 1500, 5860]),
  row(["Free", "$0/mo", "Basic listing, 1 deal/month (not enforced), no analytics"], [2000, 1500, 5860]),
  row(["\u2b50 Featured", "$15/mo", "Featured badge (appear first), 5 deals/month (not enforced), analytics + stats"], [2000, 1500, 5860]),
  row(["\ud83d\udc51 Premium", "$30/mo", "All Featured perks + unlimited deals, priority placement, premium support"], [2000, 1500, 5860]),
]),
p("Revenue streams: Featured Listings (recurring subscription), Lead Notifications (value proof driving upgrades), Daily Digest Sponsorship (future), Pay-per-Inquiry (future)."),
calloutBox("Note: Deal limits (1/5/unlimited per month) are defined in the PLANS dictionary but not enforced in code. Any business can currently post unlimited deals regardless of plan tier.", AMBER),

// ── 11. KNOWN GAPS ──
h1("11. Testing, Monitoring & Known Tech Debt"),

h2("11.1 Testing"),
p("There are no tests. The tests/ directory exists but is empty. No unit tests, integration tests, or end-to-end tests have been written. All testing has been manual via WhatsApp. Priority: write tests for intent routing, business search, and monetization flows before scaling."),

h2("11.2 Monitoring & Error Handling"),
p("No monitoring, alerting, or APM is configured. Errors are logged via Python's logging module to stdout (visible in Render logs), but there is no Sentry, Datadog, or equivalent."),
p("Claude API errors: caught with generic fallback message (\"Sorry, something went wrong\"). WhatsApp send errors: caught and logged (timeout, HTTP status, request errors) but not retried. Lead notification failures: silently swallowed \u2014 business owner never knows they missed an alert."),

h2("11.3 WhatsApp API Rate Limits"),
p("Meta imposes rate limits on WhatsApp Business API: 80 messages/second for session messages, tiered limits for template messages (based on quality rating). The daily digest and weekly proof messages send one-by-one with no rate-limit awareness. At scale (1,000+ subscribers), this could hit Meta's limits and silently fail. Need: batch sending with backoff, or queue-based sending."),

h2("11.4 Known Tech Debt"),
bullet("Python 3.11 f-string backslash fix (commit b370728) \u2014 resolved"),
bullet("In-memory state for sessions, seen_users, notification cache \u2014 needs Supabase migration"),
bullet("No input validation on business registration fields (name, phone, city)"),
bullet("No duplicate business detection (same business can be registered multiple times)"),
bullet("Seed script (seed_businesses.py) is one-time; no mechanism to update/deduplicate catalog"),

// ── 12. LEGACY NAMING ──
h1("12. Legacy Naming Cleanup"),
p("The rebrand from \"Hello Desi\" to \"Mira\" updated all user-facing messages, pyproject.toml, render.yaml, and the health check response. However, the following still reference the old name and should be cleaned up:"),
tbl([3500, 3500, 2360], [
  hdr(["Item", "Current Value", "Action"], [3500, 3500, 2360]),
  row(["GitHub Repo", "mailanu655/hello-desi", "Rename repo to mira-bot or similar"], [3500, 3500, 2360]),
  row(["Render Service URL", "hello-desi.onrender.com", "Create new service or rename"], [3500, 3500, 2360]),
  row(["Render Service ID", "srv-d73vdb7pm1nc738md8e0", "Auto-changes with new service"], [3500, 3500, 2360]),
  row(["Cron Task: Digest", "hello-desi-daily-digest", "Rename scheduled task ID"], [3500, 3500, 2360]),
  row(["Cron Task: Proof", "hello-desi-weekly-proof", "Rename scheduled task ID"], [3500, 3500, 2360]),
  row(["Local Directory", "Hello Desi/", "Rename folder"], [3500, 3500, 2360]),
]),
calloutBox("Recommendation: Defer the GitHub/Render rename until after initial user acquisition. Renaming the Render service changes the URL and requires updating the Meta webhook config. Do this during a maintenance window when you have confirmed the bot is working with real users.", ACCENT),

// ── 13. SCALING ──
h1("13. Scaling Considerations"),
p("What breaks first as usage grows:"),
tbl([1800, 2200, 5360], [
  hdr(["Users", "Bottleneck", "Fix"], [1800, 2200, 5360]),
  row(["10-50", "Render cold starts", "Upgrade to paid Render plan ($7/mo) for always-on"], [1800, 2200, 5360]),
  row(["50-200", "In-memory state loss", "Migrate sessions/seen_users to Supabase"], [1800, 2200, 5360]),
  row(["200-500", "WhatsApp rate limits", "Queue-based sending with backoff for digest/proof"], [1800, 2200, 5360]),
  row(["500-1,000", "Claude API costs", "Cache frequent queries, optimize Haiku/Sonnet routing"], [1800, 2200, 5360]),
  row(["1,000+", "Supabase free tier", "Upgrade Supabase plan or migrate to managed Postgres"], [1800, 2200, 5360]),
]),

// ── 14. KEY IDENTIFIERS ──
h1("14. Key Identifiers & Access"),
calloutBox("INTERNAL ONLY \u2014 Do not include this section in any externally shared version of this document. These identifiers, while not secrets, can facilitate targeted attacks if exposed.", RED),
tbl([3200, 6160], [
  hdr(["Resource", "Value"], [3200, 6160]),
  row(["GitHub Repo", "github.com/mailanu655/hello-desi"], [3200, 6160]),
  row(["Render Service URL", "https://hello-desi.onrender.com"], [3200, 6160]),
  row(["Render Service ID", "srv-d73vdb7pm1nc738md8e0"], [3200, 6160]),
  row(["Supabase Project ID", "atprqakojjlclwviaaii"], [3200, 6160]),
  row(["WhatsApp Business Acct", "959327453324618"], [3200, 6160]),
  row(["Phone Number ID", "1045027728691211"], [3200, 6160]),
  row(["Latest Commit", "a15ddba (Mira brand voice \u2014 March 29, 2026)"], [3200, 6160]),
  row(["Digest Cron Task", "hello-desi-daily-digest (8am daily)"], [3200, 6160]),
  row(["Proof Cron Task", "hello-desi-weekly-proof (Monday 10am)"], [3200, 6160]),
]),

      ]
    }
  ]
});

Packer.toBuffer(doc).then(buffer => {
  const outPath = "/sessions/youthful-exciting-archimedes/mnt/python-whatsapp-bot-main/Hello Desi/Mira_Project_Status_v2.docx";
  fs.writeFileSync(outPath, buffer);
  console.log("Created: " + outPath);
});
