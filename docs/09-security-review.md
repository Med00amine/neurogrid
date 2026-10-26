# 9. Security review

Reviewed 2026-10-26 against the design and the code, with tests where a control can be tested. "Test" names are in `backend/tests/`, `frontend/e2e/`.

## Assets
Personal data of ~21,860 business contacts and their replies; your mailbox credentials; AI/search API keys; the suppression list (protects third parties); your sender reputation.

## Threats and controls

| Threat | Control | Evidence |
|---|---|---|
| Anonymous access to the workspace | Every `/api` route needs a server-side session (HttpOnly, SameSite=Lax, `Secure` when HTTPS). Only login, health and the signed unsubscribe link are public. | `test_every_api_route_requires_a_session` sweeps the whole OpenAPI route table |
| CSRF | Every state-changing route also requires the per-session `X-CSRF-Token` and the admin role | `test_every_state_changing_route_needs_csrf_and_admin` |
| Brute force | argon2id hashes; 5 failed logins per email / 25 per IP per 15 min → 429; unknown user and wrong password are indistinguishable | `test_wrong_password_and_lockout`, `test_unknown_user_gets_same_error_as_wrong_password` |
| Unauthorised sends | Admin role + CSRF + typed phrase + 3 live switches + re-check at send time. Viewer accounts are read-only. | `test_live_needs_all_three_switches`, `test_viewer_can_read_but_not_write` |
| Secrets leaking | Secrets only in `.env`; UI/API return booleans; logs pass through a redaction filter; ENV `SECRET_KEY` weak value refused in production | `test_settings_endpoint_never_exposes_secret_values`, `test_log_redaction`, e2e “settings never display secret values” |
| SQL injection | SQLAlchemy parameters everywhere; the single f-string in SQL is a static index definition built from constants; LIKE wildcards escaped | `test_search_and_filters_are_injection_safe` |
| XSS | React escapes output; no `dangerouslySetInnerHTML`; CSP `default-src 'self'; script-src 'self'`; the unsubscribe page HTML-escapes | CSP header test, code grep |
| Clickjacking / MIME sniffing | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`; `no-store` on API responses | `test_security_headers_present` |
| CORS | Closed in production; only the dev origin in development | `test_cors_is_not_open_to_arbitrary_origins` |
| SSRF through research | Own DNS resolution; every address must be globally routable; connection pinned to the validated IP (TLS still verifies the host name); every redirect re-validated; ports 80/443 only; no credentials in URLs; size/time caps | 40+ cases in `test_research.py` (loopback, private, link-local, IPv4-mapped IPv6, rebinding, redirect to metadata); also refused loopback in the production container |
| Prompt injection from web pages | Text is untrusted data; a regex flags instruction-like text; the model is told to ignore it; every cited quote must exist in the saved page; the planted claim in the fixture never became a finding; flagged sources downgrade confidence | `test_injected_page_is_flagged_and_downgraded`, `test_llm_findings_with_fabricated_quotes_are_auto_rejected` |
| Hostile spreadsheets | 60 MB cap; zip-bomb check; XML entity bombs refused (defusedxml); formulas stored as inert text; invisible/control characters stripped; header-injection characters rejected in emails | `test_hostile_spreadsheets_are_refused…`, `test_import_synthetic_contacts` |
| Formula injection in exports | Cells starting with `= + - @` get a leading `'` in CSV and XLSX | `test_exports_neutralise_spreadsheet_formulas` |
| Path traversal | Uploads are re-named (`sha-prefix-safe_name`); import references accept only a basename that exists in the uploads folder | `test_upload_rejects_bad_files_and_path_traversal` |
| Duplicate / unwanted sends | Database constraints and triggers (docs/02) | 29 constraint tests; `test_parallel_workers_never_double_send` |
| Forged unsubscribe links | HMAC-signed tokens; GET never changes state (mail scanners); POST honours RFC 8058 one-click | `test_unsubscribe_link_requires_valid_signature_and_suppresses` |
| Mail-in-the-clear | STARTTLS/SSL verified against the system trust store, no downgrade | 3 TLS tests |
| Cleartext transport | Local use is `127.0.0.1` only; public deployment uses Caddy/Let's Encrypt, HSTS | docs/06 |
| Tests reaching real services | `ENVIRONMENT=test` makes SMTP and IMAP clients refuse non-local hosts; fake LLM/search clients; local sink and GreenMail only | `test_smtp_provider_refuses_non_local_hosts_in_tests` |
| Vulnerable dependencies | `pip-audit`: no known vulnerabilities. `npm audit --omit=dev`: 0 (after upgrading react-router). Dev-only tools (vitest, vite dev server) still have advisories that do not ship in the image. | run 2026-10-26 |
| Container hardening | Non-root user, healthchecks, DB bound to localhost, no secrets in the image (`.dockerignore`) | Dockerfile, compose |

## Residual risks (honest list)

1. **Single shared admin secret.** Anyone who gets the password owns the workspace and the data. Use a long password; for a public server, add network-level protection (VPN or an IP allow-list) in front.
2. **No two-factor authentication and no per-user permissions beyond admin/viewer.**
3. **Rate limiting exists only on login.** The API is otherwise unthrottled (acceptable for one user, not for a shared service).
4. **`style-src 'unsafe-inline'`** in the CSP (React inline styles). Scripts are not inline.
5. **Secrets in `.env` are plain text on disk.** Restrict the file (mode 600) and full-disk-encrypt. A secrets manager would be better on a server.
6. **LLM calls send company page text and, if enabled, reply text to the AI provider.** Reply classification by AI is off by default.
7. **The legal rules in the app are a starting point, partly from secondary sources, and not legal advice.** The largest real-world risk is regulatory/complaint exposure from emailing a purchased or scraped list into consent-required countries.
8. **Live behaviour against real providers is untested.** Real mail servers differ (greylisting, odd reply texts, rate limits). The first live batch must be tiny and watched.
9. **Backups hold personal data** and, if restored carelessly, can resurrect opted-out people (see docs/05).
10. **Time-of-check races** are closed by the advisory lock and row locks in the sender, but two *separate databases* (for example a restored copy running next to the live one) would not know about each other. Never run two copies against one mailbox.
