# Client Acquisition Studio

A private workspace for finding, researching and carefully contacting B2B prospects for freelance AI / Python / AWS work.
It is built so that **a mistake costs you nothing**: nothing is emailed until you deliberately turn real sending on, nobody can
be emailed twice, and nobody who opts out can be contacted again.

```
contacts file ──► import (preview + mapping) ──► review & compliance gates ──► company research (with sources)
      ──► explainable lead score ──► evidence-linked draft ──► YOU approve the exact text ──► campaign (dry run first)
      ──► careful sending (caps, windows, re-checks) ──► replies / bounces / unsubscribes ──► sales pipeline
```

## Start it (about 5 minutes)

You need Docker (Docker Desktop, or Docker Engine with the Compose plugin).

```bash
./scripts/setup.sh                 # creates .env with generated passwords; prints your sign-in once
docker compose up -d --build       # database + app + background worker
```

Open **http://localhost:8000** and sign in. Real email is **off**. The top bar says *“Safe mode: dry-run only”*.

Step-by-step guide for your first day (import → research → review a draft → dry-run campaign): **[docs/04-setup-and-operations.md](docs/04-setup-and-operations.md)**.

## What is in the box

| Page | What it does |
|---|---|
| **Overview** | Real counts from the database, each labelled *database / estimate / simulated / mail-system confirmed*. Getting-started checklist. |
| **Contacts** | Import `.xlsx`/`.csv` with preview and column mapping. Search, filter, bulk actions with audited reasons. |
| **Company research** | Reads each company's own website (robots.txt respected), optional web search. Every finding has an exact quote, source link and retrieval time. You approve, edit or reject. |
| **Leads & scoring** | 0-100 evidence score with its parts and the evidence behind each. Configurable segments. Not a sales prediction. |
| **Email studio** | Drafts where each claim points to approved research or your own profile. Warnings, blockers, cost, version history. Approval is bound to the exact text. |
| **Campaigns** | Preview recipients and *why* others are excluded. Dry run or live. Typed confirmation. Pause / resume / cancel. Queue with per-message history. |
| **Replies** | Replies, bounces, out-of-office from your mailbox (read-only). Suggested label you can correct. Opt-outs act immediately. |
| **Sales pipeline** | Drag-and-drop board, tasks and follow-ups (which stop by themselves on reply / unsubscribe / bounce), deals. |
| **Job monitor** | Background jobs with progress, errors, safe retry and cancel. Survives restarts. |
| **Settings** | Sender identity, limits, AI/research providers, budgets, country rules, suppression list, retention, credential *status* (never values). |

## How duplicates and unlawful sends are prevented (not just discouraged)

Enforced inside PostgreSQL, so even a bug in the application cannot bypass it: one live message per contact per sequence step across all
campaigns (partial unique index); an illegal status change (e.g. `unknown` → `queued`) is rejected by a trigger; a message to a suppressed address is
rejected by a trigger; history tables are append-only; suppressions can never be deleted. Details: [docs/02](docs/02-architecture-decision-record.md).

## Important honesty notes

* **Your contact file has no job titles, countries or send history**, and nothing in the folder records who was emailed before. So every contact starts as
  *history unknown* and *country unknown* and is blocked from sending until you resolve both. That is deliberate.
* **Most contacts are in countries where cold B2B email is restricted** (Germany, Austria, Italy, Spain, Switzerland and others generally need prior consent). See *Settings → Privacy & compliance*. This app reduces the risk; it cannot make a purchased/scraped list lawful. Consider a lawyer before the first live batch.
* **“Submitted” is not “delivered”.** With plain SMTP the only confirmed fact is that your mail server accepted the message.
* **Personal data.** `data/` and `backups/` are git-ignored. Never push them to GitHub.

## Everyday commands

| | |
|---|---|
| `make up` / `make down` / `make logs` | start / stop / watch the app |
| `make backup` / `make restore FILE=…` | back up / restore (read [docs/05](docs/05-backup-and-recovery.md) first) |
| `make admin EMAIL=you@example.com` | add a user |
| `make doctor` | configuration checklist (never prints secrets) |
| `make test` | all tests (needs `make db`, see below) |

## Development

```bash
docker compose up -d db                                   # PostgreSQL on 127.0.0.1:55432
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
alembic upgrade head && python -m pytest                  # 200+ tests, each in its own throw-away database
cd ../frontend && npm ci && npm test && npm run build     # unit tests, then the UI bundle FastAPI serves
npx playwright install chromium && npx playwright test    # browser acceptance tests (dry-run, synthetic data)
docker compose --profile test up -d greenmail             # optional: real IMAP+SMTP test server for 5 extra tests
```

## Documents

1. [Audit and research report](docs/01-audit-and-research-report.md): what was in the folder, providers compared with sources and dates, legal landscape
2. [Architecture decision record](docs/02-architecture-decision-record.md) · 3. [Implementation checklist](docs/03-implementation-checklist.md)
4. [Setup and operations guide](docs/04-setup-and-operations.md) · 5. [Backup and recovery](docs/05-backup-and-recovery.md) · 6. [Deployment](docs/06-deployment-guide.md)
7. [Cost estimate](docs/07-cost-estimate.md) · 8. [Final report](docs/08-final-report.md) · 9. [Security review](docs/09-security-review.md)
