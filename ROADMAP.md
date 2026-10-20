# Expense Analysis — Roadmap

Personal İşbank credit-card expense tracker: Gmail → PDF parse → SQLite → rule-based
tagging → Flask/Alpine/ECharts dashboard.

**Target architecture:** multi-tenant, internet-facing self-hosted deployment where
each user has isolated expenses and connects their own Gmail.

> Supersedes the old `HANDOVER.md` and `plan.txt`. The statement-month view +
> per-card filtering + configurable cutoff day described in the former `plan.txt`
> are **implemented** (see `static/app.js`: `statementMonth`, `statementCutoffDay`,
> `actualDateFrom/To`, `filterCard`).

---

## Status

- [x] **Phase 1 — Stabilize the WIP** *(done 2026-06-26)*
- [x] **Phase 0 — Hygiene** *(done 2026-06-26)*
- [x] **Phase 2 — Full multi-user + security** *(2a auth+isolation, 2b hardening, 2c per-user Gmail — all done)*
- [x] **Phase 3 — Tests & CI** *(parser/tag-engine/auth tests done; CI green on GitHub since 2026-08-05; fixture-based parser integration test added 2026-08-05)*
- [~] **Phase 4 — Features** *(CSV export, budgets, cross-currency done; strict-CSP + extra bank parsers remain)*
- [~] **Phase 5 — Docs accuracy** *(README synced to code 2026-08-05; ROADMAP audit ongoing)*
- [~] **Phase 6 — Insights** *(foundation code in progress; see the Phase 6 section)*

---

## Phase 0 — Hygiene

- [x] Delete dead `static/index.html` (orphaned pre-refactor monolith; `/` renders
      `templates/index.html`).
- [x] Delete dev cruft: `patch.py` (one-off, already run), `test.js`, `raw_pdf.txt`
      (debug dump — **contained PII**), `test_isbank.py` (debug runner, superseded by
      `scripts/parse_pdf.py`).
- [x] Consolidate `HANDOVER.md` + `plan.txt` into this file.
- [—] **Follow-up — closed, risk accepted (2026-08-05).** `raw_pdf.txt` held a real name +
      home address. Two corrections to what an earlier revision of this file claimed:
      it is **not** in this repo's history (verified: `git log --all -- '*raw_pdf*'` is
      empty), and the repo it *is* in — the former parent, `erancihan/erancihan`, at
      `playground/python/expense-analysis/raw_pdf.txt` (added `bd8a3f7`, in-tree copy
      removed `5e2cd51`) — is already **public**, so the old "do only if this repo is/will
      be public" condition had silently been met. A `git filter-repo` scrub of the parent
      would rewrite every later hash and break its submodule pointers; the exposure was
      knowingly accepted instead. Not an open task — do not re-open it as one.

## Phase 1 — Stabilize the WIP ✅

- [x] `src/ingest.py`: one `ingest_transactions()` shared by the batch importer and the
      Gmail processor (dedup by fingerprint, skip non-monetary lines, stamp
      `statement_period`).
- [x] `processor.py` routes through the shared helper → `make process` expenses now get
      `statement_period` + dedup + auto-tagging (were previously invisible to the chart).
- [x] `import_pdfs.py`: fixed silent data loss for multi-card emails; `processed_emails`
      upserted once per email (SUCCESS > FAILED), fixing a latent duplicate-key crash.
- [x] `database.py` sources `DATABASE_URL` from `src.config` (honours `config.local.yaml`
      everywhere, incl. Alembic).
- [x] `web.py`: removed duplicated card filter in `api_expenses`.
- [x] Tests: `test_ingest.py`, `test_import_pdfs.py` (multi-card regression). 14 passing.

## Phase 2 — Full multi-user + security (next)

The largest phase. Internet-facing, so security is in scope from the start.

**Decisions taken** (2026-06-26): password accounts · per-user default tags (seeded on
signup) · invite/admin-only registration.

**2a-i. Auth gate** ✅ *(done 2026-06-26)*
- [x] `User` model (email, `password_hash` via werkzeug, `is_admin`, `is_active`, timestamps)
      + `users` migration.
- [x] Flask-Login; `login` / `logout` routes + login page + header logout button.
- [x] Fail-closed gate: `before_request` requires auth for every endpoint except an
      explicit allowlist (`auth.login`, `static`); 401 for `/api/*`, redirect for pages.
- [x] Admin-only account creation (`scripts/create_user.py`, `make create-user`); no public
      sign-up. Open-redirect guard on `?next=`.
- [x] `SECRET_KEY` + `HttpOnly`/`SameSite=Lax`/`Secure` session cookies from config.
- [x] Tests: `test_auth.py` (gate, login/logout, inactive user, bad password, open-redirect).

