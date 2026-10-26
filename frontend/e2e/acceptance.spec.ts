import { expect, Page, test } from "@playwright/test";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Acceptance scenario, driven through the real UI against the real API, worker and database.
 * Everything runs in DRY-RUN mode against synthetic contacts and a local fixture company website.
 * No email can be sent: no SMTP server is configured and LIVE_SENDING_ENABLED is false.
 */
const SHOTS = path.join(__dirname, "..", "test-results", "screens");
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });

test.describe.configure({ mode: "serial" });

let page: Page;
test.beforeAll(async ({ browser }) => { page = await (await browser.newContext({ viewport: { width: 1360, height: 900 } })).newPage(); });
test.afterAll(async () => { await page.context().close(); });

async function goto(name: string) {
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name }).click();
}

async function waitForJobDone(typeText: RegExp) {
  await goto("Job monitor");
  const row = page.locator("tbody tr", { hasText: typeText }).first();
  await expect(row.getByText(/^(Completed|Partial|Failed)$/)).toBeVisible({ timeout: 90_000 });
  await expect(row.getByText("Completed")).toBeVisible();
}

test("anonymous visitors only see the sign-in page, and a wrong password is refused", async () => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await shot(page, "01-login");
  await page.getByLabel("Email").fill("e2e@example.org");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText(/invalid email or password/i);
  await page.getByLabel("Password").fill("e2e-password-12345");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Safe mode: dry-run only")).toBeVisible();
  await expect(page.getByText("Getting started")).toBeVisible();
  await shot(page, "02-overview-empty");
});

test("empty database: the overview shows honest zeros, not demo numbers", async () => {
  const tile = (label: string) => page.locator(".tile", { hasText: label }).first().locator(".value");
  await expect(tile("Total contacts")).toHaveText("0");
  await expect(tile("Suppressed")).toHaveText("0");
  await expect(page.getByText("Nothing has been sent or received yet.")).toBeVisible();
});

test("fill in the sender identity", async () => {
  await goto("Settings");
  await page.getByLabel("Your name").fill("Test Sender");
  await page.getByLabel("Job title shown in the signature").fill("AI & AWS Engineer");
  await page.getByLabel("From address").fill("sender@sender.example.org");
  await page.getByLabel("Postal address").fill("1 Test Street, Testville 12345");
  await page.getByLabel(/Where did you get their address/).fill("I found your work address on your company website.");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Settings saved")).toBeVisible();
  await expect(page.getByText("Still missing for sending")).toHaveCount(0);
});

test("import contacts through the wizard: preview, mapping, provenance, progress", async () => {
  await goto("Contacts");
  await page.getByRole("button", { name: /Import/ }).click();
  await page.getByLabel(/Spreadsheet file/).setInputFiles(path.join(__dirname, "fixtures", "contacts.csv"));
  await expect(page.getByText("contacts.csv")).toBeVisible();
  await expect(page.getByText(/5 rows/)).toBeVisible();
  await expect(page.getByRole("dialog").getByLabel("Email *")).toHaveValue("email");
  await shot(page, "03-import-mapping");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Where does this list come from?").fill("Synthetic test list created for the acceptance test");
  await page.getByLabel(/I confirm none of them has ever been emailed/).check();
  await page.getByRole("button", { name: /Import 5 rows/ }).click();
  await expect(page.getByText("Import finished")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/3 added|4 added/)).toBeVisible();
  await expect(page.getByText(/1 invalid rows/)).toBeVisible();
  await expect(page.getByText(/No email was sent/)).toBeVisible();
  await shot(page, "04-import-done");
  await page.getByRole("button", { name: "Close" }).or(page.locator(".modal")).first().press("Escape");
  await expect(page.getByRole("cell", { name: "ceo@acme-fixture.example.com", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "someone@gmail.com", exact: true })).toBeVisible();
});

test("contacts: free-mail is excluded, the shared inbox needs review, filters and search work", async () => {
  const row = (email: string) => page.locator("tbody tr", { hasText: email });
  await expect(row("someone@gmail.com")).toContainText("Excluded");
  await expect(row("info@acme-fixture")).toContainText("Review required");
  await expect(row("ceo@acme-fixture")).toContainText("Unreviewed");
  await expect(row("ceo@acme-fixture")).toContainText("Attested unsent");
  await page.getByLabel("Search contacts").fill("dana");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByLabel("Search contacts").fill("");
  await page.getByLabel("Review", { exact: true }).selectOption("excluded");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await shot(page, "05-contacts");
});

