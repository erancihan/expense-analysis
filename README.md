# Expense Analysis

A personal expense tracker that parses İşbank credit card statement PDFs, categorizes transactions with a rule-based tagging engine, and visualizes spending through a web dashboard.

## Features

- **PDF Parsing** — Extracts transactions from İşbank HTML-formatted PDF statements using positional layout analysis
- **Gmail Integration** — Automatically downloads statement PDFs from email
- **Auto-Tagging** — Rule-based categorization engine with priority-based pattern matching (contains, starts_with, regex)
- **Web Dashboard** — Single-page app with ECharts graphs, filterable expense table, and tag management
- **Migration System** — Alembic-managed schema migrations with autogenerate support

## Project Structure

```
expense-analysis/
├── src/                    # Library code (no executables)
│   ├── models.py           # SQLAlchemy ORM models
│   ├── database.py         # Engine & session factory
│   ├── web.py              # Flask app & REST API
│   ├── import_pdfs.py      # Bulk PDF import logic
│   ├── tag_engine.py       # TagEngine class (rule matching)
│   ├── gmail_client.py     # Gmail API client
│   ├── processor.py        # Email processing pipeline
│   ├── config.py           # Application configuration
│   └── parsers/
│       ├── base.py         # Parser interface
│       └── isbank.py       # İşbank statement parser
│
├── scripts/                # Executable entry points
│   ├── web.py              # Start the web server
│   ├── import_pdfs.py      # Import PDFs into DB
│   ├── download_pdfs.py    # Download PDFs from Gmail
│   ├── retag.py            # Re-tag expenses / show stats
│   ├── seed.py             # Seed tags & rules from YAML
│   ├── add_tag.py          # CLI to add tags & rules
│   ├── parse_pdf.py        # Debug-parse a single PDF
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
├── static/
│   └── index.html          # Web dashboard (Alpine.js, ECharts, TailwindCSS)
│
├── tests/                  # Test files
├── secrets/                # Gmail OAuth credentials (gitignored)
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

# 7. Start the web UI
make web
```

The web dashboard will be available at `http://127.0.0.1:5000`.

### Returning Use

```bash
# Apply any new migrations
make migrations

# Fetch new PDFs & import
make download-pdfs
make import-pdfs

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

### Import & Tagging

| Command | Description |
|---|---|
| `make download-pdfs` | Download PDF attachments from Gmail |
| `make import-pdfs` | Import all PDFs from `data/pdfs/` |
| `make import-pdfs-clean` | Clear DB and re-import all PDFs |
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
| `make parse-pdf PDF=path/to/file.pdf` | Debug-parse a single PDF |
| `make test` | Run tests |
| `make clean` | Remove venv and cache files |

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

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/expenses` | Paginated expenses with filters |
| GET | `/api/summary/monthly` | Monthly spending aggregation by tag |
| GET | `/api/tags` | All tags |
| POST | `/api/tags` | Create/update a tag |
| DELETE | `/api/tags/:id` | Delete a non-default tag |
| GET | `/api/tag-rules` | All tag rules |
| POST | `/api/tag-rules` | Create a tag rule |
| DELETE | `/api/tag-rules/:id` | Delete a non-default rule |
| POST | `/api/expenses/:id/tags` | Manually set tags on an expense |
| POST | `/api/retag` | Re-tag all expenses |
| GET | `/api/cards` | List cards with expense counts |

### Query Parameters (GET /api/expenses)

| Param | Description |
|---|---|
| `page` | Page number (default: 1) |
| `per_page` | Items per page (default: 50, max: 200) |
| `from` | Start month filter (YYYY-MM) |
| `to` | End month filter (YYYY-MM) |
| `tag` | Filter by tag name |
| `card` | Filter by card number |
| `search` | Search in descriptions |
