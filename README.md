# Expense Analysis

A personal expense tracker that parses İşbank credit card statement PDFs, categorizes transactions with a rule-based tagging engine, and visualizes spending through a web dashboard.

## Features

- **PDF Parsing** — Extracts transactions from İşbank HTML-formatted PDF statements using positional layout analysis
- **Gmail Integration** — Downloads statement PDFs from email, either from a single global account or per-user via in-app OAuth
- **Auto-Tagging** — Rule-based categorization engine with priority-based pattern matching (contains, starts_with, regex)
- **Web Dashboard** — Single-page app with ECharts graphs, filterable expense table, and tag management
- **Multi-User** — Password accounts with fully isolated expenses, tags, rules and budgets per user
- **Budgets** — Per-tag and overall monthly limits with spent/limit progress on the dashboard
- **CSV Export** — Downloads the current filtered view; text fields are formula-injection-safe
- **Cross-Currency Totals** — Configurable exchange rates produce a TRY-equivalent grand total alongside per-currency figures
- **Statements & Installments** — Every import records its statement (card + closing date) and the installment column (`n/N` and the full purchase amount), so missing statements are detectable and installment plans can be seen as one purchase
- **Rates for Analysis** — Monthly ECB exchange rates and TÜİK CPI (optional EVDS key) for comparing spending across years in today's lira or in USD
- **Security** — Fail-closed auth gate, CSRF protection, login rate limiting and security headers for internet-facing deployment
- **Migration System** — Alembic-managed schema migrations with autogenerate support

## Project Structure

```
expense-analysis/
├── src/                    # Library code (no executables)
│   ├── models.py           # SQLAlchemy ORM models (User, Expense, Tag, TagRule, Budget, …)
│   ├── database.py         # Engine & session factory (DATABASE_URL from src.config)
│   ├── web.py              # Flask app & REST API
│   ├── auth.py             # Login/logout routes, auth gate helpers
│   ├── extensions.py       # Unbound Flask extensions (CSRF, rate limiter)
│   ├── users.py            # Owner resolution for CLI / ingestion entry points
│   ├── seeding.py          # Per-user default tag & rule seeding
│   ├── ingest.py           # Shared transaction ingestion (dedup, statement_period)
│   ├── import_pdfs.py      # Bulk PDF import logic
│   ├── tag_engine.py       # TagEngine class (rule matching, per-user scoped)
│   ├── gmail_client.py     # Gmail API client (global token or per-user token JSON)
│   ├── gmail_oauth.py      # Web OAuth flow for connecting a user's Gmail
│   ├── processor.py        # Email processing pipeline (iterates connected users)
│   ├── backfill.py         # One-off: link existing expenses to statements, in place
│   ├── analytics/          # Spend dataset for insights
│   │   ├── dataset.py      # load_spend(): payments out, billed/purchased basis
│   │   ├── coverage.py     # Which months the imported statements fully cover
│   │   ├── rates.py        # Monthly FX + CPI, lens conversions
│   │   ├── merchant.py     # Merchant-name normalization
│   │   └── months.py       # YYYY-MM helpers
│   ├── config.py           # Application configuration
│   └── parsers/
│       ├── base.py         # Parser interface (BankParser)
│       ├── isbank.py       # İşbank statement parser
│       └── sample.py       # Stub showing the shape of a new bank parser
│
├── scripts/                # Executable entry points
│   ├── web.py              # Start the web server
│   ├── import_pdfs.py      # Import PDFs into DB
│   ├── download_pdfs.py    # Download PDFs from Gmail
│   ├── retag.py            # Re-tag expenses / show stats
│   ├── seed.py             # Seed tags & rules from YAML
│   ├── add_tag.py          # CLI to add tags & rules
│   ├── backup_tags.py      # Dump tags & rules back to YAML
│   ├── create_user.py      # Create (or reset) an account
│   ├── parse_pdf.py        # Debug-parse a single PDF
│   ├── backfill_statements.py  # Link expenses to statements (dry run by default)
│   ├── fetch_rates.py      # Fetch monthly FX / CPI into the rates table
│   └── process.py          # Gmail processor (scheduled)
│
├── data/
│   ├── seeds/              # Seed data (gitignored, except examples)
│   │   ├── tags.yaml.example
│   │   └── tags.yaml       # Your private merchant patterns
│   ├── pdfs/               # Downloaded PDF statements (gitignored)
│   └── expenses.db         # SQLite database (gitignored)
│
├── alembic/                # Alembic migration environment
│   ├── env.py              # Configured for our models
│   └── versions/           # Migration scripts (datetime-prefixed)
│
├── templates/              # Jinja2 templates
│   ├── index.html          # Dashboard shell (Alpine.js, ECharts, TailwindCSS)
│   ├── login.html          # Login page
│   └── partials/           # Header, stats, chart, expenses, budgets, modals
│
├── static/
│   └── app.js              # Dashboard front-end logic (Alpine component)
│
├── tests/                  # unittest suite
├── secrets/                # Gmail OAuth credentials & token (gitignored)
├── .github/workflows/      # CI: runs the suite + a fresh-DB migration check
├── Makefile                # All available commands
├── requirements.txt
├── alembic.ini
└── Dockerfile
```