test("compliance gate: the contact cannot be mailed until reviewed, country set and the country's rule attested", async () => {
  await page.getByLabel("Search contacts").fill("ceo@acme");
  await page.locator("tbody tr", { hasText: "ceo@acme-fixture" }).click();
  await expect(page.getByText("This person cannot be emailed yet")).toBeVisible();
  await expect(page.getByText(/Eligibility status is 'unreviewed'/)).toBeVisible();
  await expect(page.getByText(/outreach rules not assessed/)).toBeVisible();
  await shot(page, "06-contact-blocked");
  // review decision, with a written reason
  await page.getByLabel("Why? (kept in the audit trail)").fill("Reviewed the company and the role; relevant to my services");
  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByText("Saved to the audit trail")).toBeVisible();
  await page.keyboard.press("Escape");

  // set the country via a bulk action
  await page.locator("tbody tr", { hasText: "ceo@acme-fixture" }).getByRole("checkbox").check();
  await page.getByRole("button", { name: "Bulk actions…" }).click();
  await page.getByRole("dialog").getByLabel("Action", { exact: true }).selectOption("set_country");
  await page.getByLabel("2-letter country code").fill("FR");
  await page.getByLabel("Why is this the country?").fill("Company imprint shows France");
  await page.getByLabel(/I understand this changes/).check();
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText(/1 changed/)).toBeVisible();

  // attest France in Settings
  await goto("Settings");
  await page.getByRole("tab", { name: "Privacy & compliance" }).click();
  await expect(page.getByText("Not legal advice")).toBeVisible();
  await page.locator("tbody tr", { hasText: "France" }).getByRole("button", { name: "Attest…" }).click();
  await page.getByLabel("What did you check? (kept in the audit trail)").fill("Read the CNIL page; my offer relates to recipients' professional role");
  await page.getByLabel(/I have assessed this country's rules/).check();
  await page.getByRole("button", { name: "Record attestation" }).click();
  await expect(page.getByText("France attested")).toBeVisible();
  await expect(page.locator("tbody tr", { hasText: "Germany" })).toContainText("Consent required");
  await shot(page, "07-country-rules");

  // the contact now passes every check
  await goto("Contacts");
  await page.getByLabel("Search contacts").fill("ceo@acme");
  await page.locator("tbody tr", { hasText: "ceo@acme-fixture" }).click();
  await expect(page.getByText("This person passes every send-time check")).toBeVisible();
  await page.keyboard.press("Escape");
});

