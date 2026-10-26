# 6. Deployment guide

Three ways to run it, from simplest to most exposed. **The simplest is also the safest and is enough to win your first client.**

## Option 1: your own computer (recommended to start)

Follow [docs/04](04-setup-and-operations.md). The app listens on `127.0.0.1:8000` only, so nothing on the network can reach it.
Because your computer is not always on, background work (sending, reading replies) happens only while it runs. For a small daily campaign that is fine.

Downsides: no HTTPS, so emails carry a `mailto:` unsubscribe and the in-body opt-out line but not the one-click HTTPS unsubscribe link
(`List-Unsubscribe-Post`). That is acceptable at your volume. Gmail's one-click requirement formally applies above 5,000 messages a day
(Google sender guidelines, read 2026-10-26).

## Option 2: a small server with HTTPS (when you want it always on)

You are an AWS engineer, so an EC2 or Lightsail instance fits (check current AWS pricing yourself; it was not verified for this guide).
Any Linux VPS works. Needs: Docker + Compose, a **domain name**, ports **80 and 443** open to the internet, nothing else.

1. **Server.** Ubuntu LTS, 1 to 2 vCPU, 2 GB RAM, 20 GB disk is plenty. Create a normal user, enable automatic security updates, allow SSH only from your IP (or use SSM), and open ports 80/443. Do **not** open 5432 or 8000.
2. **DNS.** Create an `A` record `studio.example.com` → the server's IP. Wait until `dig studio.example.com` shows it.
3. **Get the code** (`git clone` your repository) and run `./scripts/setup.sh`.
4. **Edit `.env`:**
   ```
   PUBLIC_BASE_URL=https://studio.example.com
   COOKIE_SECURE=true
   ```
   (leave `LIVE_SENDING_ENABLED=false` until Part D of the setup guide.)
5. **Start with HTTPS:**
   ```bash
   DOMAIN=studio.example.com docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   ```
   Caddy obtains and renews a Let's Encrypt certificate automatically. Open `https://studio.example.com`.
   In this mode the API container is not published to the host; only Caddy reaches it.
6. **Check:** `curl -I https://studio.example.com/api/health` returns `200` with `strict-transport-security`, `content-security-policy` and `x-frame-options` headers; `https://studio.example.com/api/docs` returns 404 (API docs are disabled in production).
7. **Backups:** install the cron job from [docs/05](05-backup-and-recovery.md) and copy dumps off the server.
8. **Updates:** `git pull && DOMAIN=… docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` after a backup.

What HTTPS adds: the sign-in cookie is marked `Secure`, and each email can include a **one-click HTTPS unsubscribe link** (`List-Unsubscribe-Post`, RFC 8058; Gmail asks bulk senders for it, and it is good practice for everyone).
For RFC 8058, your mail server must DKIM-sign the `List-Unsubscribe` headers; mainstream providers do this when DKIM is enabled for your domain.

### Server hardening checklist
- [ ] SSH key login only; no password login; fail2ban or SSM.
- [ ] Only ports 80/443 open. `docker compose ps` shows the database bound to `127.0.0.1` only.
- [ ] `.env` is mode 600 and not in git. `ADMIN_PASSWORD` removed after first start.
- [ ] Automatic OS updates on; you rebuild the image occasionally to pick up base-image fixes.
- [ ] Backups run and have been test-restored. Disk encryption on (EBS encryption on AWS).
- [ ] One user (you). Add a `viewer` account for anyone who only needs to look (`python -m app.cli create-admin --role viewer`).

## The mailbox you send from (applies to every option)

* Use a mailbox on a **domain you own**, with **SPF, DKIM and DMARC** published. Gmail requires SPF or DKIM from everyone, and SPF + DKIM + DMARC from bulk senders (Google sender guidelines).
* A **separate sending domain** (for example `yourname-mail.com` that clearly belongs to you and redirects to your site) is a common way to keep your main domain's reputation separate. It must still show your real name and identity; never disguise who you are, and never rotate mailboxes to get around limits.
* Look up *your* account's sending limit. Examples from the research: Google Workspace paid accounts 2,000 messages/day and trial accounts 500/day (Google, updated 2026-10-22); Zoho documents hourly, reputation-based limits. Personal Gmail is commonly quoted as 500/day but no primary Google page was found. Enter your number in *Settings → Sending*.
* Outbound port 25 is blocked by many cloud providers (AWS throttles it by default). This app uses 587 (STARTTLS) or 465 (SSL), which are not affected.
* **Do not** point this app at Amazon SES or Resend for cold outreach. SES production access requires you to confirm you only send to people who asked for your email; Resend's policy forbids cold outreach and scraped lists (sources in docs/01). A violation can suspend the account. For an AWS professional, risking your AWS account over cold email is a bad trade.

## Environments and secrets quick reference

| Variable | Local | Server |
|---|---|---|
| `ENVIRONMENT` | `production` (default from `.env.example`) | `production` |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | `https://your-domain` |
| `COOKIE_SECURE` | `false` | `true` |
| `SECRET_KEY` | generated | generated; **do not change** it later or old unsubscribe links stop working |
| `LIVE_SENDING_ENABLED` | `false` until you decide | `false` until you decide |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | set to `*` automatically by `docker-compose.prod.yml` (safe: only Caddy can reach the API) |