## Getting Started

### Prerequisites

- Python 3.10+
- Gmail API credentials (for email integration)

### Fresh Setup

```bash
# 1. Install dependencies
make install

# 2. Set up seed data
cp data/seeds/tags.yaml.example data/seeds/tags.yaml
# Edit data/seeds/tags.yaml with your merchant patterns

# 3. Create the database schema
make migrations

# 4. Seed default tags & rules
make seed

# 5. Download PDFs from Gmail (requires OAuth credentials in secrets/)
make download-pdfs

# 6. Import PDFs into the database
make import-pdfs

# 7. Create your account (the dashboard requires login)
make create-user EMAIL=you@example.com ADMIN=1

# 8. Start the web UI
make web
```

The web dashboard will be available at `http://127.0.0.1:5000` and will prompt
you to sign in.

### Authentication

The dashboard and all `/api/*` routes require a logged-in session (Flask-Login).
There is no public sign-up — an admin provisions accounts:

```bash
make create-user EMAIL=you@example.com ADMIN=1     # prompts for a password
```

Each user has fully isolated data (expenses, tags, rules) and gets their own
copy of the default tags on creation. The CLI/ingestion tools attribute data to
the sole admin by default; with multiple users, pass `--email` to choose the
owner (e.g. `./scripts/import_pdfs.py --email you@example.com`,
`./scripts/retag.py --email …`).

Before deploying, set a strong `secret_key` (and `session_cookie_secure: true`
behind HTTPS) in `config.local.yaml` — see `config.local.yaml.example`.

### Deploying (internet-facing)

The app is built to sit behind an HTTPS reverse proxy (Caddy/nginx) with a
WSGI server (gunicorn). Security measures already in place:

- **Auth gate** — every route requires a session; admin-provisioned accounts only.
- **CSRF protection** — all state-changing requests need a token (sent via the
  `X-CSRFToken` header by the SPA, a hidden field by forms).
- **Login rate limiting** — `login_ratelimit` (default `10 per minute`).
- **Security headers** — `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`.

Checklist before exposing it:

1. Set a strong `secret_key` (`python -c "import secrets; print(secrets.token_hex(32))"`).
2. Terminate TLS at the proxy; set `session_cookie_secure: true` and
   `trusted_proxies: 1` so client IP / HTTPS are read from `X-Forwarded-*`.
3. Run under a WSGI server — `requirements.txt` doesn't include one, so install
   it yourself (`pip install gunicorn`) and serve with e.g.
   `gunicorn -w 2 'src.web:app'`. For more than one worker, point the rate
   limiter at redis; its default in-memory state isn't shared between workers.

> Note: the CSP currently allows `'unsafe-inline'`/`'unsafe-eval'` because the
> Tailwind Play CDN and Alpine.js evaluate at runtime. Vendoring Tailwind as a
> built stylesheet and using Alpine's CSP build would allow a strict policy
> (tracked in `ROADMAP.md`).

