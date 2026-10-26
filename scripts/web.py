#!/usr/bin/env python3
"""
Start the Expense Analysis web server (development).

Prints a compact status block instead of Flask's seven-line banner, then one
short line per request. Colour is used only when stdout is a TTY and NO_COLOR
is unset, so piping to a file or a CI log stays plain.

Everything here applies to the dev server only — the hooks are registered on
`app` from this entrypoint, not in src/web.py, so tests and any WSGI deployment
are untouched.

Usage:
    ./scripts/web.py                     # 127.0.0.1:5000
    ./scripts/web.py --port 8080
    NO_COLOR=1 ./scripts/web.py          # plain output
"""

import argparse
import logging
import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The dev server is single-process, so in-memory rate-limit storage is exactly
# right here and the warning is pure noise on every start. Must be filtered
# before src.web imports flask_limiter.
warnings.filterwarnings(
    'ignore', message='Using the in-memory storage for tracking rate limits')

# ── Colour ───────────────────────────────────────────────────────────────────

USE_COLOR = sys.stdout.isatty() and not os.environ.get('NO_COLOR')

DIM, BOLD, RESET = '\033[2m', '\033[1m', '\033[0m'
CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA = (
    '\033[36m', '\033[32m', '\033[33m', '\033[31m', '\033[34m', '\033[35m')


def c(text: str, *codes: str) -> str:
    if not USE_COLOR or not codes:
        return text
    return ''.join(codes) + text + RESET


_LEVEL_COLOR = {
    'DEBUG': (DIM,),
    'INFO': (CYAN,),
    'WARNING': (YELLOW,),
    'ERROR': (RED, BOLD),
    'CRITICAL': (RED, BOLD),
}


class CompactFormatter(logging.Formatter):
    """`12:04:31 WARN  web  message` — the old format spent 35 columns on the
    logger name alone, and every logger here starts with `src.`."""

    _SHORT = {'WARNING': 'WARN', 'CRITICAL': 'CRIT', 'ERROR': 'ERR '}

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime('%H:%M:%S', time.localtime(record.created))
        level = self._SHORT.get(record.levelname, record.levelname)
        name = record.name
        for prefix in ('src.', 'scripts.', '__main__'):
            if name.startswith(prefix):
                name = name[len(prefix):] or 'web'
        text = record.getMessage()
        if record.exc_info:
            text += '\n' + self.formatException(record.exc_info)
        return (f"{c(stamp, DIM)} {c(level.ljust(5), *_LEVEL_COLOR.get(record.levelname, ()))} "
                f"{c(name.ljust(12), DIM)} {text}")


IS_RELOADER_CHILD = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CompactFormatter())
    root = logging.getLogger()
    root.handlers = [handler]

    # With the reloader on, this module is imported by both the supervisor and
    # the child that serves, so every import-time log line lands twice — the
    # SECRET_KEY warning from src.config among them. The supervisor's only INFO
    # traffic is werkzeug's restart chatter, which is silenced below anyway, so
    # muting it there costs nothing and halves the startup noise.
    root.setLevel(logging.INFO if IS_RELOADER_CHILD or '--no-debug' in sys.argv
                  else logging.ERROR)

    # Werkzeug logs its whole startup block *and* every request at INFO through
    # this logger; the compact banner and the after_request hook below replace
    # both. WARNING keeps genuine server errors visible.
    logging.getLogger('werkzeug').setLevel(logging.WARNING)


# Logging is configured before importing src, because src.config warns about a
# missing SECRET_KEY at import time and that line should be formatted like the
# rest.
_setup_logging()

from flask import g, request           # noqa: E402
import flask.cli                       # noqa: E402

from src import config                 # noqa: E402
from src.web import app                # noqa: E402


# ── Request log ──────────────────────────────────────────────────────────────

_STATUS_COLOR = [(300, GREEN), (400, CYAN), (500, YELLOW)]


def _status_color(code: int) -> str:
    for ceiling, colour in _STATUS_COLOR:
        if code < ceiling:
            return colour
    return RED


