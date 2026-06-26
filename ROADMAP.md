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
- [~] **Phase 2 — Full multi-user + security** *(in progress: auth gate landed; data isolation next)*
- [ ] **Phase 3 — Tests & CI**
- [ ] **Phase 4 — Features**

---

## Phase 0 — Hygiene

- [x] Delete dead `static/index.html` (orphaned pre-refactor monolith; `/` renders
      `templates/index.html`).
- [x] Delete dev cruft: `patch.py` (one-off, already run), `test.js`, `raw_pdf.txt`
      (debug dump — **contained PII**), `test_isbank.py` (debug runner, superseded by
      `scripts/parse_pdf.py`).
- [x] Consolidate `HANDOVER.md` + `plan.txt` into this file.
- [ ] **Follow-up (needs decision):** `raw_pdf.txt` held a real name + home address and
      remains in git history. A history scrub (e.g. `git filter-repo`) would remove it
      but rewrites hashes — do only if this repo is/will be public.

> Note: the `.githooks/`, `.github/workflows/update-submodules.yml` and `tmux-wsl-info`
> files that appear "deleted" in `git diff master..feat/expense-analysis-user-login` were
> **never deleted** by this work — the feature branch was simply cut from `master` before
> those commits landed. They return automatically when this work merges to `master`.

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

**2a-ii. Data isolation** *(next)*
- [ ] Add `user_id` FK to `Expense`, `Tag`, `TagRule`, `ProcessedEmail`. Alembic
      data-migration: add nullable columns → backfill existing rows to the first admin →
      set non-null (batch mode for SQLite).
- [ ] **Scope every query by `current_user.id`** across all `web.py` endpoints
      (the bulk of the work and the main correctness risk).
- [ ] Thread `user_id` through ingestion (`ingest.py`, `import_pdfs.py`, `processor.py`,
      CLI scripts) — imports attribute to the owning user.
- [ ] Seed per-user default tags on account creation. Isolation tests (user A can't see
      user B's data).

**2b. Security hardening**
- [ ] CSRF tokens on all mutating endpoints (none today).
- [ ] CSP with nonces (`web.py` TODO) + SRI/pin the Tailwind/ECharts/Alpine CDNs (or vendor).
- [ ] Login rate-limiting; `Secure`/`HttpOnly`/`SameSite` cookies; `SECRET_KEY` + OAuth
      client secret from env.
- [ ] Document HTTPS reverse-proxy (Caddy/nginx) deployment.

**2c. Per-user Gmail**
- [ ] Per-user OAuth connect + per-user token storage (today `GmailClient` uses global
      `secrets/*.json`); processor/download iterate users. Can ship after 2a/2b.

### Decisions (resolved 2026-06-26)
- **Auth:** password accounts (werkzeug hashing + Flask-Login sessions).
- **Default tags:** per-user, seeded on signup → `Tag`/`TagRule` get a `user_id` in 2a-ii.
- **Registration:** invite/admin-only; initial admin via `scripts/create_user.py`.

## Phase 3 — Tests & CI

- [ ] Unit tests for the İşbank parser (text/PDF fixtures — highest-value, least-tested
      code), tag engine, and API endpoints (with auth).
- [ ] CI workflow + `SessionStart` hook running tests/lint on web sessions and PRs.

## Phase 4 — Features

- [ ] **Open product question (from old `plan.txt`):** should the summary charts align to
      the custom statement period (cutoff day) instead of raw calendar month? Currently
      grouped by `statement_period` (the PDF's billing cycle).
- [ ] CSV/Excel export, budgets/alerts, cross-currency totals, additional bank parsers
      (`BANK_CONFIG` already supports the plug-in shape).