### Per-user Gmail (optional, for hosted multi-user)

Each user can connect their own Gmail from **Settings → Connect Gmail**; the
scheduled processor (`make process`) then imports each connected user's
statements into their own account. If no user has connected, the processor
falls back to the legacy single-account mode (global `secrets/token.json`),
attributing data to the admin.

To enable the in-app connect flow, create a **Web application** OAuth client
(not "Desktop") in Google Cloud:

1. Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID
   → *Web application*.
2. Add an authorized redirect URI: `https://YOUR_DOMAIN/gmail/callback`.
3. Download the JSON to `secrets/credentials.json`.
4. Enable the Gmail API for the project; add your users as test users (or
   publish the consent screen). Scope used: `gmail.readonly`.

The Connect button is hidden until both `secrets/credentials.json` exists and a
token encryption key is configured.

Stored tokens contain a Google refresh token, so they are encrypted at rest with
Fernet under a dedicated `token_encryption_key`. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set it as `token_encryption_key` in `config.local.yaml` (or `TOKEN_ENCRYPTION_KEY`
in the environment). It is deliberately separate from `secret_key` so rotating the
session secret does not invalidate every stored token.

Without a key, per-user Gmail connect is unavailable — the rest of the app is
unaffected. Lose the key and connected users simply reconnect; the app reports
them as disconnected rather than failing.

### Returning Use

```bash
# Apply any new migrations
make migrations

# Fetch new PDFs & import
make download-pdfs
make import-pdfs

# Refresh FX / CPI for the insights
make fetch-rates

# Start the web UI
make web
```

## Available Commands

### Application

| Command | Description |
|---|---|
| `make web` | Start the web server on port 5000 |
| `make process` | Run the Gmail processor with scheduler |
| `make process-manual` | Step through emails one by one |

### Database

| Command | Description |
|---|---|
| `make migrations` | Apply pending Alembic migrations |
| `make migrations-generate MSG="description"` | Auto-generate a migration from model changes |
| `make migrations-status` | Show current migration state and history |
| `make seed` | Seed tags & rules from `data/seeds/tags.yaml` |
| `make backup-tags` | Dump tags & rules from the DB back to `data/seeds/tags.yaml` |
| `make fetch-rates` | Fetch monthly FX (and CPI, with `evds_api_key`) into the `rates` table |
| `make refresh-db` | Full rebuild: backup tags → reset DB → migrate → seed → download → import → retag |

### Import & Tagging

| Command | Description |
|---|---|
| `make download-pdfs` | Download PDF attachments from Gmail |
| `make import-pdfs` | Import all PDFs from `data/pdfs/` |
| `make import-pdfs-clean` | Clear DB and re-import all PDFs |
| `make backfill-statements [APPLY=1]` | Link existing expenses to their statements and fill installment fields, in place. Dry run unless `APPLY=1`, which first backs up the DB and every tag set to `data/backups/` |
| `make retag` | Re-tag all expenses using current rules |
| `make tag-stats` | Show tagging coverage statistics |

### Tag Management

```bash
# Add a tag with rules via CLI
make add-tag NAME=cafe ICON=☕ COLOR='#78716c' RULES="STARBUCKS,KRONOTROP"

# Or use the script directly for more options
./scripts/add_tag.py grocery --rule "CARREFOUR" --rule "A101" --match-type contains --priority 150
```

### Utilities

| Command | Description |
|---|---|
| `make install` | Create the venv and install dependencies |
| `make create-user EMAIL=you@example.com [ADMIN=1]` | Create (or reset) an account; prompts for a password |
| `make parse-pdf PDF=path/to/file.pdf` | Debug-parse a single PDF |
| `make test` | Run the test suite |
| `make clean` | Remove venv and cache files |

## Tests

```bash
make test        # or: python -m unittest discover -s tests -v
```

CI (`.github/workflows/tests.yml`) runs the same suite on every push to `master`
and on every pull request, plus a check that the migrations apply cleanly to an
empty database. CI runs on Python 3.12.

