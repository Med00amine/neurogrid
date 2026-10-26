# 2. Architecture decision record

Status: accepted, 2026-10-26.

## Context
One operator (a freelance engineer), a 21,860-contact list with unknown send history, a $50-200 budget,
2-3 hours/day, and a requirement that **duplicate or unlawful sends must be structurally impossible**, not
merely unlikely. There is no legacy code to preserve.

## Decisions

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 1 | **Modular monolith**: one FastAPI app (`api`), one worker process (same image, different command), PostgreSQL. Four containers with the optional proxy. | Smallest thing that satisfies "persistent jobs that survive restarts". | Microservices, Kubernetes (explicitly unwanted). |
| 2 | **PostgreSQL 16** with SQLAlchemy 2 + Alembic, psycopg 3, synchronous code. | Partial unique indexes, triggers and `SKIP LOCKED` are the safety mechanism. Sync keeps transactions easy to reason about. | SQLite (no partial-index/trigger parity with prod). |
| 3 | **Postgres-backed job queue** (`jobs` + `job_items`, `FOR UPDATE SKIP LOCKED`, heartbeat, stale-lock reaper). | Jobs, progress and the monitor UI read one table; no Redis to run, back up or secure. Items give idempotent resume. | Celery+Redis: more moving parts, state split across two stores. Revisit if throughput exceeds ~10 jobs/second (it will not). |
| 4 | **React 18 + TypeScript + Vite**, served as static files by FastAPI in production. | Single origin: cookie auth and CSRF are simple, no CORS in production. | Next.js (extra server), HTMX (charts and detail panels are richer in React). |
| 5 | **Auth:** server-side sessions in the DB (random token, SHA-256 stored), HttpOnly SameSite=Lax cookie, per-session CSRF header on unsafe methods, argon2id passwords, login throttling, roles `admin`/`viewer`. | Revocable, no JWT pitfalls. Only `admin` may confirm campaigns. | JWT in localStorage. |
| 6 | **Secrets live in environment / `.env` only.** The UI shows *configured / missing*, never values. Logs pass through a redacting filter. | Smallest attack surface; no encryption-key management. | Storing keys in the DB. |
| 7 | **Email: SMTP (send) + IMAP (replies, bounces)** behind a `Provider` interface, plus a `dry_run` provider that cannot reach the network. | Matches research (section 1.2): the only cold-outreach-compatible, cheapest route. | SES/Resend (terms forbid cold outreach). |
| 8 | **LLM:** Anthropic SDK with structured outputs; a deterministic *template mode* works with no key. Search: Tavily optional. | Works at $0; every LLM claim must carry a verbatim quote that the code re-checks against stored page text. | LLM-only drafting with no verification. |

## How "no duplicate send" is enforced (defense in depth)

1. **Partial unique index** `uq_live_message_per_contact_step` on `messages(contact_id, sequence_step)` where `NOT is_dry_run AND status NOT IN ('cancelled','failed')`. A second live message for the same contact and step cannot exist, even from two workers or two campaigns. Retries reuse the same row.
2. **Unique `idempotency_key` and `rfc_message_id`** on messages.
3. **Write-ahead intent:** the worker commits `submitting` + an attempt row *before* touching the provider, and a crash leaves `submitting`, which recovery converts to `unknown`, never to `queued`.
4. **Ambiguous outcomes are never retried automatically.** Only failures that provably happened before the message data was sent (connection, TLS, auth, 4xx at RCPT) re-queue.
5. **Re-check before every send** inside the transaction: eligibility, suppression (email + domain), history, jurisdiction policy, campaign state, window and rate limits.
6. **DB trigger** refuses to move a live message to `queued`/`submitting` for a suppressed address, even if application code has a bug.
7. **Append-only `outreach_events` and `audit_log`** (triggers reject UPDATE/DELETE/TRUNCATE).
8. **Suppressions are a separate table** keyed by normalized email/domain. Import code has no code path that deactivates one.
9. **Unknown history blocks sending.** A fresh database never means "unsent": contacts need an explicit, audited attestation or a history import.
10. **Live sending needs three independent switches:** env `LIVE_SENDING_ENABLED=true`, a live-mode setting, and a per-campaign typed confirmation. Dry-run messages are excluded from the uniqueness index and from real metrics.

## Consequences
* Delivery can only be reported as *submitted/accepted* with SMTP; "delivered" appears only when a DSN says so. The dashboard labels this.
* Throughput is deliberately tiny (default 20/day). That is a feature.
* Postgres is a single point of failure; backups are documented (docs/05).

## Module layout (as built)
```
backend/app/
  models/        one file per area (auth, contacts, research, drafting, sending, replies, pipeline_jobs); Alembic migrations in backend/migrations
  services/      contacts, importing, eligibility, jurisdictions, suppression, segments, scoring, metrics, retention, llm, seed
                 research/ (fetcher, extract, heuristics, llm_extract, search, pipeline, jobs)    drafting/ (generator, lint, compose, service, jobs)
                 sending/ (providers, limits, engine, campaigns)    replies/ (parse, classify, processor, imap, jobs)    pipeline_service
  routers/       thin HTTP layer, one file per page of the UI
  jobs/          queue (Postgres), runner, worker, periodic (task registry)
  core/          config-adjacent: security, deps (auth/CSRF), events (append-only helpers), settings_store, pricing, text, logging_setup
frontend/src/    pages/ (one per sidebar item), components/ (ui, charts, ContactDrawer, ImportWizard, Layout), api.ts, auth.tsx
```
Dependencies point one way: routers → services → models. The sender, research and reply modules never import each other; they meet in the database.

## Decisions added during the build
| # | Decision | Why |
|---|---|---|
| 9 | **No Redis/Celery.** Periodic work (send tick every 5 s, mailbox poll every 5 min, follow-up guard, retention) is a small registry in `jobs/periodic.py`. | Fewer moving parts. The registry is its own module because the worker starts as `python -m app.jobs.worker` (module `__main__`); a registry inside that file existed twice and silently never ran. Caught by the browser test; guarded by `tests/test_worker_process.py`. |
| 10 | **One send = three steps with no transaction held open across the network call** (claim+`submitting`+commit → provider call → record outcome). | A crash leaves `submitting`, which recovery turns into `unknown`, never `queued`. |
| 11 | **Evidence is verified by code, not trusted from the model.** Every quote is re-searched in the saved page text; failures are `auto_rejected` and cannot be approved. | Defeats hallucination and prompt injection reaching an email. |
| 12 | **Approval is bound to a SHA-256 of the exact subject+body.** Any edit voids it; the engine re-checks at send time. | "The text I approved is the text that is sent." |
| 13 | **Live sending needs 3 switches + typed confirmation**, and `ENVIRONMENT=test` makes the SMTP/IMAP clients refuse non-local hosts. | A misconfigured test or a bug cannot reach the real world. |
| 14 | **Reply handling is read-only IMAP** (`BODY.PEEK`, `readonly=True`). | The app never changes your mailbox. |
| 15 | **STARTTLS/SSL uses the system trust store with hostname verification; no downgrade.** | Credentials and mail are never sent in clear because a server misbehaves. |
