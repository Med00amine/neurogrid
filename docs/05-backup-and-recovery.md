# 5. Backup and recovery guide

## What needs protecting

| Data | Where | Why it matters |
|---|---|---|
| **The PostgreSQL database** | Docker volume `pgdata` | Contacts, research, drafts, the append-only event/audit history, and above all the **suppression list** and **send history** that stop duplicate and unwanted emails |
| Uploaded files | Docker volume `appdata` (`/data/uploads`) | The original spreadsheets you imported |
| `.env` | project folder | Your passwords and API keys. **Not** in git. Keep a copy in a password manager. |
| Your original contact file | `data/` | Source of truth for the import |

All of it contains personal data about other people. Treat backups like the live system: encrypt them and never put them on GitHub (`backups/` and `data/` are git-ignored).

## Back up

```bash
make backup           # or: ./scripts/backup.sh
```
Writes `backups/neurogrid-YYYYMMDD-HHMMSS.dump` (PostgreSQL custom format) and `backups/uploads-….tar`, mode 600. **Tested:** a backup was taken, data was changed, the restore brought the database back and the app reported healthy.

Schedule it (Linux/Mac) with `crontab -e`:
```
15 3 * * *  cd /path/to/neurogrid && ./scripts/backup.sh >> backups/backup.log 2>&1
```
Copy backups **off the machine** (an encrypted external drive, or an encrypted cloud bucket you control). Example encryption:
```bash
gpg --symmetric --cipher-algo AES256 backups/neurogrid-20261103-031500.dump     # asks for a passphrase
```
Suggested retention: daily for 14 days, weekly for 3 months.

Also export the **suppression list** weekly (Settings → Privacy & compliance → Export). It is the smallest, most important file, and a plain CSV you can re-import anywhere (imports can only *add* suppressions).

## Restore

```bash
make restore FILE=backups/neurogrid-20261103-031500.dump
```
It asks you to type `RESTORE`, stops the app, **replaces** the database, restores uploads if present, and restarts.

### ⚠ The one thing that can hurt you after a restore
A restore returns the database to the moment of the backup. Anyone who **unsubscribed, bounced or was suppressed after that moment is forgotten**, and
a contact could be eligible again. So after any restore:
1. Keep `LIVE_SENDING_ENABLED=false` (the safe default) until you have done steps 2 to 4.
2. **Replies → Check mailbox now.** The mailbox cursor is restored too, so replies and unsubscribes received since the backup are read again and re-applied.
3. Re-import your latest **suppression export** (Contacts → Import… → *Do-not-contact list*). This can only add entries.
4. Look at *Settings → Privacy & compliance → Suppression list* and at *Campaigns* for anything that looks wrong.

## Disaster scenarios

| Situation | What to do |
|---|---|
| App will not start after an update | `docker compose logs api`. Roll back: `git checkout <previous tag>`, `docker compose up -d --build`. Migrations only move forward; if one fails, restore the pre-update backup. |
| Database volume deleted or disk died | Install Docker on the new machine, `./scripts/setup.sh` is *not* needed if you saved `.env`; copy `.env` back, `docker compose up -d db`, then `make restore FILE=…` and follow the checklist above. |
| Accidentally cancelled a campaign / approved a wrong draft | Nothing to restore: nothing is deleted. Cancelled messages stay in the history; create a new campaign. Emails already accepted by your mail server cannot be recalled. |
| Suspect the data was tampered with | The `outreach_events` and `audit_log` tables are append-only (the database refuses UPDATE/DELETE). Compare against a backup with `pg_restore --list` or restore it into a scratch database. |
| Lost your sign-in | `docker compose exec api python -m app.cli set-password --email you@example.com` |
| Want to start completely clean | `docker compose down -v` deletes the database volume. This is irreversible; back up first. |

## Test your backups
Once, before you rely on them: restore into a scratch database and look at it.
```bash
docker compose exec -T db psql -U neurogrid -d postgres -c "CREATE DATABASE scratch"
docker compose exec -T db pg_restore -U neurogrid -d scratch --no-owner < backups/neurogrid-….dump
docker compose exec -T db psql -U neurogrid -d scratch -c "select count(*) from contacts; select count(*) from suppressions;"
docker compose exec -T db psql -U neurogrid -d postgres -c "DROP DATABASE scratch"
```
