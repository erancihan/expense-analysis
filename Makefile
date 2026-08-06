VENV := venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
ALEMBIC := $(VENV)/bin/alembic

.PHONY: install install-dev web process import-pdfs retag seed backup-tags refresh-db migrations create-user clean

## Create virtual environment (only if it doesn't exist)
$(VENV):
	python -m venv $(VENV)

## Install dependencies
install: venv
	$(PIP) install -r requirements.txt

## Install dependencies including test-only ones
install-dev: venv
	$(PIP) install -r requirements-dev.txt

## ── Application ─────────────────────────────────────────────────

## Start the web UI
web: install
	$(PYTHON) scripts/web.py

## Run the Gmail expense processor (scheduled)
process: install
	$(PYTHON) scripts/process.py

## Run the Gmail processor in manual mode
process-manual: install
	$(PYTHON) scripts/process.py --manual

## ── Database ─────────────────────────────────────────────────────

## Run pending Alembic migrations
migrations: install
	$(ALEMBIC) upgrade head

## Generate a new migration from model changes (usage: make migrations-generate MSG="add foo column")
migrations-generate: install
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

## Show current migration status
migrations-status: install
	$(ALEMBIC) current
	$(ALEMBIC) history

## Seed default tags and rules from data/seeds/tags.yaml
seed: install
	$(PYTHON) scripts/seed.py

## Backup tags and rules from DB to data/seeds/tags.yaml
backup-tags: install
	$(PYTHON) scripts/backup_tags.py

## Full data refresh: snapshot tags → migrate → seed → download → re-import → retag
## Rebuilds all expense data in place. Your account, tags and rules survive: it
## no longer deletes the database file (that wiped the user and left you locked
## out, since there is no create-user step), and the tag snapshot no longer
## overwrites data/seeds/tags.yaml, which is the input this reseeds from.
refresh-db: install
	@echo "══════ Snapshotting current tags ══════"
	@echo "(timestamped — deliberately NOT data/seeds/tags.yaml, which is the input)"
	$(PYTHON) scripts/backup_tags.py -o data/seeds/tags.snapshot-$$(date +%Y%m%d-%H%M%S).yaml
	@echo "══════ Running migrations ══════"
	$(ALEMBIC) upgrade head
	@echo "══════ Reconciling tags & rules from data/seeds/tags.yaml ══════"
	$(PYTHON) scripts/seed.py
	@echo "══════ Downloading PDFs ══════"
	$(PYTHON) scripts/download_pdfs.py
	@echo "══════ Clearing expenses and re-importing ══════"
	$(PYTHON) scripts/import_pdfs.py --clear
	@echo "══════ Re-tagging ══════"
	$(PYTHON) scripts/retag.py
	@echo "══════ Done ══════"

## ── Import & Tagging ────────────────────────────────────────────

## Download PDF attachments from Gmail to data/pdfs/
download-pdfs: install
	$(PYTHON) scripts/download_pdfs.py

## Import all PDFs from data/pdfs/ into the database
import-pdfs: install
	$(PYTHON) scripts/import_pdfs.py

## Import all PDFs (clearing existing data first)
import-pdfs-clean: install
	$(PYTHON) scripts/import_pdfs.py --clear

## Re-tag all expenses using current rules
retag: install
	$(PYTHON) scripts/retag.py

## Show tagging statistics
tag-stats: install
	$(PYTHON) scripts/retag.py --stats

## Parse a single PDF for debugging (usage: make parse-pdf PDF=data/pdfs/foo.pdf)
parse-pdf: install
	$(PYTHON) scripts/parse_pdf.py $(PDF)

## ── Scripts ─────────────────────────────────────────────────────

## Add a tag (usage: make add-tag NAME=cafe ICON=☕ COLOR='#78716c' RULES="STARBUCKS,KRONOTROP")
add-tag: install
	@if [ -z "$(NAME)" ]; then echo "Usage: make add-tag NAME=cafe [ICON=☕] [COLOR='#hex'] [RULES='PAT1,PAT2']"; exit 1; fi
	@RULE_ARGS=""; \
	if [ -n "$(RULES)" ]; then \
		for r in $$(echo "$(RULES)" | tr ',' ' '); do \
			RULE_ARGS="$$RULE_ARGS --rule $$r"; \
		done; \
	fi; \
	$(PYTHON) scripts/add_tag.py $(NAME) \
		$$([ -n "$(COLOR)" ] && echo "--color $(COLOR)") \
		$$([ -n "$(ICON)" ] && echo "--icon $(ICON)") \
		$$RULE_ARGS

## Create (or reset) a user account (usage: make create-user EMAIL=you@example.com [ADMIN=1])
create-user: install
	@if [ -z "$(EMAIL)" ]; then echo "Usage: make create-user EMAIL=you@example.com [ADMIN=1]"; exit 1; fi
	$(PYTHON) scripts/create_user.py $(EMAIL) $$([ -n "$(ADMIN)" ] && echo "--admin")

## ── Testing ─────────────────────────────────────────────────────

## Run tests
test: install-dev
	$(PYTHON) -m unittest discover -s tests -v

## ── Cleanup ─────────────────────────────────────────────────────

## Remove virtual environment and cached files
clean:
	rm -rf $(VENV)
	find . -type d -name '__pycache__' -exec rm -rf {} +
