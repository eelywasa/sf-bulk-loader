"""
Generates sf-bulk-loader-reference.pdf — portable project reference guide.
Run: pip install -r requirements-pdf.txt && python generate_pdf.py
"""

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

# ── Constants ──────────────────────────────────────────────────────────────────

OUTPUT = Path(__file__).parent / "sf-bulk-loader-reference.pdf"
SCREENSHOTS = Path(__file__).parent / "docs" / "screenshots"
GENERATED = datetime.now().strftime("%-d %B %Y")

# Brand colours
BLUE       = colors.HexColor("#2563EB")
BLUE_LIGHT = colors.HexColor("#EFF6FF")
BLUE_MID   = colors.HexColor("#DBEAFE")
DARK       = colors.HexColor("#111827")
GREY       = colors.HexColor("#6B7280")
BORDER     = colors.HexColor("#E5E7EB")
GREEN      = colors.HexColor("#16A34A")
RED        = colors.HexColor("#DC2626")
AMBER      = colors.HexColor("#D97706")
ROW_ALT    = colors.HexColor("#F9FAFB")

PAGE_W, PAGE_H = A4
MARGIN = 2 * cm
CONTENT_W = PAGE_W - 2 * MARGIN

# ── Styles ─────────────────────────────────────────────────────────────────────

base = getSampleStyleSheet()

def style(name, **kw):
    return ParagraphStyle(name, **kw)

S = {
    "h1": style("h1", fontSize=22, textColor=BLUE, spaceAfter=6, spaceBefore=18,
                fontName="Helvetica-Bold", leading=26),
    "h2": style("h2", fontSize=14, textColor=DARK, spaceAfter=4, spaceBefore=14,
                fontName="Helvetica-Bold", leading=18),
    "h3": style("h3", fontSize=11, textColor=DARK, spaceAfter=3, spaceBefore=10,
                fontName="Helvetica-Bold", leading=14),
    "body": style("body", fontSize=9.5, textColor=DARK, spaceAfter=5, leading=14,
                  fontName="Helvetica"),
    "small": style("small", fontSize=8.5, textColor=GREY, spaceAfter=4, leading=12,
                   fontName="Helvetica"),
    "code": style("code", fontSize=8, fontName="Courier", textColor=DARK,
                  spaceAfter=4, leading=11, backColor=colors.HexColor("#F3F4F6"),
                  borderPadding=(3, 4, 3, 4)),
    "caption": style("caption", fontSize=8, textColor=GREY, alignment=TA_CENTER,
                     spaceAfter=8, fontName="Helvetica-Oblique"),
    "cover_title": style("cover_title", fontSize=36, textColor=colors.white,
                         fontName="Helvetica-Bold", leading=42, alignment=TA_CENTER),
    "cover_sub": style("cover_sub", fontSize=14, textColor=colors.HexColor("#BFDBFE"),
                       fontName="Helvetica", alignment=TA_CENTER),
    "cover_date": style("cover_date", fontSize=10, textColor=colors.HexColor("#93C5FD"),
                        fontName="Helvetica", alignment=TA_CENTER),
    "th": style("th", fontSize=8.5, fontName="Helvetica-Bold", textColor=DARK,
                alignment=TA_LEFT),
    "td": style("td", fontSize=8.5, fontName="Helvetica", textColor=DARK,
                leading=11, alignment=TA_LEFT),
    "td_code": style("td_code", fontSize=8, fontName="Courier", textColor=DARK,
                     leading=11),
    "td_grey": style("td_grey", fontSize=8.5, fontName="Helvetica", textColor=GREY,
                     leading=11, alignment=TA_LEFT),
    "bullet": style("bullet", fontSize=9.5, textColor=DARK, spaceAfter=3,
                    leading=13, fontName="Helvetica",
                    leftIndent=12, firstLineIndent=0,
                    bulletIndent=0, bulletText="•"),
}

# ── Page templates ─────────────────────────────────────────────────────────────

def _header_footer(canvas, doc):
    """Draw header bar + footer on every non-cover page."""
    canvas.saveState()
    page = doc.page

    # Header bar
    canvas.setFillColor(BLUE)
    canvas.rect(0, PAGE_H - 1.1 * cm, PAGE_W, 1.1 * cm, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(colors.white)
    canvas.drawString(MARGIN, PAGE_H - 0.75 * cm, "Salesforce Bulk Loader")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#BFDBFE"))
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.75 * cm, "Reference Guide")

    # Footer rule
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 1.4 * cm, PAGE_W - MARGIN, 1.4 * cm)

    # Page number
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GREY)
    canvas.drawCentredString(PAGE_W / 2, 0.85 * cm, f"Page {page}")

    # Generated date right
    canvas.drawRightString(PAGE_W - MARGIN, 0.85 * cm, f"Generated {GENERATED}")

    canvas.restoreState()


