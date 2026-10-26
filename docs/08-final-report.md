# 8. Final report

Date: 2026-10-26. Everything below is what was actually built and run, not what was planned.

## 1. What was delivered

A working, containerised workspace (React UI + FastAPI + PostgreSQL + background worker) that covers every page and rule in the brief,
runs end to end in **dry-run mode**, and refuses to send real email until you deliberately enable it.

* **Real database throughout.** All Overview numbers are queries; each carries a basis label (database / estimate / simulated / mail-system confirmed). An empty database shows zeros (browser test).
* **Your file imported safely.** `data/contacts_clean.xlsx`: 21,860 rows → **21,860 contacts, 17,314 companies, 0 invalid, 0 duplicates**, in 32 seconds through the HTTP API and background worker; re-importing the same file adds nothing and changes nothing. Nothing was sent.
* **Duplicate and unlawful sends are blocked by the database**, not just the code (docs/02, 29 constraint tests).
* **Research with provenance**, **explainable scoring**, **evidence-linked drafts bound to an exact approved text**, **campaigns with typed confirmation**, **reply/bounce/unsubscribe tracking**, **pipeline**, **job monitor**, **settings that never show secrets**.
* Docker Compose (db + app + worker), Alembic migrations, `.env.example`, setup/backup/restore scripts, Makefile, six guides.

## 2. What the audit found (the starting point)

| Brief expectation | Reality in the folder |
|---|---|
| An existing outreach project to inspect and preserve | **None.** The folder held only `data/contacts_clean.xlsx`: no code, Docker files, database, logs, credentials or send history. |
| An existing email mechanism, sent-status tracking | None to inspect. So the app imports everyone as **history unknown** and blocks sending until you resolve it. |
| 3 columns | 7 columns (`source_row, email, email_domain, name, company, attributes, data_quality_issue`); 3 matter. |
| CEO/CTO contacts | **No job-title column.** Role relevance is "unknown" unless research finds a title on the company's own site (and you approve it). |
| Cleaned list | Confirmed: 21,860 unique, valid, lower-case addresses; 30 rows have no company name (24 of them flagged `invalid_attributes`); 1 name (`-Ing`) would be a formula risk in a naive export (neutralised). |
| Country / consent / provenance | **Not in the file.** Country is guessed from the domain ending only: **15,499 contacts (71%) have no usable country** (.com/.io/.ai …); DE 1,971 · FR 1,401 · IT 496 · CH 469 · ES 319 · BE 266 · GB 215 · NL 135 · AT 117. |

**What that means for your plan:** the countries the app treats as *consent required* (DE, AT, CH, IT, ES, NL, PL, CZ) hold ≈3,600 contacts (about 16%);
only France and the UK (≈1,600, about 7%) can become sendable after you attest their rules, and 71% need a country established first. You will
start with a small, hand-reviewed batch, and that is the correct way to start anyway.

## 3. Actual test results (final run)

| Suite | Result |
|---|---|
| Backend (pytest, real PostgreSQL, each test in its own database) | **215 passed, 0 failed, 0 skipped** |
| Frontend unit tests (vitest) | **23 passed** |
| Browser acceptance tests (Playwright/Chromium, real API + worker + DB, synthetic data, dry-run) | **15 passed** |
| Type-check (`tsc`) | clean |
| `pip-audit` / `npm audit --omit=dev` | no known vulnerabilities / 0 vulnerabilities |
| Docker | image built; full stack started in production mode; healthy; import → job → shared volume verified; **backup → change → restore round-trip verified** |

Backend breakdown: research 44 · sending 45 (incl. TLS, test-email) · replies+pipeline 30 · database constraints 29 · API security 14 · import 13 · drafting 12 · jobs 10 · API end-to-end 7 · config 4 · IMAP integration 5 · worker process 2.

What the tests cover (brief section 10): importing the **actual file** · dedup and constraints · history preservation · suppression and unsubscribe · research provenance and unsupported claims (fabricated quotes, injected page) · draft generation and approval · pause/cancel · provider limits and retry logic · **ambiguous send outcomes** · reply matching and classification · authentication and authorisation (every route swept) · restart recovery and persistent jobs (a worker killed mid-job resumes without repeating items) · dashboard numbers equal the database · secret handling · security-sensitive routes.

