# 1. Audit and research report

Prepared 2026-10-26. All provider facts below were read from the cited pages on that date.
Where a source was only a secondary summary, or a page could not be read, that is stated. Nothing
here is legal advice.

## 1.1 Audit of the supplied project

| Question | Finding |
|---|---|
| What is in the folder? | One file: `data/contacts_clean.xlsx` (1.4 MB). No source code, Docker files, database, migrations, tests, logs or `.env`. |
| Existing email-sending mechanism | **None exists in the folder.** No SMTP/API/Gmail code, no credentials. |
| Sent-status tracking, dedupe, retries, campaign history | **None exists.** There is no record of anyone having been emailed. |
| Working code to preserve | None. The only asset is the contact data. |
| Why might contacts be emailed twice? | Two risks. (1) The list carries **no history column**, so a fresh database cannot tell contacts you already emailed from new ones. (2) Any prior outreach project outside this folder is not visible. The app therefore imports every contact with history = *unknown* and blocks sending until you review it (section 5 of the brief). |

### The contact file (inspected read-only, 2026-10-26)

* Sheet `contacts`, 21,860 data rows, 7 columns: `source_row, email, email_domain, name, company, attributes, data_quality_issue`. The brief said three columns; the three that matter are `email`, `name`, `company`; the other four are derived/bookkeeping.
* **21,860 unique emails** (case-insensitive), all syntactically valid, no padding, no domain/column mismatch. The earlier dedupe holds.
* 17,315 distinct email domains and 17,069 distinct company names (about 4,500 rows share a domain with another contact).
* 30 blank company values; 24 rows flagged `invalid_attributes` (they have no company).
* `attributes` is JSON with a single key (`Company`) and is redundant with `company`.
* **No job-title column.** "CEO/CTO" cannot be verified from the file, so role relevance is treated as unknown until research finds it.
* **No country, source/provenance, consent or send-history columns.**
* 1 free-mail address, 2 role mailboxes (`info@`, `jobs@`), 1 cell beginning with `-` (`-Ing`) that would be a formula-injection risk in a naive export.
* TLD mix: .com 10,246; .de 1,971; .fr 1,401; .ai 1,345; .io 1,277; .it 496; .ch 469; .net 438; .eu 418; .es 319; .uk 215. The audience is predominantly European, which matters for the legal rules below.

## 1.2 Email delivery options

| Option | Price (verified) | Fit for *cold* outreach to this list | Delivery/bounce events | Verdict |
|---|---|---|---|---|
| **Your own mailbox over SMTP + IMAP** (Gmail/Workspace, Zoho, Fastmail, any host) | $0 to a few $/month. Workspace paid accounts: 2,000 messages/day; trial 500/day; rolling 24h; sending blocks up to 24h when exceeded [G1]. Personal Gmail is commonly cited as 500/day but I found no primary Google page for it. Zoho documents hourly limits (50-500 external/hour, reputation-based), not a daily cap [Z1]. | Works technically. Google's sender guidelines still say not to send to people who did not sign up and not to buy addresses [G2], so deliverability risk is real and volume must stay low. | SMTP gives only "accepted by server". Bounces arrive as DSN mails, readable over IMAP. No trustworthy "delivered" event. | **Selected default.** Smallest, cheapest, and the only option consistent with provider terms at low volume. |
| **Amazon SES** | $0.10 per 1,000 à-la-carte; tiered plans from $0.16 (Essentials). New accounts: $200 credits for 6 months. Sandbox: verified recipients only, 200/day, 1/sec [A1][A2]. | **Production access requires you to confirm you "only send email to individuals who've explicitly requested it"** [A2]. A cold list does not satisfy that. A complaint spike can suspend SES, a risk to the AWS account you rely on professionally. | Excellent (SNS events). | **Not recommended for cold outreach.** Possible later if recipients opt in. |
| **Resend** | Free: 3,000/month, 100/day. Pro $20 for 50,000/month. Webhooks and inbound on all plans [R1]. | Acceptable-use policy says: "You are prohibited from sending unsolicited messages of any kind" including "cold outreach, purchased lists, or scraped contact data" [R2]. | Webhooks. | **Rejected for this use.** |
| Postmark, Brevo, SendGrid, Mailgun | Not verified. The Postmark guideline page returned 404 and the Brevo terms page returned only a title. | Transactional/marketing ESPs generally require opt-in lists; unverified here. | Yes | Not evaluated. Do not assume they allow cold outreach. |
| Cold-email SaaS (Instantly, Smartlead, …) | Not evaluated (no verified pricing or terms). | They still send from your mailboxes. | Vendor dashboards | Out of scope for a $50-200 start. |