def _cover_page(canvas, doc):
    """Full-bleed cover — no header/footer chrome."""
    canvas.saveState()

    # Blue background
    canvas.setFillColor(BLUE)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    # Decorative rectangle accent
    canvas.setFillColor(colors.HexColor("#1D4ED8"))
    canvas.rect(0, PAGE_H * 0.38, PAGE_W, PAGE_H * 0.02, fill=1, stroke=0)

    # Snowflake / asterisk logo stand-in (text-based)
    canvas.setFont("Helvetica-Bold", 64)
    canvas.setFillColor(colors.white)
    canvas.drawCentredString(PAGE_W / 2, PAGE_H * 0.65, "❄")

    canvas.restoreState()


# ── Helpers ─────────────────────────────────────────────────────────────────────

def screenshot(filename, caption, max_w=CONTENT_W, max_h=12 * cm):
    path = SCREENSHOTS / filename
    if not path.exists():
        return []
    img = Image(str(path))
    # Scale proportionally
    iw, ih = img.imageWidth, img.imageHeight
    scale = min(max_w / iw, max_h / ih, 1.0)
    img.drawWidth  = iw * scale
    img.drawHeight = ih * scale
    return [
        img,
        Paragraph(caption, S["caption"]),
    ]


def section_rule():
    return HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=6, spaceBefore=2)


def table(headers, rows, col_widths=None):
    """Styled table with alternating rows."""
    data = [[Paragraph(h, S["th"]) for h in headers]]
    for row in rows:
        data.append([
            Paragraph(str(c), S["td_code"] if i == 0 and len(headers) >= 3 else S["td"])
            for i, c in enumerate(row)
        ])

    ts = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  BLUE_MID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("GRID",         (0, 0), (-1, -1),  0.4, BORDER),
        ("TOPPADDING",   (0, 0), (-1, -1),  4),
        ("BOTTOMPADDING",(0, 0), (-1, -1),  4),
        ("LEFTPADDING",  (0, 0), (-1, -1),  6),
        ("RIGHTPADDING", (0, 0), (-1, -1),  6),
        ("VALIGN",       (0, 0), (-1, -1),  "TOP"),
    ])

    t = Table(data, colWidths=col_widths or [CONTENT_W / len(headers)] * len(headers),
              repeatRows=1)
    t.setStyle(ts)
    return t


def callout(text, color=BLUE_LIGHT, border=BLUE):
    """Coloured callout / tip box."""
    data = [[Paragraph(text, S["body"])]]
    ts = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), color),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("LINEAFTER",    (0, 0), (0, -1),  3, border),
        ("LINEBEFORE",   (0, 0), (0, -1),  3, border),
    ])
    t = Table(data, colWidths=[CONTENT_W])
    t.setStyle(ts)
    return t


# ── Content builder ────────────────────────────────────────────────────────────