test("research a company from its (fixture) website, with sources and verified quotes", async () => {
  await goto("Company research");
  await page.getByLabel("Search companies").fill("acme");
  await page.locator("tbody tr", { hasText: "acme-fixture.example.com" }).click();
  await page.getByLabel("Official website").fill("http://127.0.0.1:8781");
  await page.getByRole("button", { name: "Save website" }).click();
  await expect(page.getByText(/Website saved/)).toBeVisible();
  await page.getByRole("button", { name: "Research this company" }).click();
  await expect(page.getByText(/Rule-based extraction \(free\)/)).toBeVisible();
  await page.getByRole("button", { name: "Start research" }).click();
  await waitForJobDone(/Research 1 company/);

  await goto("Company research");
  await page.getByLabel("Search companies").fill("acme");
  await page.locator("tbody tr", { hasText: "acme-fixture.example.com" }).click();
  await expect(page.getByText("What was done")).toBeVisible();
  await expect(page.getByText(/page\(s\) read/)).toBeVisible();
  // sources with retrieval time + the saved-text viewer
  await expect(page.getByText(/retrieved .* · HTTP 200/).first()).toBeVisible();
  await expect(page.getByText("suspicious text")).toBeVisible(); // the fixture has a prompt-injection page
  // findings: verbatim quote, verified mark, fact vs inference
  await expect(page.getByText("✓ verified").first()).toBeVisible();
  await expect(page.getByText("Stated on the site").first()).toBeVisible();
  await expect(page.getByText("Inference (not stated)").first()).toBeVisible();
  await expect(page.getByText(/won a \$5 million/)).toHaveCount(0); // the planted claim never became a finding
  await shot(page, "08-company-research");
  await page.getByRole("button", { name: /Approve \d+ verified facts/ }).click();
  await expect(page.getByText(/verified facts approved/)).toBeVisible();
  // an inference needs its own explicit approval
  const inference = page.locator("[data-testid=finding][data-category=fit_reason]").first();
  await inference.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(inference.getByText("Approved", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
});

test("the lead is scored with an explainable breakdown", async () => {
  await goto("Leads & scoring");
  await expect(page.getByText(/does not predict|not a prediction/i).first()).toBeVisible();
  const row = page.locator("tbody tr", { hasText: "ceo@acme-fixture" });
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.getByRole("heading", { name: "Why this score" })).toBeVisible();
  for (const c of ["Service match", "Problem evidence", "Decision relevance", "Evidence quality", "Contact reliability"]) await expect(page.getByText(c, { exact: true })).toBeVisible();
  await expect(page.getByText(/Job title unknown/)).toBeVisible();
  await shot(page, "09-lead-score");
  await page.keyboard.press("Escape");
});

test("generate a draft, inspect the evidence behind it, approve the exact text", async () => {
  await goto("Email studio");
  await page.getByRole("button", { name: /Generate drafts/ }).click();
  await page.getByLabel(/Use AI for natural wording/).uncheck();
  await expect(page.getByText(/free template mode/)).toBeVisible();
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await waitForJobDone(/Generate 1 draft/);

  await goto("Email studio");
  await page.locator("tbody tr", { hasText: "Chris Martin" }).click();
  await expect(page.getByText("Why this prospect is a fit")).toBeVisible();
  await expect(page.getByText("Which claim rests on what")).toBeVisible();
  await expect(page.getByText("used in this email").first()).toBeVisible();
  await expect(page.getByText(/retrieved/).first()).toBeVisible();
  await page.getByRole("button", { name: "Exactly what will be sent" }).click();
  await expect(page.getByText("1 Test Street, Testville 12345")).toBeVisible();
  await expect(page.getByText(/reply with .unsubscribe./)).toBeVisible();
  await expect(page.getByText("I found your work address on your company website.")).toBeVisible();
  await shot(page, "10-draft-preview");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  // editing voids nothing yet (not approved); then approve
  const ack = page.getByLabel(/I have read the warnings/);
  if (await ack.count()) await ack.check();
  await page.getByRole("button", { name: "Approve this exact text" }).click();
  await expect(page.getByText(/Approved\. This exact text can now be used/)).toBeVisible();
  await expect(page.getByText("approval matches the text")).toBeVisible();
  // an edit voids the approval
  const body = page.getByLabel("Message", { exact: true });
  await body.fill((await body.inputValue()) + "\n\nP.S. Happy to share a short summary.");
  await page.getByRole("button", { name: "Save edits" }).click();
  await expect(page.getByText(/Saved as a new version/)).toBeVisible();
  await expect(page.getByText("approval matches the text")).toHaveCount(0);
  // the hand-written paragraph is flagged, so approving needs an explicit acknowledgement
  await expect(page.getByText(/written or edited by you and has not been checked/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve this exact text" })).toBeDisabled();
  await page.getByLabel(/I have read the warnings/).check();
  await page.getByRole("button", { name: "Approve this exact text" }).click();
  await expect(page.getByText("approval matches the text")).toBeVisible();
  await page.keyboard.press("Escape");
});

test("a dry-run campaign: preview, exclusions, explicit confirmation, queue", async () => {
  await goto("Campaigns");
  await expect(page.getByText(/Nothing is sent until you confirm/)).toBeVisible();
  await page.getByRole("button", { name: "New campaign" }).click();
  await page.getByRole("dialog").getByLabel("Name", { exact: true }).fill("Acceptance rehearsal");
  await expect(page.getByLabel(/Live \(real emails\)|Live/).last()).toBeDisabled(); // live is switched off
  await page.getByRole("button", { name: "Create draft campaign" }).click();
  await expect(page.getByText("Dry run: nothing is sent")).toBeVisible();
  await expect(page.locator(".card", { hasText: "Will be sent" }).first()).toContainText("1");
  await shot(page, "11-campaign-draft");
  // before confirmation, nothing is queued
  await page.getByRole("tab", { name: /Queue/ }).click();
  await expect(page.getByText("Nothing queued yet")).toBeVisible();
  await page.getByRole("button", { name: /Review & confirm/ }).click();
  const confirm = page.getByRole("button", { name: "Start dry run" });
  await expect(confirm).toBeDisabled(); // needs the typed phrase
  await page.getByLabel("Confirmation phrase").fill("DRY RUN 1");
  await confirm.click();
  await expect(page.getByText("Dry run started")).toBeVisible();
  await page.getByRole("tab", { name: /Queue/ }).click();
  await expect(page.locator("tbody tr", { hasText: "ceo@acme-fixture" })).toContainText("Submitted", { timeout: 30_000 });
  await page.locator("tbody tr", { hasText: "ceo@acme-fixture" }).click();
  await expect(page.getByText(/The exact text that was sent/)).toBeVisible();
  await expect(page.getByText("dryrun-")).toBeVisible();
  await shot(page, "12-campaign-queue");
  await page.keyboard.press("Escape");
});

test("the overview reflects the work and labels simulated numbers; pipeline shows the card", async () => {
  await goto("Overview");
  await expect(page.locator(".tile", { hasText: "Total contacts" }).locator(".value")).toHaveText("4");
  await expect(page.locator(".tile", { hasText: "Drafts approved" }).locator(".value")).toHaveText("1");
  const sim = page.locator(".tile", { hasText: "Submitted" }).filter({ has: page.locator(".basis.simulated") }).first();
  await expect(sim.locator(".value")).toHaveText("1");
  await expect(page.locator(".tile", { hasText: "accepted by mail server" }).locator(".value")).toHaveText("0"); // real sends: none
  await expect(page.getByText("Not proof of delivery")).toBeVisible();
  await shot(page, "13-overview-after");
  await goto("Sales pipeline");
  await expect(page.locator(".lane", { hasText: "Approved" }).getByText("Chris Martin")).toBeVisible();
  await shot(page, "14-pipeline");
});

test("settings never display secret values, and live sending stays protected", async () => {
  await goto("Settings");
  await page.getByRole("tab", { name: "Connections & credentials" }).click();
  await expect(page.getByText("Secrets are never shown here")).toBeVisible();
  const html = await page.content();
  expect(html).not.toContain("e2e-password-12345");
  await page.getByRole("tab", { name: "Sending & limits" }).click();
  await expect(page.getByText("Live sending is protected by three separate switches")).toBeVisible();
  await expect(page.getByText("off (safe)").first()).toBeVisible();
  await shot(page, "15-settings-sending");
});

test("job monitor lists completed jobs with progress and timings", async () => {
  await goto("Job monitor");
  const done = page.locator("tbody tr", { hasText: /Import contacts/ }).first();
  await expect(done).toContainText("Completed");
  await done.click();
  await expect(page.getByText("What to do:")).toHaveCount(0);
  await expect(page.getByText(/rows|done/).first()).toBeVisible();
  await shot(page, "16-jobs");
  await page.keyboard.press("Escape");
});

test("dark mode and a phone-sized screen both work without horizontal scrolling", async () => {
  await page.getByRole("button", { name: "Toggle light and dark theme" }).click();
  await goto("Overview");
  await shot(page, "17-overview-dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
  await page.waitForTimeout(500); // let the sidebar finish sliding out before measuring
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await shot(page, "18-overview-mobile");
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.waitForTimeout(400);
  await shot(page, "18b-mobile-menu-open");
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Contacts" }).click();
  await page.waitForTimeout(400);
  await expect(page.getByRole("heading", { name: "Contacts" })).toBeVisible();
  const overflow2 = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow2).toBeLessThanOrEqual(1);
  await shot(page, "19-contacts-mobile");
});

test("signing out ends the session", async () => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.getByRole("button", { name: "Open menu" }).isVisible().catch(() => false);
  await page.getByRole("button", { name: /Sign out/ }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  const r = await page.request.get("/api/overview");
  expect(r.status()).toBe(401);
});
