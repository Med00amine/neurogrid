# 4. Setup and operations guide

Written for a non-specialist. Every step says what you will see. Nothing in Part A or B can send an email.

## Part A: first start

1. **Install Docker** (Docker Desktop on Windows/Mac, or Docker Engine + Compose on Linux). Check: `docker compose version` prints a version.
2. In a terminal, open the project folder and run:
   ```bash
   ./scripts/setup.sh
   ```
   It asks for your email, then prints your **sign-in password once** and writes `.env` (readable only by you). The file holds generated
   passwords; never commit or share it.
3. Start everything:
   ```bash
   docker compose up -d --build
   ```
   The first build takes a few minutes. Afterwards `docker compose ps` should show `api`, `worker` and `db` as *healthy*.
4. Open **http://localhost:8000** and sign in with the email and the printed password.
5. Open `.env`, **delete the `ADMIN_PASSWORD=` line**, and run `docker compose up -d`. (The app warns you in *Settings → Connections* until you do.)

Safe by default: the purple badge in the top bar reads **“Safe mode: dry-run only”**. Real email needs three separate switches (Part D).

## Part B: your first day, dry run only (about 2 hours)

### 1. Tell the app who you are (Settings → Sender identity)
Fill **Your name**, **From address**, **Postal address** and the **“Where did you get their address?”** sentence (it must be true, because every
recipient reads it; for example *“I found your work address on your company's public website.”*). Add your GitHub / LinkedIn / portfolio links if you want them in your
signature, and **tick “I confirm this is really my link”** for each: emails only ever include confirmed links. Leave **Proof points** empty unless you have real, verifiable
results to cite; with it empty, drafts make no experience claims at all. Click **Save changes**.

### 2. Import your contacts (Contacts → Import…)
1. Choose **Contacts**, pick `data/contacts_clean.xlsx`.
2. Check the preview. The app guesses *email → email, name → name, company → company*. Your file also has `source_row`, `email_domain`, `attributes`,
   `data_quality_issue`; unticked columns are not stored. Click **Continue**.
3. **Where does this list come from?** Write the truth (source and date). It is stored with the import.
4. **Have these people been emailed before?** Because you told me there was an older outreach project that this folder does not contain:
   pick **“I'm not sure”** (recommended). Everyone is imported as *history unknown* and blocked from sending. Resolve it properly later
   (Part C). Only choose “I confirm none…” if you are certain.
5. Click **Import**. 21,860 rows take about 30 seconds. The summary tells you how many were added, duplicated or invalid. **No email is sent.**

Expect: about 71% of contacts have an *unknown country* (their domains are `.com`, `.io`, `.ai` …), and 3 are pre-excluded (a free-mail address, a recruiting mailbox) or flagged for review (a shared `info@` box).

### 3. Pick a small test batch
Use filters (Contacts page): for example **Country = FR**, search a name, or filter by company. Select 10 to 20 people (checkbox). **Do not try to
process the whole list.** Then **Bulk actions…**:
* **Set country** (only where you really know it, e.g. the company's imprint shows France).
* **I confirm these were never emailed before**: only if you really know (see Part C).
* **Set review decision → Eligible**, with a reason (“relevant AI consultancy, role fits”).

### 4. Country rules (Settings → Privacy & compliance)
Read the *Country rules* table. Countries marked **Consent required** cannot be emailed without a recorded consent for that person.
For a country marked **Opt-out permitted** (for example France, United Kingdom), read the linked source, decide if you meet its conditions, and click **Attest…**
(your note is kept in the audit trail). This is your legal judgement; the app records it, it does not make it. See the warning on that page.

### 5. Research a company (Company research)
Select the companies of your test batch → **Research N selected**. You see the estimated cost before anything starts (free if no AI key is configured).
Watch it in **Job monitor**. Then open the company:
* **Sources** list every page read, with retrieval time. A page that tried to give instructions to an AI is flagged *suspicious text* and not trusted.
* **Findings** each show the **exact quote** and ✓ verified (the app re-checks that the quote really exists in the saved page). *Stated on the site* means a fact; *Inference* means a guess you must explicitly accept.
* Click **Approve** on what is true and useful, **Edit** to fix wording, **Reject** to discard. **Approve N verified facts** does the safe ones at once. Unapproved findings never reach an email.

### 6. Check the score (Leads & scoring)
Click a lead to see each part of its score and the evidence. It is a ranking of *evidence*, not a prediction that someone will buy.

### 7. Generate and review a draft (Email studio)
**Generate drafts…** → pick a template → (AI optional) → Generate. Open the draft:
* **Why this prospect is a fit** shows the approved research with sources.
* **Which claim rests on what** links each paragraph to research or to your profile.
* **Check before approving** lists warnings; **Cannot be approved** lists blockers (an unsupported claim, an unverified link, a placeholder, a near-identical copy sent to an unrelated company).
* **Exactly what will be sent** shows the final text with your signature, postal address, data-source sentence and opt-out line.
* Edit freely: any edit voids the approval; hand-written paragraphs are flagged because the app cannot check them. **Approve this exact text** locks that wording.

### 8. Run a dry-run campaign (Campaigns)
**New campaign** → name it → **Dry run** → **Create draft campaign**. The panel shows who *will* be sent and, under *Excluded (why)*, each person left out and the reason.
Nothing is queued yet. **Review & confirm…** → type the phrase it shows (`DRY RUN 1`) → **Start dry run**. Within seconds the queue shows each message as *Submitted* with a `dryrun-…` reference.
Open a message to see the exact text and the history. **No email left your computer and no contact was changed.**

You have now run the whole loop safely. Look at **Overview**: the numbers are labelled *simulated*.

## Part C: resolving “have they been emailed before?”

The old outreach project is not in this folder, so the app cannot know. Options, best first:
1. **Import your old sent log** (Contacts → Import… → *Sent history*). Needs a column with the email and optionally date and status
   (`sent`, `bounced`, `replied`, `unsubscribed`). Those people become *previously contacted* and cannot receive an automatic first email;
   bounces and unsubscribes become suppressions.
2. Check your mailbox's **Sent** folder and export recipients, then import as above.
3. Only if you truly know no one was contacted: use **“I confirm these were never emailed”**. It is recorded with your name and reason.

## Part D: going live, only when you are ready

Do this only after Parts B and C, and ideally after a lawyer has looked at your target countries.

1. **Prepare the mailbox.** Use a mailbox on a domain you own, with **SPF, DKIM and DMARC** set up (your mail provider has a guide). For Gmail/Workspace create an *App Password* for SMTP. Find your provider's **daily sending limit** for your account.
2. **Add to `.env`**: `SMTP_HOST`, `SMTP_PORT=587`, `SMTP_SECURITY=starttls`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, and for replies and bounces `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`. Run `docker compose up -d`.
3. **Settings → Connections & credentials**: *Test SMTP login*, *Test IMAP login* (neither sends anything), then **Send a test email to yourself** (to your own address only). In your inbox check it is not in spam and that “show original” says SPF=PASS, DKIM=PASS.
4. **Settings → Sending & limits**: set *Your provider's documented daily limit*; keep **Emails per day** low (start at 10 to 20; the app refuses more than half your provider's limit); keep the window and minimum gap.
5. **Flip the three switches**: in `.env` set `LIVE_SENDING_ENABLED=true` and restart; in Settings choose provider **SMTP** and switch on **Allow live (real) campaigns**. The top bar now shows an orange **“Live sending is ENABLED”**.
6. **Your first live campaign**: 5 to 10 people you have fully cleared, created as a **Live** campaign. You must type `SEND n EMAILS`. Watch *Campaigns → Queue* and *Replies*.
7. **Warm up slowly.** Raise the daily cap gradually (for example +5 every few days) while bounces stay low and nobody complains. (That ramp and the "about 2%" bounce guide are common practice, not a rule of any provider; Google asks that spam complaints stay below 0.10%.) The app pauses a campaign by itself on repeated provider errors, policy rejections, an unknown outcome, or a bounce rate above 8%.

Turning it off again: set `LIVE_SENDING_ENABLED=false` (or switch off live mode in Settings). A running live campaign pauses itself on the next tick.

## Part E: daily routine (2 to 3 hours is plenty)

| When | What |
|---|---|
| Start of day | **Overview → Needs your attention**. Open *Replies*; handle interested ones first (reply from your own mailbox). Check *Job monitor* for failures. |
| Prospecting | Take 10 to 20 new contacts through review → research → approve → draft → approve. |
| Campaigns | Confirm the next small batch; pause if anything looks off. |
| When a reply arrives | Open it; confirm or correct the suggested label; set the pipeline stage; add a task or deal. An unsubscribe or “stop emailing me” is applied automatically. |
| Weekly | `make backup`; export the suppression list (*Settings → Privacy → Export*); look at *Overview → Spend*. |

**Unknown outcome.** If a message shows *Unknown* (the connection broke after sending), the campaign pauses. Open the message → *Search my mailbox for it*, check your Sent folder, then record *It was sent* or *It was NOT sent*. The app never retries an unknown message by itself, because that could send a duplicate.

## Part F: troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Page shows “Cannot reach the server” | `docker compose ps`; `docker compose logs api` |
| A contact says “cannot be emailed yet” | Open the contact: it lists every reason (not reviewed, history unknown, country rule, suppressed …). |
| Research job “failed” | Job monitor → open it: each company shows why (site unreachable, robots.txt disallows, blocked address). Retry only repeats unfinished companies. |
| Draft cannot be approved | The *Cannot be approved* box says what to fix (usually a claim without approved research). |
| Campaign “paused” | The banner says why (provider errors, login rejected, unknown outcome, live switch off). Fix it, then **Resume**. |
| No replies appear | IMAP not configured (*Replies* page says so), or wrong mailbox name (`IMAP_MAILBOX`). Use *Test IMAP login*. |
| Forgot password | `docker compose exec api python -m app.cli set-password --email you@example.com` |
| Changed `.env` but nothing changed | `docker compose up -d` (recreates containers with the new values). |
| Cost surprises | *Settings → Budgets*. Jobs stop at the limits you set. Real billing is on the provider's dashboard. |

## Part G: updating
```bash
git pull
docker compose up -d --build     # database migrations are applied automatically on start
```
Take a backup first (`make backup`).