def build_story():
    story = []

    # ── COVER ──────────────────────────────────────────────────────────────────
    story.append(Spacer(1, PAGE_H * 0.45))
    story.append(Paragraph("Salesforce Bulk Loader", S["cover_title"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Reference Guide", S["cover_sub"]))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph(f"Generated {GENERATED}", S["cover_date"]))
    story.append(PageBreak())

    # ── 1. INTRODUCTION ────────────────────────────────────────────────────────
    story.append(Paragraph("1. Introduction", S["h1"]))
    story.append(section_rule())

    story.append(Paragraph(
        "Salesforce Bulk Loader is a containerised web application for orchestrating "
        "large-scale data loads into Salesforce using the <b>Bulk API 2.0</b>. "
        "It provides a browser-based interface for defining multi-object load plans, "
        "executing them against any Salesforce org, tracking job progress in real time, "
        "and capturing per-record success and error logs.",
        S["body"]
    ))

    story.append(Paragraph("Who should use this tool?", S["h2"]))
    story.append(Paragraph(
        "Salesforce Bulk Loader is designed for <b>Salesforce admins</b> and "
        "<b>data engineers</b> who need to load large volumes of data — hundreds of "
        "thousands to millions of records — reliably and repeatedly. It is particularly "
        "suited to organisations that run regular data migrations, periodic enrichment "
        "loads, or multi-object hierarchical inserts where order matters.",
        S["body"]
    ))

    story.append(Paragraph("Key capabilities", S["h2"]))
    bullets = [
        "<b>Multi-step load plans</b> — define parent-before-child load order in a single plan; steps execute sequentially and partitions run concurrently.",
        "<b>Bulk API 2.0</b> — all DML operations (insert, update, upsert, delete) and bulk queries (query, queryAll) use Salesforce's high-throughput API.",
        "<b>Real-time monitoring</b> — WebSocket-powered run detail page shows per-partition progress without manual refreshing.",
        "<b>Error handling</b> — configurable error threshold per step; optional abort-on-failure; per-record error CSVs for remediation.",
        "<b>Retry failed rows</b> — retry just the failed records from a step without re-processing successes.",
        "<b>Bulk queries</b> — run SOQL queries that write result CSVs, optionally chaining the output directly into a DML step in the same run.",
        "<b>S3 integration</b> — use S3 buckets as CSV input sources or output destinations.",
        "<b>Notifications</b> — email and webhook alerts on run completion.",
        "<b>Three deployment profiles</b> — desktop (single-user, no login), self-hosted Docker, and AWS-hosted.",
    ]
    for b in bullets:
        story.append(Paragraph(b, S["bullet"]))
        story.append(Spacer(1, 1))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("Dashboard — live run statistics at a glance", S["caption"]))
    story += screenshot("sf-bulk-loader-dashboard.png",
                        "Figure 1: Dashboard showing active run stats, recent completions, and error rate.")

    story.append(PageBreak())

    # ── 2. ARCHITECTURE ────────────────────────────────────────────────────────
    story.append(Paragraph("2. Architecture Overview", S["h1"]))
    story.append(section_rule())

    story.append(Paragraph("System components", S["h2"]))
    story.append(Paragraph(
        "The application is composed of three main layers deployed together as a Docker "
        "Compose stack.",
        S["body"]
    ))

    comp_data = [
        ["Component", "Technology", "Role"],
        ["Frontend", "React 18, Vite, TypeScript, Tailwind CSS",
         "Browser SPA — pages for Dashboard, Connections, Plans, Runs, Files. "
         "React Query for server state; WebSocket for live run updates."],
        ["nginx", "nginx",
         "Reverse proxy (hosted profiles). Routes /api/* and /ws/* to the backend; "
         "serves the built React bundle for all other paths."],
        ["Backend", "Python 3.12, FastAPI, SQLAlchemy 2.0 async",
         "REST API, WebSocket endpoint, asyncio orchestrator background tasks, "
         "Salesforce JWT Bearer auth, Bulk API 2.0 client, CSV processor."],
        ["Database", "SQLite (WAL) or PostgreSQL",
         "Persists all application state. SQLite is the default; PostgreSQL is "
         "recommended for multi-user or production installs."],
    ]
    story.append(table(comp_data[0], comp_data[1:],
                       col_widths=[3*cm, 5*cm, CONTENT_W - 8*cm]))
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("Data model", S["h2"]))
    story.append(Paragraph(
        "Five core entities form a clear parent-to-child hierarchy:",
        S["body"]
    ))
    story.append(Paragraph(
        "<b>Connection</b> → <b>LoadPlan</b> → <b>LoadStep</b> + <b>LoadRun</b> → <b>JobRecord</b>",
        S["code"]
    ))
    story.append(Paragraph(
        "A <b>Connection</b> holds the Salesforce org credentials (JWT Bearer, "
        "Fernet-encrypted private key). A <b>LoadPlan</b> belongs to one Connection and "
        "defines the load configuration. Each plan has one or more <b>LoadStep</b>s that "
        "declare what to load. Executing a plan creates a <b>LoadRun</b>, which in turn "
        "produces one <b>JobRecord</b> per CSV partition per step.",
        S["body"]
    ))

    story.append(Paragraph("Distribution profiles", S["h2"]))
    story.append(Paragraph(
        "The <code>APP_DISTRIBUTION</code> environment variable selects the deployment "
        "profile, which determines authentication, transport, and storage behaviour.",
        S["body"]
    ))
    profile_data = [
        ["Profile", "Auth", "Transport", "Database", "Typical use"],
        ["desktop",     "None (no login)",  "Loopback only",    "SQLite only",          "Electron single-user app"],
        ["self_hosted", "Email + password", "HTTP or HTTPS",    "SQLite or PostgreSQL", "Docker; internal/on-prem"],
        ["aws_hosted",  "Email + password", "HTTPS required",   "PostgreSQL required",  "Cloud (CloudFront + RDS)"],
    ]
    story.append(table(profile_data[0], profile_data[1:],
                       col_widths=[2.8*cm, 3*cm, 3*cm, 3.5*cm, CONTENT_W - 12.3*cm]))
    story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph("Execution flow", S["h2"]))
    story.append(Paragraph(
        "When a run is triggered the orchestrator executes each LoadStep in sequence order:",
        S["body"]
    ))
    steps = [
        "Resolve the step's CSV glob (or S3 prefix) to a list of files.",
        "Partition each file into chunks of <code>partition_size</code> records.",
        "Create one <b>JobRecord</b> per partition.",
        "Execute all partitions concurrently, bounded by <code>max_parallel_jobs</code>.",
        "For each partition: authenticate via JWT Bearer → create Bulk API job → upload CSV → close job → poll to completion with exponential backoff (5 s → 30 s max) → download result CSVs.",
        "After all partitions complete: evaluate the error threshold. If exceeded and <i>Abort on step failure</i> is on, abort the run.",
        "Broadcast progress over WebSocket throughout.",
    ]
    for i, s in enumerate(steps, 1):
        story.append(Paragraph(f"{i}.  {s}", S["bullet"]))

    story.append(PageBreak())

    # ── 3. CORE CONCEPTS ───────────────────────────────────────────────────────
    story.append(Paragraph("3. Core Concepts Glossary", S["h1"]))
    story.append(section_rule())

    glossary = [
        ("Connection",
         "Stores the credentials required to authenticate with a Salesforce org via "
         "JWT Bearer OAuth 2.0 — the Connected App's consumer key, the RSA private key "
         "(Fernet-encrypted at rest), and the org's instance URL. One connection per org."),
        ("Load Plan",
         "A reusable template that defines what to load, in what order, and the "
         "execution policy (max parallel jobs, error threshold, abort behaviour). "
         "Executing a plan creates a Load Run."),
        ("Load Step",
         "An individual operation within a plan — one Salesforce object, one operation "
         "type (insert / update / upsert / delete / query / queryAll), a CSV file glob "
         "pattern, and an optional partition size override. Steps execute sequentially "
         "within a run; partitions within a step execute concurrently."),
        ("Load Run",
         "A single execution of a Load Plan. A run progresses through statuses: "
         "pending → running → completed | completed_with_errors | failed | aborted. "
         "Each run produces result files (success / error / unprocessed CSVs + logs.zip)."),
        ("Job Record",
         "Represents one Salesforce Bulk API 2.0 job — one partition of one step in "
         "one run. Tracks the sf_job_id, record counts, result file paths, and status. "
         "Statuses: pending → uploading → upload_complete → in_progress → "
         "job_complete | failed | aborted."),
        ("Partition",
         "A chunk of records split from a source CSV file. The partition size (default "
         "10,000 records) controls how many records go into each Bulk API job. Smaller "
         "partitions increase parallelism; larger partitions reduce API overhead."),
        ("Error Threshold",
         "A per-step percentage (default 10%). After all partitions in a step complete, "
         "the loader computes failed_records / processed_records × 100. If this exceeds "
         "the threshold, the step is considered failing — triggering abort or "
         "completed_with_errors depending on the plan's abort setting."),
        ("External ID",
         "A Salesforce field used by upsert operations to match incoming records to "
         "existing ones. The Bulk Loader requires external IDs for upsert (no runtime "
         "ID mapping). Parent relationships in CSV files also reference external IDs "
         "using the notation ParentObject.ExternalId__c."),
        ("Output Sink",
         "Where result CSVs are written after a run. Options: local filesystem "
         "(data/output/) or an S3 bucket configured as a Storage Connection on the plan."),
        ("Input Connection (S3)",
         "An optional S3 bucket configured as an alternative CSV input source. "
         "When set on a plan, steps can read their source CSVs from S3 rather than "
         "the local data/input/ directory."),
    ]

    for term, definition in glossary:
        story.append(Paragraph(term, S["h3"]))
        story.append(Paragraph(definition, S["body"]))

    story.append(PageBreak())

    # ── 4. QUICK START ─────────────────────────────────────────────────────────
    story.append(Paragraph("4. Quick-Start Walkthrough", S["h1"]))
    story.append(section_rule())
    story.append(Paragraph(
        "This walkthrough covers the full path from first deployment to a completed "
        "load run using the self-hosted Docker profile.",
        S["body"]
    ))

    story.append(Paragraph("Step 1 — Deploy", S["h2"]))
    story.append(Paragraph(
        "Prerequisites: Docker 24+ and Docker Compose v2. No local Python or Node.js required.",
        S["body"]
    ))
    story.append(Paragraph(
        "git clone https://github.com/eelywasa/sf-bulk-loader.git\n"
        "cd sf-bulk-loader\n"
        "cp .env.example .env\n"
        "# Edit .env: set ADMIN_EMAIL and ADMIN_PASSWORD\n"
        "mkdir -p data/input data/output data/db\n"
        "docker compose up --build",
        S["code"]
    ))
    story.append(Paragraph(
        "The app will be available at <b>http://localhost</b>. "
        "API documentation is at <b>http://localhost/api/docs</b>.",
        S["body"]
    ))
    story.append(callout(
        "<b>Bootstrap admin.</b> On a fresh database the backend creates the first "
        "admin account from ADMIN_EMAIL and ADMIN_PASSWORD in .env. These values are "
        "consumed once — they are ignored on every subsequent boot."
    ))

    story.append(Paragraph("Step 2 — Create a Salesforce Connection", S["h2"]))
    story.append(Paragraph(
        "Navigate to <b>Connections</b> and click <i>New Salesforce Connection</i>. "
        "You will need a Salesforce Connected App configured for JWT Bearer auth:",
        S["body"]
    ))
    conn_steps = [
        "Generate an RSA key pair: <code>openssl genrsa -out server.key 4096</code> then <code>openssl req -new -x509 -key server.key -out server.crt -days 3650</code>",
        "In Salesforce Setup, create a Connected App with OAuth enabled, upload the certificate (.crt), and grant the \"api\" OAuth scope.",
        "Enable \"Use digital signatures\" and set the permitted users to \"Admin approved users are pre-authorised\".",
        "Note the Consumer Key (client_id) from the Connected App.",
        "In Bulk Loader, enter the Instance URL, Username, Consumer Key, and paste the private key contents.",
        "Click <i>Test</i> to verify the connection before saving.",
    ]
    for s in conn_steps:
        story.append(Paragraph(s, S["bullet"]))

    story += screenshot("screenshot-connections.png",
                        "Figure 2: Connections page showing Salesforce and S3 storage connections.")

    story.append(Paragraph("Step 3 — Prepare CSV Files", S["h2"]))
    story.append(Paragraph(
        "Place source CSV files in <code>data/input/</code> (or your S3 input bucket). "
        "Files must follow these conventions:",
        S["body"]
    ))
    csv_rules = [
        "Encoding: UTF-8, Latin-1, or CP-1252 (detected automatically).",
        "Line endings: LF or CRLF.",
        "Header row: Salesforce API field names (e.g. <code>FirstName</code>, <code>External_Id__c</code>).",
        "Parent relationships: use dot notation — <code>Account.External_Id__c</code> sets the account lookup via external ID.",
        "For upsert operations: include the external ID field in every row.",
    ]
    for r in csv_rules:
        story.append(Paragraph(r, S["bullet"]))

    story.append(Paragraph("Step 4 — Create a Load Plan", S["h2"]))
    story.append(Paragraph(
        "Navigate to <b>Load Plans</b> and click <i>New Plan</i>. Configure the plan header:",
        S["body"]
    ))
    story += screenshot("screenshot-plans.png",
                        "Figure 3: Load Plans list — each plan groups an ordered set of steps.")
    story.append(Paragraph(
        "Then add steps in parent-before-child order. For each step specify:",
        S["body"]
    ))
    step_fields = [
        ["Field", "Description"],
        ["Object name",     "Salesforce API object name (e.g. Account, Contact__c). For query steps, a free-text label."],
        ["Operation",       "insert / update / upsert / delete / query / queryAll."],
        ["CSV file pattern","Glob over data/input/ — e.g. contacts/*.csv."],
        ["External ID field","Required for upsert — the field Salesforce uses to match records."],
        ["Partition size",  "Records per Bulk API job (inherits plan default if blank)."],
    ]
    story.append(table(step_fields[0], step_fields[1:],
                       col_widths=[3.5*cm, CONTENT_W - 3.5*cm]))

    story += screenshot("screenshot-plan-detail.png",
                        "Figure 4: Load Plan detail — plan settings including error threshold and abort behaviour.")

    story.append(Paragraph("Step 5 — Run and Monitor", S["h2"]))
    story.append(Paragraph(
        "From the plan page click <b>Start Run</b>. You will be taken to the run detail "
        "page, which receives live updates via WebSocket — no manual refresh needed.",
        S["body"]
    ))
    story += screenshot("screenshot-run-detail.png",
                        "Figure 5: Run detail — 1.8 M records across 5 steps, "
                        "each step showing partition-level progress and a retry option for failed rows.")

    story.append(callout(
        "<b>Retry failed rows.</b> If a step ends with errors, click <i>Retry Failed Records</i> "
        "on that step row. The loader creates new partitions from just the failed rows "
        "and re-submits them — you do not need to re-process the successes.",
        color=colors.HexColor("#ECFDF5"),
        border=GREEN,
    ))

    story.append(PageBreak())

    # ── 5. CONFIGURATION REFERENCE ─────────────────────────────────────────────
    story.append(Paragraph("5. Configuration Reference", S["h1"]))
    story.append(section_rule())
    story.append(Paragraph(
        "All configuration is via environment variables loaded from <code>.env</code>. "
        "Copy <code>.env.example</code> as a starting point — it contains inline comments "
        "for every variable.",
        S["body"]
    ))

    story.append(Paragraph("Core variables", S["h2"]))
    core_vars = [
        ["Variable", "Default", "Description"],
        ["APP_DISTRIBUTION",       "self_hosted",       "Deployment profile: desktop | self_hosted | aws_hosted."],
        ["ADMIN_EMAIL",            "(required)",         "Bootstrap admin email — creates the first account on an empty database."],
        ["ADMIN_PASSWORD",         "(required)",         "Bootstrap admin password. ≥12 chars, mixed case, digit, special character."],
        ["ENCRYPTION_KEY",         "(auto-generated)",   "Fernet key for Salesforce private key encryption. Back up data/db/encryption.key."],
        ["JWT_SECRET_KEY",         "(auto-generated)",   "HS256 key for signing session tokens."],
        ["DATABASE_URL",           "sqlite+aiosqlite:///…","SQLAlchemy connection string. Swap to postgresql+asyncpg://… for PostgreSQL."],
        ["SF_API_VERSION",         "v62.0",              "Salesforce REST/Bulk API version."],
        ["FRONTEND_BASE_URL",      "(empty)",            "Public URL of the app — required for password-reset email links."],
        ["JWT_EXPIRY_MINUTES",     "60",                 "Session token lifetime in minutes."],
        ["INVITATION_TTL_HOURS",   "24",                 "Admin-issued invitation link validity window."],
        ["LOG_LEVEL",              "INFO",               "DEBUG | INFO | WARNING | ERROR."],
    ]
    story.append(table(core_vars[0], core_vars[1:],
                       col_widths=[4.5*cm, 3.5*cm, CONTENT_W - 8*cm]))
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("Salesforce polling & partitioning", S["h2"]))
    sf_vars = [
        ["Variable", "Default", "Description"],
        ["SF_POLL_INTERVAL_INITIAL",  "5",      "Starting poll interval (seconds) for Bulk API job status."],
        ["SF_POLL_INTERVAL_MAX",      "30",     "Maximum poll interval after exponential backoff."],
        ["SF_JOB_TIMEOUT_MINUTES",    "30",     "Soft warning threshold for long-running jobs (logs once; continues polling)."],
        ["SF_JOB_MAX_POLL_SECONDS",   "3600",   "Hard cap on poll loop per job. 0 = unbounded."],
        ["DEFAULT_PARTITION_SIZE",    "10000",  "Records per Bulk API job partition."],
        ["MAX_PARTITION_SIZE",        "100000000", "Hard upper limit on partition size."],
        ["INPUT_DIR",                 "/data/input",  "Container path for source CSVs (read-only mount)."],
        ["OUTPUT_DIR",                "/data/output", "Container path for result files."],
    ]
    story.append(table(sf_vars[0], sf_vars[1:],
                       col_widths=[4.5*cm, 2.5*cm, CONTENT_W - 7*cm]))
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("Email (optional)", S["h2"]))
    story.append(Paragraph(
        "Email settings are managed in the UI at <b>Settings → Email</b> after first boot. "
        "The env vars below are only read on first boot to seed the database.",
        S["body"]
    ))
    email_vars = [
        ["Variable", "Default", "Description"],
        ["EMAIL_BACKEND",          "noop",          "noop | smtp | ses. noop silently discards all mail."],
        ["EMAIL_FROM_ADDRESS",     "(empty)",        "Envelope-from address (required for smtp/ses)."],
        ["EMAIL_FROM_NAME",        "(empty)",        "Display name in outbound mail."],
        ["EMAIL_SMTP_HOST",        "(empty)",        "SMTP server hostname."],
        ["EMAIL_SMTP_PORT",        "(empty)",        "SMTP port (typically 587 for STARTTLS, 465 for SSL)."],
        ["EMAIL_SMTP_USERNAME",    "(empty)",        "SMTP login username."],
        ["EMAIL_SMTP_PASSWORD",    "(empty)",        "SMTP password — encrypted in DB after first boot."],
        ["EMAIL_SES_REGION",       "us-east-1",     "AWS region when backend is ses."],
    ]
    story.append(table(email_vars[0], email_vars[1:],
                       col_widths=[4.5*cm, 2.5*cm, CONTENT_W - 7*cm]))

    story.append(PageBreak())

    # ── 6. OPERATIONAL REFERENCE ───────────────────────────────────────────────
    story.append(Paragraph("6. Operational Reference", S["h1"]))
    story.append(section_rule())

    story.append(Paragraph("Run and job statuses", S["h2"]))

    story.append(Paragraph("<b>Run statuses</b>", S["h3"]))
    run_statuses = [
        ["Status", "Meaning"],
        ["pending",                 "Run created; background task not yet started."],
        ["running",                 "At least one step is executing."],
        ["completed",               "All steps finished within the error threshold."],
        ["completed_with_errors",   "Finished, but a step exceeded its threshold and abort was off."],
        ["failed",                  "Unrecoverable error outside step-level error accounting (e.g. auth failure)."],
        ["aborted",                 "Stopped by user request, or step exceeded threshold with abort-on-step-failure on."],
    ]
    story.append(table(run_statuses[0], run_statuses[1:],
                       col_widths=[4*cm, CONTENT_W - 4*cm]))
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("<b>Job statuses</b>", S["h3"]))
    job_statuses = [
        ["Status", "Meaning"],
        ["pending",          "Job created locally; not yet submitted to Salesforce."],
        ["uploading",        "CSV is being streamed to the Bulk API."],
        ["upload_complete",  "Upload done; waiting for Salesforce to begin processing."],
        ["in_progress",      "Salesforce is processing the batch."],
        ["job_complete",     "Salesforce finished — success/error CSVs downloaded."],
        ["failed",           "Job failed or poll timeout exceeded."],
        ["aborted",          "Job was aborted (user or error threshold)."],
    ]
    story.append(table(job_statuses[0], job_statuses[1:],
                       col_widths=[3.5*cm, CONTENT_W - 3.5*cm]))

    story.append(Paragraph("Monitoring runs", S["h2"]))
    story += screenshot("screenshot-runs.png",
                        "Figure 6: Runs list — filter by plan, status, and date range.")
    story.append(Paragraph(
        "The Runs list supports filtering by plan, status, and date range. "
        "Click any run to open its detail page. The detail page shows:",
        S["body"]
    ))
    run_panels = [
        "<b>Summary bar</b> — run status, total records, successes, errors, elapsed time.",
        "<b>Steps panel</b> — each step with its operation, status, job count, and partition-level progress bar.",
        "<b>Retry button</b> — appears on steps that ended with errors; re-submits only the failed rows.",
        "<b>Drill-in arrow</b> — opens the individual job view with raw Salesforce payload and result file links.",
    ]
    for p in run_panels:
        story.append(Paragraph(p, S["bullet"]))

    story.append(Paragraph("Result files", S["h2"]))
    story.append(Paragraph(
        "After each step completes, the loader writes three CSV files per partition "
        "to the output sink:",
        S["body"]
    ))
    result_files = [
        ["File", "Contents"],
        ["successfulResults.csv",   "Rows that were processed successfully. Includes sf_id for insert operations."],
        ["failedResults.csv",       "Rows that failed. Includes sf_error_message."],
        ["unprocessedRecords.csv",  "Rows not attempted (e.g. because the run was aborted before this partition ran)."],
    ]
    story.append(table(result_files[0], result_files[1:],
                       col_widths=[5*cm, CONTENT_W - 5*cm]))
    story.append(Paragraph(
        "A <code>logs.zip</code> for the entire run is also written, bundling the "
        "structured JSON log lines for all jobs.",
        S["body"]
    ))

    story.append(Paragraph("Files pane", S["h2"]))
    story += screenshot("screenshot-files.png",
                        "Figure 7: Files pane — browse input CSVs and download output result files.")
    story.append(Paragraph(
        "The <b>Files</b> page lets operators browse the input directory and download "
        "output result files without SSH access to the server. Select the source "
        "(local input, local output, or an S3 connection) from the dropdown. "
        "CSV files can be previewed inline.",
        S["body"]
    ))

    story.append(Paragraph("Observability", S["h2"]))
    story.append(Paragraph(
        "The backend emits structured JSON log lines for every significant event, "
        "all tagged with correlation IDs that tie a log line to a specific run, step, "
        "and job. The canonical event names are:",
        S["body"]
    ))
    events = [
        ["Event name", "When it fires"],
        ["run.started",          "A LoadRun transitions from pending to running."],
        ["run.completed",        "A run reaches a terminal status."],
        ["step.started",         "A step begins executing its partitions."],
        ["step.completed",       "All partitions in a step have reached a terminal state."],
        ["job.created",          "A Bulk API job is created in Salesforce."],
        ["job.upload_complete",  "CSV upload to Salesforce finished; job sent to InProgress."],
        ["job.completed",        "Bulk API job reached JobComplete; results downloaded."],
        ["job.failed",           "Bulk API job failed or poll timeout exceeded."],
    ]
    story.append(table(events[0], events[1:],
                       col_widths=[5.5*cm, CONTENT_W - 5.5*cm]))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "Prometheus metrics are exposed at <code>/metrics</code>. Optional "
        "OpenTelemetry spans and Sentry error reporting can be enabled — see "
        "<code>docs/observability.md</code> for the full taxonomy and DoD checklist.",
        S["body"]
    ))

    story.append(Paragraph("Administration", S["h2"]))
    story.append(Paragraph(
        "If no admin can log in (forgotten password, locked account, missing email backend) "
        "the backend includes a break-glass CLI:",
        S["body"]
    ))
    story.append(Paragraph(
        "# Reset a user's password\n"
        "docker compose exec backend python -m app.cli admin-recover admin@example.com\n\n"
        "# Unlock a locked account\n"
        "docker compose exec backend python -m app.cli unlock user@example.com\n\n"
        "# List all admin accounts\n"
        "docker compose exec backend python -m app.cli list-admins",
        S["code"]
    ))
    story.append(callout(
        "<b>Security note.</b> The admin-recover command resets the password to a random "
        "value printed to stdout. Run it only from a secure shell session. "
        "See docs/usage/admin-recovery.md for the full exit-code reference.",
        color=colors.HexColor("#FFF7ED"),
        border=AMBER,
    ))

    return story


