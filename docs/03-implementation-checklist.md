# 3. Implementation checklist

"Verified" = an automated test or a real run proved it (see docs/08 for the actual results).

| Stage | Scope | Status |
|---|---|---|
| 1 | Audit + research report | done, docs/01 |
| 2 | ADR + checklist | done, docs/02, this file |
| 3 | Schema, migrations, safe import, backup script | done, verified (29 constraint tests, 13 import tests incl. the real 21,860-row file; backup/restore round-trip run) |
| 4 | Backend APIs, auth, job queue/worker | done, verified (every API route swept for auth + CSRF; restart-recovery tests; real worker process test) |
| 5 | UI wired to the real database | done, verified (15 browser scenarios + 23 unit tests; light, dark, phone width) |
| 6 | Research + evidence pipeline | done, verified against a local fixture site (SSRF, robots.txt, prompt-injection page, fabricated quotes, cache, budget stop). **Not run against the live internet with a real AI key.** |
| 7 | Drafting + approval | done, verified (evidence links, blockers, version-bound approval, near-duplicate guard) |
| 8 | Providers, suppression, sending safeguards | done, verified against a real local SMTP server that can misbehave (42 tests incl. TLS). **Never run against a real mail provider.** |
| 9 | Reply processing + sales pipeline | done, verified (unit + real IMAP/SMTP via GreenMail). |
| 10 | Tests, security review, Docker, docs, final report | done, docs/08 and docs/09 |

## Brief checklist (section numbers refer to the brief)
- [x] 4A Overview with database-backed, basis-labelled metrics
- [x] 4B Contacts: XLSX/CSV import with preview + mapping, DB constraints
- [x] 4C Company research with sources, excerpts, timestamps, confidence, uncertainties, edit/reject
- [x] 4D Explainable score, segments
- [x] 4E Email studio: evidence, warnings, cost, edit/regenerate/approve/reject, templates, tone/length
- [x] 4F Campaign manager: preview, exclusions, typed confirmation, pause/resume/cancel, caps, windows, backoff, error categories
- [x] 4G Sales pipeline: stages, tasks, notes, deals, follow-up stop rules
- [x] 4H Job monitor: persistent jobs, progress, retry/cancel
- [x] 4I Settings incl. secret *status* only
- [x] 5 Duplicate prevention, ambiguous-outcome handling, append-only history, suppression that imports cannot undo
- [x] 6 Reply tracking (IMAP), matching by headers, classification as suggestion, immediate opt-out
- [x] 8 Eligibility + suppression gate, jurisdiction policies, data minimisation, transparent sender identity
- [x] 9 Budgets, usage accounting, caching, dry-run mode
- [x] 10 Automated tests (backend, frontend, browser) and security review
- [x] 12 Docker Compose, migrations, `.env.example`, guides, cost estimate, final report