**Smallest practical upgrade if delivery confirmation is ever needed:** keep SMTP/IMAP, request DSN
`NOTIFY=SUCCESS` where the server advertises the DSN extension, and parse RFC 3464 reports. If you later
move to opt-in contacts, SES or Resend become appropriate. The app has a provider interface for that.
It will not switch providers silently.

### Reply tracking
IMAP polling with `In-Reply-To` / `References` header matching against the `Message-ID` the app itself
generates. Works for any mailbox. Bounces (RFC 3464 DSN) come through the same path.

### One-click unsubscribe
RFC 8058 requires one HTTPS URI in `List-Unsubscribe`, `List-Unsubscribe-Post: List-Unsubscribe=One-Click`,
a DKIM signature covering both headers, no redirect, and no cookies on the POST [RFC8058]. Gmail only
*requires* it above 5,000 messages/day, but says all senders should offer an easy unsubscribe [G2].
The app always adds a `mailto:` unsubscribe plus an in-body opt-out line; it adds the HTTPS one-click form
only when `PUBLIC_BASE_URL` is a public HTTPS address (it cannot work from localhost).

## 1.3 Research (company intelligence) providers

| Source | Price/limits (verified) | Notes | Decision |
|---|---|---|---|
| **Company's own website** (fetched directly) | Free | Primary, authoritative for what the company says about itself. Needs SSRF protection and robots.txt respect. | **Primary source** |
| **Tavily Search API** | Free 1,000 credits/month; pay-as-you-go $0.008/credit; basic search = 1 credit, advanced = 2 [T1][T2]. `POST https://api.tavily.com/search`, Bearer auth, returns title/url/content snippet per result [T3]. | Cheap and snippet-based, good for hiring/news signals. | **Optional secondary source (implemented)** |
| Brave Search API | $5 per 1,000 requests, $5 free credit monthly [B1]. | The documentation I read does not state result storage/caching rights; the FAQ says storage needs "a plan that includes storage rights". Evidence must be stored, so this is unresolved. | Not implemented |
| Exa | $10 free credits/month, $4-$15 per 1,000 searches [E1]. | More expensive per query. | Not implemented |
| Bing Search API | Retired 2025-08-11 [S1]. | - | Unavailable |
| Google Custom Search JSON API | Closed to new customers; reported retirement 2027-01-01 [S1]. | Secondary reports only. | Unavailable |
| Claude web-search tool | $10 per 1,000 searches plus tokens [C1]. | Twice the Tavily pay-as-you-go cost per search. | Not used |

## 1.4 LLM options (prices per million tokens, input/output, [C1])

| Model | Price | Use here |
|---|---|---|
| `claude-haiku-5-5` | $0.10 / $0.50 (prompts ≤100K) | Default for research extraction (tiny cost per company) |
| `claude-sonnet-5-5` | $2 / $10 | Default for drafting (better prose) |
| `claude-opus-5-5` | $4 / $20 | Available in settings, not default |

Batch API halves these, but interactive review needs immediate results, so it is not used. The Anthropic
web-search server tool was not used (see above). Rough cost: a research pass of ~8K input / ~1.5K output
tokens costs about $0.0016 on Haiku 5.5 or $0.031 on Sonnet 5.5; a draft of ~4K/0.6K tokens about $0.008
on Sonnet 5.5. A 500-company pilot is therefore a few dollars, far below the $50-200 budget. These are
*estimates from the price table*; the app records the token counts the API actually returns.

## 1.5 Architecture options (job queue, database, UI)