## Tag System

Expenses are categorized using a rule-based engine. Rules match against expense descriptions and are applied by priority (highest first).

### Match Types

- **contains** — Pattern found anywhere in description (default)
- **starts_with** — Description starts with pattern
- **regex** — Full regex matching

### Seed File Format

`data/seeds/tags.yaml`:

```yaml
tags:
  - name: restaurant
    color: "#ef4444"
    icon: "🍽️"
  - name: grocery
    color: "#22c55e"
    icon: "🛒"

rules:
  - { tag: restaurant, pattern: "BURGER KING", match_type: contains, priority: 5 }
  - { tag: grocery,    pattern: "MIGROS",      match_type: contains, priority: 5 }
```

### Tag Sources

- **auto** — Applied automatically by the tag engine based on rules
- **manual** — Set by the user through the web UI (preserved during re-tagging)
- **suppressed** — Removed by the user; the link is kept so re-tagging won't put
  the tag back, and it's excluded from tag filters and totals

## API Endpoints

All `/api/*` routes require a logged-in session and only ever see the current
user's data. Mutating requests need a CSRF token.

### Expenses

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/expenses` | Paginated expenses with filters (card numbers masked to last 4) |
| GET | `/api/expenses/export.csv` | Download the filtered expenses as CSV |
| GET | `/api/summary/monthly` | Spending aggregated by statement period, with per-tag/currency/card breakdowns |
| GET | `/api/cards` | List cards with expense counts |
| GET | `/api/expenses/:id/details` | Expense detail, including which rules produced its auto-tags |

### Tags & rules

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/tags` | All tags |
| POST | `/api/tags` | Create a tag, or update the color/icon of one with that name |
| PUT | `/api/tags/:id` | Rename or restyle a tag |
| DELETE | `/api/tags/:id` | Delete a tag |
| POST | `/api/tags/:id/merge` | Merge a tag into `target_tag_id` |
| GET | `/api/tag-rules` | All tag rules |
| POST | `/api/tag-rules` | Create a tag rule |
| DELETE | `/api/tag-rules/:id` | Delete a tag rule |
| POST | `/api/retag` | Re-tag all expenses |

Tags and rules carry an `is_default` flag marking the ones seeded on signup. It
is reported in the API but not enforced — a default tag or rule can be deleted
like any other.

### Tags on an expense

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/expenses/:id/tags` | Manually set tags on an expense |
| DELETE | `/api/expenses/:id/tags/:tag_id` | Suppress a tag so re-tagging won't re-apply it |
| POST | `/api/expenses/:id/tags/:tag_id/restore` | Restore a suppressed tag as manual |
| DELETE | `/api/expenses/:id/tags/:tag_id/hard` | Permanently remove the tag link |

### Budgets

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/budgets` | List budgets (the overall budget first) |
| POST | `/api/budgets` | Upsert a budget — `{tag_id, amount}`; `tag_id: null` means overall |
| DELETE | `/api/budgets/:id` | Delete a budget |

### Gmail connection

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/gmail/status` | Whether this user is connected, and whether OAuth is configured at all |
| GET | `/gmail/connect` | Redirect into Google's consent screen |
| GET | `/gmail/callback` | OAuth redirect target; stores the token |
| POST | `/gmail/disconnect` | Clear this user's stored token |

### Query Parameters (GET /api/expenses and /api/expenses/export.csv)

| Param | Description |
|---|---|
| `page` | Page number (default: 1) — list endpoint only |
| `per_page` | Items per page (default: 50, max: 200) — list endpoint only |
| `from` | Start date filter, `YYYY-MM` or `YYYY-MM-DD` (inclusive) |
| `to` | End date filter, `YYYY-MM` or `YYYY-MM-DD` (inclusive) |
| `tag` | Filter by tag name (suppressed tags don't count as a match) |
| `card` | Filter by full card number |
| `search` | Substring search in descriptions (max 200 chars) |

`GET /api/summary/monthly` takes its own `from`/`to`, which filter **statement
periods** (`YYYY-MM`) rather than transaction dates.