**2a-ii. Data isolation** ✅ *(done 2026-06-26)*
- [x] `user_id` FK on `Expense`, `Tag`, `TagRule`, `ProcessedEmail`; tag names unique
      per-user. Alembic data-migration adds nullable columns → backfills to an owner
      (auto-creates a `legacy@local` admin if pre-existing data has no user) → sets
      non-null (SQLite batch mode). Verified on a legacy data upgrade.
- [x] Every `web.py` query scoped by `current_user.id`; cross-user reads/writes/deletes
      return 404 (incl. an `_owns_expense` guard on the tag endpoints).
- [x] Per-user `TagEngine` (rules/retag/stats scoped) — closes the "one user's rules tag
      another's expenses" leak.
- [x] `user_id` threaded through ingestion (`ingest`, `import_pdfs`, `processor`) and CLI
      tools (`seed`, `add_tag`, `retag`, `backup_tags`, `import_pdfs`) via
      `resolve_owner(db, --email)` (defaults to the sole/first admin).
- [x] Per-user default tags seeded on account creation (`create_user.py`).
- [x] Isolation tests (web boundary + ingestion dedup separation). Full suite: 30 passing.

**2b. Security hardening** ✅ *(done 2026-06-26)*
- [x] CSRF protection on all mutating requests (Flask-WTF); SPA sends the token via an
      `X-CSRFToken` header (auto-injected by a `fetch` wrapper), forms via a hidden field.
      Verified end-to-end with protection on (tokenless POST → 400).
- [x] `Content-Security-Policy` header (restricts sources; `frame-ancestors`/`base-uri`/
      `form-action` locked down) + existing `X-Frame-Options`/`nosniff`/`Referrer-Policy`.
- [x] Login rate-limiting (Flask-Limiter, `login_ratelimit`, default 10/min).
- [x] `Secure`/`HttpOnly`/`SameSite=Lax` cookies; `SECRET_KEY` from env/config with a
      dev-default warning; `ProxyFix` (`trusted_proxies`) for correct IP/scheme behind a proxy.