def install_request_log() -> None:
    def _mark_start():
        g._started = time.perf_counter()

    # Must run before src.web's require_login: a before_request that returns a
    # response short-circuits the rest, so appending this normally left every
    # redirect and 401 without a timing.
    app.before_request_funcs.setdefault(None, []).insert(0, _mark_start)

    @app.after_request
    def _log_request(response):
        started = getattr(g, '_started', None)
        ms = f"{(time.perf_counter() - started) * 1000:.0f}ms" if started else '-'
        path = request.full_path.rstrip('?') or request.path
        # Static assets are noise once the page is up; keep them dim rather than
        # dropping them, because a 404 on one is worth seeing.
        style = (DIM,) if request.endpoint == 'static' else ()
        line = (f"{c(str(response.status_code), _status_color(response.status_code), BOLD)} "
                f"{c(request.method.ljust(4), DIM)} {c(path, *style)} {c(ms, DIM)}")
        print(f"{c(time.strftime('%H:%M:%S'), DIM)} {line}", flush=True)
        return response


# ── Startup banner ───────────────────────────────────────────────────────────

def collect_stats() -> dict | None:
    """Counts for the banner, or None when the schema isn't there yet."""
    from sqlalchemy import func

    from src.database import SessionLocal
    from src.models import Expense, ExpenseTag, Tag, TagRule, User

    db = SessionLocal()
    try:
        return {
            'expenses': db.query(func.count(Expense.id)).scalar() or 0,
            'tagged': db.query(func.count(func.distinct(ExpenseTag.expense_id)))
                        .filter(ExpenseTag.source != 'suppressed').scalar() or 0,
            'tags': db.query(func.count(Tag.id)).scalar() or 0,
            'rules': db.query(func.count(TagRule.id)).scalar() or 0,
            'users': db.query(func.count(User.id)).scalar() or 0,
            'latest': db.query(func.max(Expense.statement_period)).scalar(),
        }
    except Exception:
        # A missing table means migrations haven't run. That's worth saying in
        # the banner, not worth refusing to start over.
        return None
    finally:
        db.close()


def print_banner(host: str, port: int, debug: bool) -> None:
    url = f"http://{host}:{port}"
    # Still the werkzeug dev server either way — only the reloader and the
    # interactive debugger differ.
    mode = 'debug' if debug else 'no-debug'
    print()
    print(f"  {c('expense-analysis', BOLD, MAGENTA)}   {c(url, BOLD, GREEN)}   {c(mode, DIM)}")

    db_path = config.DATABASE_URL.replace('sqlite:///', '')
    db_path = os.path.relpath(db_path) if os.path.isabs(db_path) else db_path
    stats = collect_stats()
    if stats is None:
        print(f"  {c('db'.ljust(6), DIM)} {db_path}  "
              f"{c('schema missing — run `make refresh-db`', YELLOW)}")
    else:
        pct = (stats['tagged'] / stats['expenses'] * 100) if stats['expenses'] else 0
        accounts = f"{stats['users']} account" + ('' if stats['users'] == 1 else 's')
        print(f"  {c('db'.ljust(6), DIM)} {db_path} {c('·', DIM)} {accounts}")
        print(f"  {c('data'.ljust(6), DIM)} {stats['expenses']:,} expenses {c('·', DIM)} "
              f"{c(f'{pct:.1f}% tagged', GREEN if pct >= 80 else YELLOW)} {c('·', DIM)} "
              f"{stats['tags']} tags {c('·', DIM)} {stats['rules']} rules {c('·', DIM)} "
              f"latest {c(stats['latest'] or 'none', BOLD)}")
    print(f"  {c('ctrl-c to quit', DIM)}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(prog='web.py', description='Expense Analysis dev server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--no-debug', action='store_true',
                        help='disable the reloader and the interactive debugger')
    args = parser.parse_args()
    debug = not args.no_debug

    install_request_log()

    # Flask prints "* Serving Flask app" / "* Debug mode" through click, not a
    # logger, so silencing werkzeug is not enough to replace it.
    flask.cli.show_server_banner = lambda *a, **kw: None

    # Only the child that actually serves prints the banner.
    if not debug or IS_RELOADER_CHILD:
        print_banner(args.host, args.port, debug)

    app.run(host=args.host, port=args.port, debug=debug)


if __name__ == '__main__':
    main()