# ── Document assembly ──────────────────────────────────────────────────────────

def build():
    doc = BaseDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=1.6 * cm,
        bottomMargin=2 * cm,
        title="Salesforce Bulk Loader — Reference Guide",
        author="Salesforce Bulk Loader",
        subject="Project documentation",
    )

    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, leftPadding=0, rightPadding=0,
                        topPadding=0, bottomPadding=0, id="cover")
    body_frame  = Frame(MARGIN, 2 * cm, CONTENT_W, PAGE_H - 3.8 * cm, id="body")

    cover_tpl = PageTemplate(id="cover", frames=[cover_frame], onPage=_cover_page)
    body_tpl  = PageTemplate(id="body",  frames=[body_frame],  onPage=_header_footer)
    doc.addPageTemplates([cover_tpl, body_tpl])

    story = build_story()
    # Switch to body template after the cover PageBreak
    from reportlab.platypus import NextPageTemplate
    story.insert(0, NextPageTemplate("cover"))
    # Insert before the first PageBreak so the body template takes effect on page 2.
    # NextPageTemplate affects the template used after the *next* page break, so
    # it must precede the break, not follow it.
    for i, el in enumerate(story):
        if isinstance(el, PageBreak):
            story.insert(i, NextPageTemplate("body"))
            break

    doc.build(story)
    print(f"PDF written to {OUTPUT}  ({OUTPUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build()
