# 7. Cost estimate

Your budget is $50 to $200. The app's own running costs are small; the real constraints are deliverability and legal, not money.
"Verified" means the number was read from the provider's page on 2026-10-26 (see docs/01 for sources). "Not verified" means I did **not** check it; look it up.

## Per-unit prices (verified)

| Item | Price | Source |
|---|---|---|
| Claude Haiku 5.5 (default for research) | $0.10 in / $0.50 out per million tokens (prompts ≤ 100K) | platform.claude.com pricing |
| Claude Sonnet 5.5 (default for writing drafts) | $2 in / $10 out per million tokens | same |
| Claude Opus 5.5 (selectable, not default) | $4 in / $20 out per million tokens | same |
| Tavily search (optional) | 1 credit per basic search; $0.008 per credit pay-as-you-go; **1,000 free credits a month** | tavily.com/pricing, docs.tavily.com |
| Sending through your own mailbox | **$0 per email** from this app (your mailbox plan applies) | n/a |
| Amazon SES / Resend | **not used** (policies forbid cold outreach; see docs/06) | docs/01 |

## Per-prospect cost (estimated from the table above)

Token counts are **assumptions** (about 8,000 input / 1,200 output tokens to read a small company's pages, about 4,000 / 700 for one draft).
I could not measure real usage: no API key was available while building, so every LLM and search call in the tests used a fake client.
**The app records the real token counts from the first call onwards** (Settings → Budgets, and each research run / draft shows its own cost), so after your first 10 companies you will have true numbers.

| Step | Model | Estimated cost per prospect |
|---|---|---|
| Research (read pages, extract cited findings) | Haiku 5.5 | 8,000×$0.10/M + 1,200×$0.50/M = **$0.0014** |
| Draft | Sonnet 5.5 | 4,000×$2/M + 700×$10/M = **$0.015** |
| Optional search | Tavily | 1 credit = **$0.008** (free for the first 1,000 a month) |
| **Total** | | **about $0.017** without search; about $0.025 with paid search |

Without any AI key the research uses fixed rules and the drafts use templates: **$0**, with less natural wording and fewer insights.

## What you would spend

| Scenario | AI | Search | Total AI+search |
|---|---|---|---|
| Pilot: 100 prospects researched and drafted | ≈ $1.64 | free tier | **≈ $1.6** |
| 500 prospects | ≈ $8.20 | free tier covers 1,000 | **≈ $8** |
| 2,000 prospects, search on | ≈ $33 | ≈ $8 (1,000 credits beyond the free 1,000) | **≈ $41** |
| Everything re-researched twice (not recommended; caching avoids it) | ×3 | | |

The app **stops** a job rather than exceed the budgets in *Settings → Budgets* (defaults: $15 AI, $5 search per month, $5 per job).

## Fixed and optional costs (not verified: check the current price)

| Item | Needed? | Note |
|---|---|---|
| Computer you already own | Yes | $0 |
| Small cloud server | Optional | Only if you want it always on (docs/06). Price not verified here. |
| Domain name | Recommended | Needed for SPF/DKIM/DMARC and a professional From address. |
| A business mailbox plan (Google Workspace, Zoho, Fastmail …) | Recommended | Plans and limits differ; personal Gmail is free but cold outreach from it is risky for the account. Not verified. |
| Lawyer consultation for DE/AT/IT/ES/CH targeting | Strongly advisable | The biggest real cost risk is a legal complaint, not API fees. |

## Bottom line
Technology cost for the first 500 prospects: **under $10** plus a domain and, optionally, a business mailbox. Your $50 to $200 is comfortably enough. Spend any surplus on a legal check and on slow, careful
warm-up (small daily volume), not on more sending.