### Defects the testing found, all fixed
| Found by | Defect | Fix |
|---|---|---|
| **Browser test** | **The real worker never ran its periodic tasks** (sending, mailbox polling, follow-up guard, retention) when started as `python -m app.jobs.worker`: the registry existed twice (`__main__` vs imported module). Unit tests called the function directly and could not see it. | Registry moved to its own module; regression test starts the worker exactly like production |
| Browser test | A page could render a blank screen when a table's data had not loaded yet | `QueryState` treats "no data" as loading; null-safe tables; an error boundary so no bug blanks the workspace |
| Unit test | "try again later" was classed as rate limiting; a refused recipient could be retried on the wrong schedule | Classifier rewritten and table-tested |
| Unit test | Campaign stayed "running" after its last message (stale read) | flush before the completion check |
| Route sweep | Two read-only "estimate" endpoints needed only a login | now admin + CSRF like every other POST |
| Wiring the UI | `"false"` in a JSON filter was truthy (would have researched the wrong contacts) | one normalising constructor + test |
| Screenshot review | Quoted sentences ended `.”.` | fixed |
| Accessibility check | Sidebar landmark named the wrong element | fixed |
| Review | Cost table arithmetic error (search credits) | corrected |
| Audit | react-router advisories | upgraded; audit clean |

## 4. What is NOT verified (read this)

1. **No live provider was ever contacted.** No real LLM (Anthropic), search (Tavily), SMTP provider (Gmail/Workspace/Zoho …) or real IMAP mailbox was used; no API keys existed. The SMTP and IMAP code is tested against **real protocol servers I ran locally** (aiosmtpd with injected failures including TLS, and GreenMail), and the LLM/search code against scripted fakes. Real providers have quirks (greylisting, odd bounce wording, App-Password flows) that only a real first batch will reveal. Start tiny.
2. **Research quality on real websites is unmeasured.** The pipeline was run on a local fixture site. Rule-based extraction is modest by design; AI extraction is untested against a real model, so its cost figures are estimates from the price table (docs/07).
3. **The legal table is a starting point.** Primary sources: Germany (§7 UWG), France (CNIL, 2026-06-10), UK (ICO), US (FTC). Italy, Spain, Austria, Netherlands, Poland, Czechia are from secondary sources; Switzerland is unverified; Belgium, Ireland, the Nordics and others are "not assessed". A lawyer should check your target countries before the first live batch.
4. **Delivery cannot be confirmed with plain SMTP.** "Submitted" means your server accepted it. "Delivered" appears only if the receiving system returns a receipt (rare). This is a property of SMTP, not a bug; the smallest practical upgrade (docs/01 §1.2) conflicts with the cold-outreach policies of the usual providers.
5. **Only Linux + Chromium were exercised.** Docker Desktop on Windows/Mac and other browsers should work but were not run. The local machine has Node 18, so Playwright 1.49 was pinned; the Docker build uses Node 20.
6. Not implemented (deliberately, to avoid fake integrations): Amazon SES / Resend / other API senders, OpenAI-compatible LLMs, Brave/Exa search, OAuth for Gmail, calendar booking, multi-tenant users, two-factor authentication.

## 5. Security posture
See docs/09. Short version: strong against the common web attacks and against hostile imports/pages; **single shared admin credential, login throttling only, secrets in a plain `.env`**, and the main non-technical risk is regulatory (emailing a purchased or scraped list into consent-required countries).

## 6. Next highest-priority improvements
1. **Run the real pilot:** 10 hand-picked French/UK contacts, AI key on, live switches on, five emails. Read real replies; measure real cost and quality. (An hour of reality beats weeks of features.)
2. **Resolve the old send history** (import the sent log from the previous tool), the single biggest safety lever for the other 21,000 contacts.
3. Lawyer review of the country rules for your target segments; extend the policy table and sources.
4. A "contact enrichment" import (titles, countries) from a source you are allowed to use, so 71% unknown-country becomes actionable.
5. Two-factor sign-in and an IP allow-list before any public deployment.
6. Provider adapters with delivery webhooks *only if* you move to opt-in contacts.
7. Calendar-link and proposal templates once replies start arriving.

## 7. Changes made to your machine
Files inside this project folder only. Docker images pulled: `postgres:16-alpine`, `greenmail/standalone`, build bases. My test containers and volumes were removed afterwards (including my temporary dev database that contained a test user), `.env` files I generated were deleted (run `scripts/setup.sh` yourself), and `backups/` was emptied. **No email was sent, no external service was called except documentation pages and package downloads, and no credential of yours was used.**