* **Database:** PostgreSQL 16 (required by the brief; partial unique indexes and triggers are what enforce no-duplicate-send).
* **Queue:** Celery + Redis was evaluated; a Postgres-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`) was chosen: one fewer service, jobs and progress survive restarts by construction, and the Job Monitor reads the same table the worker uses. See the ADR.
* **UI:** React + TypeScript + Vite served by FastAPI in production (single origin → simple cookies/CSRF, no CORS surface).

## 1.6 Privacy, anti-spam and provider rules (what drove the design)

> The app encodes these as **editable, attestable policy records**. Each country policy starts
> un-attested; a contact in a "consent required" country cannot be sent to without recorded consent
> evidence. The list's countries are inferred from the email TLD only, which is a weak signal
> (.com/.io/.ai tell us nothing), so unknown country means *blocked until reviewed*.

| Jurisdiction | Position | Source |
|---|---|---|
| **EU (ePrivacy Art. 13)** | Prior consent for unsolicited email to *natural-person* subscribers; for legal persons each member state decides. Sender identity must not be concealed and a valid opt-out address must exist. | Directive 2002/58/EC art. 13 (my reading was from memory, as the EUR-Lex fetch returned empty) [L1] |
| **GDPR** | Named business contacts are personal data. When data was not collected from the person, they must be informed (at the latest at first communication / within one month), including the right to object; the right to object to direct marketing is absolute. | Art. 14, 21 (EUR-Lex fetch empty; ICO confirms the same timing for UK GDPR) [L2][L3] |
| **Germany** | Email advertising requires prior express consent, businesses included; narrow existing-customer exception. §7(2) Nr. 2 / §7(3) UWG. | gesetze-im-internet.de, summarised by the fetch tool [L4]; secondary: itsalesaas.com (2026-09-28) |
| **France** | B2B prospecting by email can rest on legitimate interest if the offer relates to the person's professional role, they were informed, and can object. Generic mailboxes (info@) are outside the personal-data principles. | CNIL page dated 2026-06-10 [L5] |
| **United Kingdom** | PECR email rule does not apply to corporate subscribers (companies, LLPs); sole traders and some partnerships are individuals and need consent/soft opt-in. UK GDPR still applies to named people. A working opt-out address is required. | ICO B2B guidance [L3] |
| **Italy, Spain, Austria, Netherlands, Poland, Czechia** | Reported as opt-in even for companies. Secondary sources only (Garante decisions, AEPD documents, a 2026-09-28 vendor article). Treated as *consent required* until you verify otherwise. | [L6][L7] |
| **Switzerland** | Not verified (Fedlex needs JavaScript). Treated conservatively as consent required. | - |
| **Belgium, Ireland, Nordics, other** | Not verified. Policy = *unknown* (blocked until you decide). | - |
| **United States (CAN-SPAM)** | Applies to B2B email. Requires accurate headers, non-deceptive subject, ad identification, valid postal address, a working opt-out honoured within 10 business days; penalties up to $53,088 per email. | FTC guide [L8] |
| **Gmail** | SPF or DKIM for all; SPF+DKIM+DMARC and one-click unsubscribe above 5,000/day; keep spam rate below 0.10% (never reach 0.30%). Do not purchase addresses or message people who did not sign up. | Google sender guidelines [G2] |

**Consequences built into the product:** global suppression list that imports can never override; a mandatory
opt-out line, sender identity and postal address in every email; a data-source disclosure line you must fill
in truthfully; per-country policy gates; unknown history blocks sending; small conservative daily caps; and a
dry-run mode that cannot send. These reduce risk; they do not make a purchased/scraped list lawful. Consider
asking a lawyer before the first live batch, especially for DE/AT/CH/IT/ES.

## 1.7 Sources

* [A1] https://aws.amazon.com/ses/pricing/
* [A2] https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html (also https://aws.amazon.com/service-terms/ §15)
* [R1] https://resend.com/pricing
* [R2] https://resend.com/legal/acceptable-use
* [G1] https://knowledge.workspace.google.com/admin/gmail/gmail-sending-limits-in-google-workspace (page updated 2026-10-22)
* [G2] https://support.google.com/a/answer/81126
* [Z1] https://www.zoho.com/mail/help/adminconsole/rates-and-limits.html (via search summary)
* [RFC8058] https://datatracker.ietf.org/doc/html/rfc8058
* [T1] https://www.tavily.com/pricing  [T2] https://docs.tavily.com/documentation/api-credits  [T3] https://docs.tavily.com/documentation/api-reference/endpoint/search
* [B1] https://brave.com/search/api/  [E1] https://exa.ai/pricing
* [S1] Search summaries on Bing API retirement and Google CSE closure (dev.to, brave.com/learn/google-api-shutdown, simonwillison.net); secondary
* [C1] https://platform.claude.com/docs/en/about-claude/pricing
* [L1] https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32002L0058
* [L2] https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679
* [L3] https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/
* [L4] https://www.gesetze-im-internet.de/uwg_2004/__7.html
* [L5] https://www.cnil.fr/fr/la-prospection-commerciale-par-courrier-electronique
* [L6] https://itsalesaas.com/blog/is-cold-email-legal-in-europe (2026-09-28, secondary)
* [L7] Garante Privacy and AEPD documents surfaced by search (secondary)
* [L8] https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business

## 1.8 Limits of this research

* Several fetches (EUR-Lex, RIS Austria, Fedlex, Postmark, Brevo) failed or returned empty, so those rows rely on secondary sources or are marked unverified.
* Provider prices change. The app's price table stores the date and source URL beside every number and shows them in Settings.
* No API keys were available in this environment, so no live LLM, Tavily or mail-server call was made while building. Everything that touches those services is tested against local fakes and a local SMTP sink, and this is stated again in the final report.