- [x] HTTPS reverse-proxy deployment documented (README "Deploying").
- [ ] **Follow-up:** strict nonce-based CSP needs vendoring Tailwind (built CSS) + Alpine's
      CSP build to drop `'unsafe-inline'`/`'unsafe-eval'`; SRI/version-pin the jsDelivr
      scripts. (Tailwind Play CDN can't take SRI.) Tracked for Phase 4.

**2c. Per-user Gmail** ✅ *(done 2026-06-26)*
- [x] `User.gmail_token` column (+ migration) stores each user's OAuth token.
- [x] Web OAuth flow: `/gmail/connect` → Google consent → `/gmail/callback` stores the
      token; `/gmail/disconnect` clears it; `/api/gmail/status` drives the UI. State
      param guards the callback; works for a headless server (the CLI desktop flow can't).
- [x] `GmailClient.from_token_json()` builds a per-user client and refreshes/persists the
      token; the processor iterates connected users (each user's Gmail → their account),
      falling back to the legacy global-token single-account mode when none are connected.
- [x] Settings UI: Connect/Disconnect (hidden until an OAuth client is configured).
- [x] Google Cloud *Web client* setup documented (README). Tests mock the Google libs
      (status/connect/callback/disconnect + processor iteration). Full suite: 69 passing.
- [x] *Follow-up done (2026-08-06):* `gmail_token` is encrypted at rest with Fernet
      under a dedicated `token_encryption_key` (`src/token_crypto.py` +
      `EncryptedText` in `src/models.py`). No schema migration was needed — the
      column was already `TEXT`. Connect is gated on a key being configured, so a
      token can never be stored unencrypted. Reads that fail to decrypt log and
      return `None` rather than raising, because Flask-Login loads a `User` on
      every request and a raising column type would 500 the whole app.

### Decisions (resolved 2026-06-26)
- **Auth:** password accounts (werkzeug hashing + Flask-Login sessions).
- **Default tags:** per-user, seeded on signup → `Tag`/`TagRule` get a `user_id` in 2a-ii.
- **Registration:** invite/admin-only; initial admin via `scripts/create_user.py`.

## Phase 3 — Tests & CI ✅ *(done 2026-08-05)*

- [x] Unit tests for the İşbank parser pure functions (Turkish number normalisation,
      amount/date patterns, card-metadata extraction, position matching).
- [x] Tag-engine tests (match types, priority, user-rule override, suppression, and
      per-user scoping/isolation).
- [x] API/auth tests with isolation + CSRF/rate-limit (in `test_auth.py`).
- [x] CI workflow (`.github/workflows/tests.yml`) — runs the suite and a fresh-DB migration
      check on every push to `master` and every PR. **76 tests.** *(Added 2026-08-05. A
      previous revision of this file marked CI as done while no workflow existed in either
      repo — and even once the file existed it had never executed, because the branch
      carrying it was pushed without a PR. First green runs: PR #1 and the `master` push
      that merged it, both 2026-08-05. Verified with `gh run list --workflow=tests`, not
      by the file's presence.)*
- [—] *Optional:* `SessionStart` hook to auto-create the venv for Claude-on-web sessions —
      **not pursued**: development happens in a local terminal, where the hook is inert.
- [x] Full `extract_transactions` integration test *(2026-08-05)* — five tests in
      `tests/test_isbank_integration.py` run the real `pdfminer` layout path against a
      PDF generated at test time by `tests/fixtures/isbank_pdf.py`. No sample statement
      is checked in: real statements carry a name and address, which is how `raw_pdf.txt`
      reached the former parent repo's public history. Covers card metadata, amount
      matching by row position, the `amount=None` path, description continuation, the
      `TOPLAM` stop marker, and a missing `TUTAR` header.

## Phase 4 — Features *(in progress)*

- [x] **CSV export** — `GET /api/expenses/export.csv` (per-user, respects the current
      from/to/card/tag/search filters via the shared `_filtered_user_expenses` helper) +
      an Export button. Formula-injection-safe text fields.
- [x] **Budgets** — `Budget` model (+ migration) for per-tag and overall monthly limits;
      CRUD API (`/api/budgets`, per-user scoped, upsert) + a dashboard card showing
      spent/limit progress for the selected statement period (green/amber/red).
- [x] **Cross-currency totals** — configurable `exchange_rates` (TRY-equivalent); the
      monthly summary returns `converted_total` per period (TRY at 1.0 + foreign at its
      rate; unrated currencies excluded), surfaced as an "≈ ₺ incl. FX" sub-stat.
- [ ] Additional bank parsers — needs a sample statement from another bank (`BANK_CONFIG`
      already supports the plug-in shape).
- [ ] Strict nonce-based CSP (2b follow-up) — deferred: needs vendored Tailwind + Alpine
      CSP build (large/risky frontend refactor).
- [x] **Resolved:** the old `plan.txt` chart-period question — charts already group by
      `statement_period` (the PDF's billing cycle), which is the desired behaviour.

## Phase 5 — Docs accuracy *(in progress)*

Added after an audit found this file and the README both asserting things the code
didn't do. Claims here are only marked done once checked against the code, a test
run, or `gh` — never against another document.

- [x] **README synced to the code** *(2026-08-05)* — the structure tree still pointed at
      `static/index.html`, deleted back in Phase 0, and never mentioned `templates/`;
      six `src/` modules and two scripts were missing; the API table listed 11 of 25
      routes. Also corrected: `from`/`to` accept `YYYY-MM-DD` as well as `YYYY-MM` and
      apply to `export.csv` too, `/api/summary/monthly` filters statement periods rather
      than transaction dates, card numbers come back masked, the `suppressed` tag source
      was undocumented, and the deploy checklist told you to run gunicorn without noting
      it is absent from `requirements.txt`.
- [x] **Decision — `is_default` stays advisory** *(2026-08-05)* — the README had claimed
      tag/rule `DELETE` refuses defaults. It does not: `api_delete_tag` (`src/web.py`) and
      `api_delete_tag_rule` filter on `user_id` alone, and the flag is only ever written
      as `False` on create and echoed in `to_dict()`. Being able to prune unwanted seeded
      tags is intended, so the flag is display-only by design and no guard was added. The
      README documents the gap explicitly. Don't "fix" this without a new decision.
- [ ] Audit the rest of this file the same way — Phases 1/2 are still marked done on the
      strength of their own prose. The code and tests for them do exist, but the
      individual sub-claims have not been checked line by line.

## Phase 6 — Insights *(in progress)*

Graphs and insights about spending habits on a separate `/insights` page,
computed at request time (nothing derived from a user's data enters this
public repo). Five sub-projects, each with its own spec → plan → build cycle:
0 foundation, 1 page shell + "Am I spending more?", 2 "What's locked in?",
3 "Where does it go?", 4 "What's unusual?".

**0 — Foundation**
- [ ] Parser reads the closing date from the label's own row (it took the first
      date in the text, which filed some 2021-era statements a month late) and
      the TAKSİT BİLGİSİ installment column.
- [ ] `statements` table; `expenses.statement_id` + `installment_*`; `rates` table.
- [ ] Backfill applied to the real data in place (no insert/delete/retag; tag
      sets verified identical).
- [ ] `src/analytics/`: months, coverage, merchant normalization, rates, `load_spend`.
- [ ] `make fetch-rates` run; CPI live-checked once an EVDS key exists.

Mark items done only after checking them against the code, a test run, or the
backfill report — not against this file.
