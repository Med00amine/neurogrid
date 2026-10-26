import { ReactNode, useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, download, get, post, put, qs } from "../api";
import { useAuth } from "../auth";
import { Meter } from "../components/charts";
import { useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Confirm, Empty, Field, Icon, Modal, PageHead, Pager, QueryState, StatusBadge, Switch, Tabs, useDebounced, useToast } from "../components/ui";
import { ago, fmtDate, fmtNum, fmtUsd, label } from "../format";

type TabId = "sender" | "sending" | "ai" | "budget" | "compliance" | "connections";

export default function Settings() {
  const [tab, setTab] = useState<TabId>("sender");
  const q = useQuery({ queryKey: ["settings"], queryFn: () => get("/api/settings") });
  const { user } = useAuth();
  return (
    <>
      <PageHead title="Settings">Your sender identity, safety limits, AI and research providers, budgets, privacy rules and connection status. {user?.role !== "admin" && <b>You have read-only access.</b>}</PageHead>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "sender", label: "Sender identity" }, { id: "sending", label: "Sending & limits" }, { id: "ai", label: "Research & AI" }, { id: "budget", label: "Budgets & spend" }, { id: "compliance", label: "Privacy & compliance" }, { id: "connections", label: "Connections & credentials" }]} />
      <div className="mt-16">
        <QueryState q={q}>
          {q.data && (tab === "sender" ? <Sender s={q.data} /> : tab === "sending" ? <Sending s={q.data} /> : tab === "ai" ? <AI s={q.data} /> : tab === "budget" ? <Budget s={q.data} /> : tab === "compliance" ? <Compliance s={q.data} /> : <Connections s={q.data} />)}
        </QueryState>
      </div>
    </>
  );
}

/* ── generic group form ──────────────────────────────────────────────────────────────────────── */
function useGroup(name: string, initial: any) {
  const [v, setV] = useState<any>(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const refresh = useRefresh();
  const toast = useToast();
  useEffect(() => setV(initial), [JSON.stringify(initial)]);
  const save = useMutation({
    mutationFn: (body?: any) => put(`/api/settings/${name}`, body ?? v),
    onSuccess: () => { setErrors({}); toast.ok("Settings saved"); refresh("settings", "sending-status", "overview"); },
    onError: (e: ApiError) => { setErrors(Object.fromEntries(e.fields.map((f) => [f.field, f.message]))); toast.error(e.fields.length ? e.fields.map((f) => f.message).join("; ") : e.message); },
  });
  const set = (k: string) => (e: any) => setV({ ...v, [k]: e.target ? (e.target.type === "number" ? Number(e.target.value) : e.target.value) : e });
  return { v, setV, set, save, errors, dirty: JSON.stringify(v) !== JSON.stringify(initial) };
}

function SaveBar({ g, admin }: { g: ReturnType<typeof useGroup>; admin: boolean }) {
  if (!admin) return null;
  return <div className="row mt-16"><button className="btn primary" disabled={!g.dirty || g.save.isPending} onClick={() => g.save.mutate(undefined)}>{g.save.isPending ? "Saving…" : "Save changes"}</button>{g.dirty && <span className="small muted">Unsaved changes</span>}</div>;
}

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

/* ── Sender identity ─────────────────────────────────────────────────────────────────────────── */
function Sender({ s }: { s: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const g = useGroup("sender", s.groups.sender);
  const { v, set, errors } = g;
  const verified: string[] = v.verified_urls ?? [];
  const toggleVerified = (url: string, on: boolean) => g.setV({ ...v, verified_urls: on ? [...new Set([...verified, url])] : verified.filter((x: string) => x !== url) });
  const urlField = (key: string, lbl: string, ph: string) => (
    <div className="col gap-4" key={key}>
      <Field label={lbl} error={errors[key]}>{(id) => <input id={id} className="input" disabled={!admin} value={v[key] ?? ""} placeholder={ph} onChange={set(key)} />}</Field>
      {v[key] && <label className="checkbox small"><input type="checkbox" disabled={!admin} checked={verified.includes(v[key])} onChange={(e) => toggleVerified(v[key], e.target.checked)} /><span>I confirm this is really my link. Emails may include it only when ticked.</span></label>}
    </div>
  );
  return (
    <div className="col gap-16">
      {s.readiness_dry_run.length > 0 && <Banner tone="warn" title="Still missing for sending"><ul style={{ margin: 0, paddingLeft: 18 }}>{s.readiness_dry_run.map((r: any) => <li key={r.code}>{r.text}</li>)}</ul></Banner>}
      <Card title="Who is sending" subtitle="Recipients see exactly this. Sender identity must be honest and consistent.">
        <div className="grid g2">
          <Field label="Your name" error={errors.display_name}>{(id) => <input id={id} className="input" disabled={!admin} value={v.display_name} onChange={set("display_name")} />}</Field>
          <Field label="Job title shown in the signature">{(id) => <input id={id} className="input" disabled={!admin} value={v.job_title} onChange={set("job_title")} placeholder="e.g. AI & AWS Engineer" />}</Field>
          <Field label="From address" hint="Must be a mailbox you really control. Leave empty to use SMTP_FROM_EMAIL." error={errors.from_email}>{(id) => <input id={id} className="input" type="email" disabled={!admin} value={v.from_email} onChange={set("from_email")} />}</Field>
          <Field label="Reply-to (optional)">{(id) => <input id={id} className="input" type="email" disabled={!admin} value={v.reply_to} onChange={set("reply_to")} />}</Field>
        </div>
      </Card>
      <Card title="Your links" subtitle="The app never invents links. Type yours, then tick to confirm each one.">
        <div className="grid g2">{urlField("website_url", "Website", "https://…")}{urlField("github_url", "GitHub", "https://github.com/…")}{urlField("linkedin_url", "LinkedIn", "https://www.linkedin.com/in/…")}{urlField("portfolio_url", "Portfolio", "https://…")}</div>
      </Card>
      <Card title="Required in every email" subtitle="Commercial email rules (such as CAN-SPAM) and data-protection law expect these.">
        <div className="col gap-12">
          <Field label="Postal address" hint="A valid physical or registered mailbox address." error={errors.postal_address}>{(id) => <textarea id={id} className="textarea" style={{ minHeight: 60 }} disabled={!admin} value={v.postal_address} onChange={set("postal_address")} />}</Field>
          <Field label="Where did you get their address? (one honest sentence)" hint="Shown to every recipient (GDPR transparency). Example: “I found your work address on your company's public website.” Do not write anything that is not true.">{(id) => <textarea id={id} className="textarea" style={{ minHeight: 60 }} disabled={!admin} value={v.data_source_statement} onChange={set("data_source_statement")} />}</Field>
          <Field label="Link to your privacy information (optional)">{(id) => <input id={id} className="input" disabled={!admin} value={v.privacy_url} onChange={set("privacy_url")} />}</Field>
        </div>
      </Card>
      <Card title="What you may say about yourself" subtitle="Drafts can claim only what is listed here.">
        <div className="grid g2">
          <Field label="Services you offer (one per line)">{(id) => <textarea id={id} className="textarea" style={{ minHeight: 140 }} disabled={!admin} value={(v.services ?? []).join("\n")} onChange={(e) => g.setV({ ...v, services: lines(e.target.value).length ? e.target.value.split("\n") : [] })} onBlur={() => g.setV({ ...v, services: lines((v.services ?? []).join("\n")) })} />}</Field>
          <Field label="Proof points (one per line)" hint="Only real, verifiable experience: a project, a result, a certification. Leave empty if unsure. An email will then make no experience claims.">{(id) => <textarea id={id} className="textarea" style={{ minHeight: 140 }} disabled={!admin} value={(v.proof_points ?? []).join("\n")} onChange={(e) => g.setV({ ...v, proof_points: e.target.value.split("\n") })} onBlur={() => g.setV({ ...v, proof_points: lines((v.proof_points ?? []).join("\n")) })} />}</Field>
        </div>
      </Card>
      <SaveBar g={g} admin={admin} />
    </div>
  );
}

/* ── Sending & limits ────────────────────────────────────────────────────────────────────────── */
function Sending({ s }: { s: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const sending = useGroup("sending", s.groups.sending);
  const general = useGroup("general", s.groups.general);
  const toast = useToast();
  const [confirmLive, setConfirmLive] = useState(false);
  const { v, set, errors } = sending;
  const test = useMutation({ mutationFn: () => post("/api/sending/test-smtp", {}), onSuccess: (r: any) => (r.ok ? toast.ok("Connected and logged in. Nothing was sent.") : toast.error(r.error)), onError: (e: any) => toast.error(e.message) });
  const env = s.env;
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const ceiling = Math.floor(v.provider_documented_daily_limit / 2);
  return (
    <div className="col gap-16">
      <Card title="Live sending is protected by three separate switches" subtitle="All three must be on before a real email can leave. Each campaign also needs a typed confirmation.">
        <ol className="list">
          <li className="row between"><span><b>1.</b> Server switch <code>LIVE_SENDING_ENABLED=true</code> in <code>.env</code></span>{env.live_sending_enabled_in_env ? <Badge tone="warn">on</Badge> : <Badge tone="ok">off (safe)</Badge>}</li>
          <li className="row between"><span><b>2.</b> Live mode in this page</span>{v.live_mode_enabled ? <Badge tone="warn">on</Badge> : <Badge tone="ok">off (safe)</Badge>}</li>
          <li className="row between"><span><b>3.</b> Provider is “SMTP” (not “dry run”)</span>{v.provider === "smtp" ? <Badge tone="warn">SMTP</Badge> : <Badge tone="ok">dry run</Badge>}</li>
        </ol>
        {s.readiness_live.length > 0 && <div className="mt-12"><Banner tone="info" title="Before live sending can start">{<ul style={{ margin: 0, paddingLeft: 18 }}>{s.readiness_live.map((r: any) => <li key={r.code}>{r.text}</li>)}</ul>}</Banner></div>}
      </Card>
      <Card title="Provider & safety limits" subtitle="Conservative by design. You can lower them; you cannot raise them above half of what your provider documents.">
        <div className="col gap-16">
          <div className="grid g2">
            <Field label="Sending provider" hint="“Dry run” never contacts any server.">{(id) => <select id={id} className="select" disabled={!admin} value={v.provider} onChange={set("provider")}><option value="dry_run">Dry run (nothing is sent)</option><option value="smtp">SMTP: your own mailbox</option></select>}</Field>
            <Field label="Your provider's documented daily limit" hint="Look it up for YOUR account. Gmail/Workspace: see Google's sending-limits page. Caps below may not exceed half of this." error={errors.provider_documented_daily_limit}>{(id) => <input id={id} className="input" type="number" disabled={!admin} value={v.provider_documented_daily_limit} onChange={set("provider_documented_daily_limit")} />}</Field>
            <Field label={`Emails per day (max ${ceiling})`} hint="Start low (10 to 20) and raise slowly." error={errors.daily_cap}>{(id) => <input id={id} className="input" type="number" min={1} disabled={!admin} value={v.daily_cap} onChange={set("daily_cap")} />}</Field>
            <Field label="Emails per hour" error={errors.hourly_cap}>{(id) => <input id={id} className="input" type="number" min={1} disabled={!admin} value={v.hourly_cap} onChange={set("hourly_cap")} />}</Field>
            <Field label="Minimum seconds between emails" error={errors.min_interval_seconds}>{(id) => <input id={id} className="input" type="number" min={10} disabled={!admin} value={v.min_interval_seconds} onChange={set("min_interval_seconds")} />}</Field>
            <Field label="Time zone for the window and the daily count" error={general.errors.timezone}>{(id) => <input id={id} className="input" list="tzs" disabled={!admin} value={general.v.timezone} onChange={general.set("timezone")} />}</Field>
            <datalist id="tzs">{["UTC", "Europe/Paris", "Europe/Berlin", "Europe/London", "Europe/Madrid", "Europe/Rome", "Europe/Zagreb", "Europe/Belgrade", "Africa/Casablanca", "Africa/Algiers", "Africa/Tunis", "America/New_York", "America/Los_Angeles", "Asia/Dubai"].map((z) => <option key={z} value={z} />)}</datalist>
          </div>
          <div className="field"><span className="lbl">Allowed sending days</span><div className="row wrap">{days.map((d, i) => <label key={d} className="chip"><input type="checkbox" disabled={!admin} checked={v.window_days.includes(i)} onChange={(e) => sending.setV({ ...v, window_days: e.target.checked ? [...v.window_days, i].sort() : v.window_days.filter((x: number) => x !== i) })} />{d}</label>)}</div></div>
          <div className="grid g2"><Field label="Window opens">{(id) => <input id={id} className="input" type="time" disabled={!admin} value={v.window_start} onChange={set("window_start")} />}</Field><Field label="Window closes" error={errors.window_start}>{(id) => <input id={id} className="input" type="time" disabled={!admin} value={v.window_end} onChange={set("window_end")} />}</Field></div>
          <div className="grid g3">
            <Field label="Maximum follow-ups" hint="After the first email.">{(id) => <input id={id} className="input" type="number" min={0} max={3} disabled={!admin} value={v.max_followups} onChange={set("max_followups")} />}</Field>
            <Field label="Days between emails">{(id) => <input id={id} className="input" type="number" min={2} disabled={!admin} value={v.followup_gap_days} onChange={set("followup_gap_days")} />}</Field>
            <Field label="Pause after N errors in a row">{(id) => <input id={id} className="input" type="number" min={1} disabled={!admin} value={v.max_consecutive_temp_failures} onChange={set("max_consecutive_temp_failures")} />}</Field>
          </div>
          {admin && <div className="col gap-8" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            <Switch checked={v.live_mode_enabled} onChange={(on) => (on ? setConfirmLive(true) : sending.setV({ ...v, live_mode_enabled: false }))} label="Allow live (real) campaigns" />
            <span className="xs muted">Turning this on does not send anything. It only allows you to confirm a live campaign later.</span>
          </div>}
        </div>
      </Card>
      <div className="row">{admin && <><button className="btn primary" disabled={!(sending.dirty || general.dirty) || sending.save.isPending} onClick={() => { if (sending.dirty) sending.save.mutate(undefined); if (general.dirty) general.save.mutate(undefined); }}>Save changes</button><button className="btn" disabled={!env.smtp.configured || test.isPending} onClick={() => test.mutate()}><Icon name="link" size={14} />Test SMTP login (sends nothing)</button></>}</div>
      <Card title="What your setup can and cannot confirm">
        <ul className="list small">
          <li><b>Accepted by your mail server:</b> yes. This is what “submitted” means. It does not prove delivery or that anyone read it.</li>
          <li><b>Delivered:</b> {env.imap.configured ? "only when the receiving system returns a delivery receipt, which most do not." : "not available. Connect IMAP to at least see bounces."}</li>
          <li><b>Bounces:</b> {env.imap.configured ? "recorded when a delivery-failure notice reaches your mailbox." : "not detected until IMAP is connected."}</li>
          <li><b>Replies and unsubscribe-by-email:</b> {env.imap.configured ? "read from your mailbox (read-only)." : "not detected until IMAP is connected."}</li>
          <li><b>Smallest upgrade for delivery events:</b> an email service with webhooks. Most forbid cold outreach to bought or scraped lists (Amazon SES and Resend both do, per their published rules), so check the provider's policy first.</li>
        </ul>
      </Card>
      {confirmLive && <Confirm title="Allow live campaigns?" confirmLabel="Allow live campaigns" onConfirm={() => { sending.setV({ ...v, live_mode_enabled: true }); setConfirmLive(false); }} onCancel={() => setConfirmLive(false)}>
        <p>You are about to allow campaigns that send <b>real email to real people</b>. Please make sure you have: filled in your sender identity and postal address, resolved the email history of the people you will contact, reviewed each country's rules (Privacy &amp; compliance), and run a dry run.</p>
        <p className="small sec">You still have to save, set <code>LIVE_SENDING_ENABLED=true</code> on the server, create a campaign and type a confirmation phrase.</p>
      </Confirm>}
    </div>
  );
}

/* ── Research & AI ───────────────────────────────────────────────────────────────────────────── */
function AI({ s }: { s: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const r = useGroup("research", s.groups.research);
  const l = useGroup("llm", s.groups.llm);
  const toast = useToast();
  const testLlm = useMutation({ mutationFn: () => post("/api/settings/test-llm", {}), onSuccess: (x: any) => (x.ok ? toast.ok(`AI works (${x.model}). Cost about ${fmtUsd(x.cost_usd_est)}`) : toast.error(x.error)), onError: (e: any) => toast.error(e.message) });
  const testSearch = useMutation({ mutationFn: () => post("/api/settings/test-search", {}), onSuccess: (x: any) => (x.ok ? toast.ok(`Search works (${x.credits} credit used)`) : toast.error(x.error)), onError: (e: any) => toast.error(e.message) });
  const price = (m: string) => { const p = s.prices.llm[m]; return p ? `$${p.input} in / $${p.output} out per million tokens` : ""; };
  return (
    <div className="col gap-16">
      <Card title="Research" subtitle="How company websites are read.">
        <div className="grid g2">
          <Field label="Web search (adds hiring/news signals)" hint={s.env.search.configured ? "Key found." : "Needs TAVILY_API_KEY in .env. Free tier: 1,000 credits a month."}>{(id) => <select id={id} className="select" disabled={!admin} value={r.v.search_provider} onChange={r.set("search_provider")}><option value="none">Off: website only (free)</option><option value="tavily">Tavily</option></select>}</Field>
          <Field label="Reuse research for (days)" hint="Avoids paying twice for the same company.">{(id) => <input id={id} className="input" type="number" min={1} disabled={!admin} value={r.v.cache_days} onChange={r.set("cache_days")} />}</Field>
          <Field label="Pages read per company">{(id) => <input id={id} className="input" type="number" min={1} max={10} disabled={!admin} value={r.v.max_pages_per_company} onChange={r.set("max_pages_per_company")} />}</Field>
          <Field label="Seconds between page requests" hint="Be polite to small sites.">{(id) => <input id={id} className="input" type="number" min={0} step={0.5} disabled={!admin} value={r.v.delay_between_requests_seconds} onChange={r.set("delay_between_requests_seconds")} />}</Field>
        </div>
        <div className="col gap-8 mt-12"><Switch checked={r.v.use_llm} disabled={!admin} onChange={(on) => r.setV({ ...r.v, use_llm: on })} label="Use AI to extract findings (otherwise fixed rules quote the site)" /><Switch checked={r.v.respect_robots} disabled={!admin} onChange={(on) => r.setV({ ...r.v, respect_robots: on })} label="Respect robots.txt (recommended)" /></div>
        <SaveBar g={r} admin={admin} />
      </Card>
      <Card title="AI model" subtitle={s.env.llm.configured ? "ANTHROPIC_API_KEY found." : "No ANTHROPIC_API_KEY in .env: drafting and research use free fixed rules. Everything still works."}>
        <div className="grid g2">
          <Field label="Provider">{(id) => <select id={id} className="select" disabled={!admin} value={l.v.provider} onChange={l.set("provider")}><option value="anthropic">Anthropic Claude</option><option value="none">None (templates and rules only)</option></select>}</Field>
          <Field label="Thinking effort" hint="Lower is cheaper and faster.">{(id) => <select id={id} className="select" disabled={!admin} value={l.v.effort} onChange={l.set("effort")}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select>}</Field>
          <Field label="Model for research" hint={price(l.v.research_model)}>{(id) => <select id={id} className="select" disabled={!admin} value={l.v.research_model} onChange={l.set("research_model")}>{s.known_models.map((m: string) => <option key={m}>{m}</option>)}</select>}</Field>
          <Field label="Model for writing emails" hint={price(l.v.draft_model)}>{(id) => <select id={id} className="select" disabled={!admin} value={l.v.draft_model} onChange={l.set("draft_model")}>{s.known_models.map((m: string) => <option key={m}>{m}</option>)}</select>}</Field>
        </div>
        <div className="mt-12"><Switch checked={l.v.classify_replies} disabled={!admin} onChange={(on) => l.setV({ ...l.v, classify_replies: on })} label="Ask the AI to suggest a label for replies the rules cannot classify (sends that reply's text to the provider)" /></div>
        <SaveBar g={l} admin={admin} />
        {admin && <div className="row mt-12"><button className="btn" onClick={() => testLlm.mutate()} disabled={testLlm.isPending}>Test AI (about a hundredth of a cent)</button><button className="btn" onClick={() => testSearch.mutate()} disabled={testSearch.isPending}>Test search (1 credit)</button></div>}
      </Card>
      <Card title="Price table used for cost estimates" subtitle={`Read from the providers' pages on ${s.prices.checked_on}. Real billing is on your provider dashboard.`}>
        <div className="table-wrap"><table className="t"><thead><tr><th>Model</th><th className="r">Input / million tokens</th><th className="r">Output / million tokens</th><th>Source</th></tr></thead>
          <tbody>{Object.entries(s.prices.llm).map(([m, p]: any) => <tr key={m}><td className="mono">{m}</td><td className="r num">${p.input}</td><td className="r num">${p.output}</td><td className="small"><a href={p.source} target="_blank" rel="noopener noreferrer">pricing page ↗</a></td></tr>)}
            {Object.entries(s.prices.search).map(([m, p]: any) => <tr key={m}><td>{m} search</td><td className="r num" colSpan={2}>${p.usd_per_credit} per credit; {p.free_credits_per_month} free credits a month</td><td className="small">{p.source}</td></tr>)}</tbody></table></div>
      </Card>
    </div>
  );
}

/* ── Budgets ─────────────────────────────────────────────────────────────────────────────────── */
function Budget({ s }: { s: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const g = useGroup("budget", s.groups.budget);
  return (
    <div className="col gap-16">
      <Card title="This month" subtitle="Estimated from token counts and the price table">
        <div className="grid g2"><Meter label="AI spend" value={s.spend.llm_month_usd} max={g.v.monthly_llm_usd} format={(n) => fmtUsd(n, 4)} /><Meter label="Search spend" value={s.spend.search_month_usd} max={g.v.monthly_search_usd} format={(n) => fmtUsd(n, 4)} /></div>
      </Card>
      <Card title="Hard limits" subtitle="A research or drafting job stops (and says why) rather than exceed these.">
        <div className="grid g3">
          <Field label="AI budget per month (USD)" error={g.errors.monthly_llm_usd}>{(id) => <input id={id} className="input" type="number" min={0} step={1} disabled={!admin} value={g.v.monthly_llm_usd} onChange={g.set("monthly_llm_usd")} />}</Field>
          <Field label="Search budget per month (USD)">{(id) => <input id={id} className="input" type="number" min={0} step={1} disabled={!admin} value={g.v.monthly_search_usd} onChange={g.set("monthly_search_usd")} />}</Field>
          <Field label="Per job limit (USD)" hint="Stops one large batch from running away.">{(id) => <input id={id} className="input" type="number" min={0} step={1} disabled={!admin} value={g.v.per_job_usd} onChange={g.set("per_job_usd")} />}</Field>
        </div>
        <SaveBar g={g} admin={admin} />
      </Card>
      <Banner tone="info" title="Your total budget">A research pass on a small company costs about a tenth of a cent with the default research model; a draft about one cent. Plain email through your own mailbox has no per-email fee. Optional paid parts are the AI key and, if you enable it, search.</Banner>
    </div>
  );
}

/* ── Privacy & compliance ────────────────────────────────────────────────────────────────────── */
function Compliance({ s }: { s: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const toast = useToast();
  const gen = useGroup("general", s.groups.general);
  return (
    <div className="col gap-16">
      <Jurisdictions admin={admin} />
      <Suppressions admin={admin} />
      <Card title="Data retention" subtitle="Third-party text is deleted after these periods. Suppressions, the audit trail and event history are never deleted.">
        <div className="grid g2">
          <Field label="Keep saved web-page text for (days)" hint="Needed only to re-check quotes. Findings and short excerpts stay.">{(id) => <input id={id} className="input" type="number" min={7} disabled={!admin} value={gen.v.source_text_retention_days} onChange={gen.set("source_text_retention_days")} />}</Field>
          <Field label="Keep reply bodies for (days)">{(id) => <input id={id} className="input" type="number" min={30} disabled={!admin} value={gen.v.inbound_retention_days} onChange={gen.set("inbound_retention_days")} />}</Field>
        </div>
        <SaveBar g={gen} admin={admin} />
      </Card>
      {admin && <Card title="Export your data" subtitle="Exports neutralise spreadsheet formulas, so a hostile cell cannot run when you open the file.">
        <div className="row wrap">
          <button className="btn" onClick={() => download("/api/contacts/export?format=csv", "contacts.csv").catch((e) => toast.error(e.message))}><Icon name="download" size={14} />Contacts (CSV)</button>
          <button className="btn" onClick={() => download("/api/contacts/export?format=xlsx", "contacts.xlsx").catch((e) => toast.error(e.message))}><Icon name="download" size={14} />Contacts (Excel)</button>
          <button className="btn" onClick={() => download("/api/suppressions-export", "suppressions.csv").catch((e) => toast.error(e.message))}><Icon name="download" size={14} />Suppression list</button>
          <button className="btn" onClick={() => download("/api/events-export", "outreach_events.csv").catch((e) => toast.error(e.message))}><Icon name="download" size={14} />Outreach event history</button>
        </div>
        <p className="xs muted mt-8">For a full database backup see docs/05-backup-and-recovery.md.</p>
      </Card>}
      {admin && <AuditLog />}
    </div>
  );
}

function Jurisdictions({ admin }: { admin: boolean }) {
  const q = useQuery({ queryKey: ["jurisdictions"], queryFn: () => get("/api/jurisdictions") });
  const [att, setAtt] = useState<any | null>(null);
  const refresh = useRefresh();
  const toast = useToast();
  const rule = useMutation({ mutationFn: (v: { code: string; rule: string }) => put(`/api/jurisdictions/${v.code}/rule`, { rule: v.rule }), onSuccess: () => { refresh("jurisdictions", "contacts"); }, onError: (e: any) => toast.error(e.message) });
  return (
    <Card title="Country rules" subtitle="Which countries you may email, based on where the contact is. Sending needs the rule to allow it AND you to attest, or recorded consent per person." pad={false}>
      <div className="card-pad"><Banner tone="warn" title="Not legal advice">These starting points come from the sources linked below (checked {q.data?.items.find((i: any) => i.sources_checked_on)?.sources_checked_on ?? "recently"}); several are secondary sources. Strict countries (for example Germany, Austria, Italy, Spain, Switzerland) generally need prior consent even for company emails. A purchased or scraped list does not make that lawful. Consider asking a lawyer before the first live batch.</Banner></div>
      <QueryState q={q}>
        <div className="table-wrap"><table className="t"><thead><tr><th>Country</th><th className="r">Contacts</th><th>Rule</th><th>Your attestation</th><th>Notes and sources</th></tr></thead>
          <tbody>{q.data?.items.map((p: any) => (
            <tr key={p.country_code}><td className="nw"><b>{p.name}</b> <span className="muted">{p.country_code}</span></td><td className="r num">{fmtNum(p.contacts)}</td>
              <td>{admin ? <select className="select" style={{ width: 190 }} aria-label={`Rule for ${p.name}`} value={p.rule} onChange={(e) => rule.mutate({ code: p.country_code, rule: e.target.value })}><option value="consent_required">Consent required</option><option value="opt_out_permitted">Opt-out permitted</option><option value="unknown">Not assessed</option></select> : label(p.rule)}</td>
              <td className="nw">{p.rule === "opt_out_permitted" ? (p.attested ? <Badge tone="ok">attested {ago(p.attested_at)}</Badge> : admin ? <button className="btn sm" onClick={() => setAtt(p)}>Attest…</button> : <Badge tone="warn">not attested</Badge>) : p.rule === "consent_required" ? <span className="small muted">needs per-contact consent</span> : <span className="small muted">decide a rule first</span>}</td>
              <td style={{ whiteSpace: "normal", minWidth: 280 }}><div className="small">{p.notes}</div>{p.source_urls?.map((u: string) => <div key={u} className="xs"><a href={u} target="_blank" rel="noopener noreferrer">{u.replace(/^https?:\/\//, "").slice(0, 60)} ↗</a></div>)}</td></tr>))}</tbody></table></div>
      </QueryState>
      {att && <AttestModal p={att} onClose={() => setAtt(null)} />}
    </Card>
  );
}

function AttestModal({ p, onClose }: { p: any; onClose: () => void }) {
  const [note, setNote] = useState("");
  const [ok, setOk] = useState(false);
  const refresh = useRefresh();
  const toast = useToast();
  const m = useMutation({ mutationFn: () => post(`/api/jurisdictions/${p.country_code}/attest`, { note, confirm: ok }), onSuccess: () => { toast.ok(`${p.name} attested`); refresh("jurisdictions", "contacts"); onClose(); }, onError: (e: any) => toast.error(e.message) });
  return (
    <Modal title={`Attest: ${p.name}`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!ok || note.trim().length < 10 || m.isPending} onClick={() => m.mutate()}>Record attestation</button></>}>
      <div className="col gap-12">
        <p className="small sec">{p.notes}</p>
        <Field label="What did you check? (kept in the audit trail)">{(id) => <textarea id={id} className="textarea" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Read the CNIL B2B page; my offer relates to recipients' professional role; emails identify me and offer a clear opt-out." />}</Field>
        <label className="checkbox"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /><span>I have assessed this country's rules for my situation and I meet them. Contacts in {p.name} become eligible for sending once reviewed.</span></label>
      </div>
    </Modal>
  );
}

function Suppressions({ admin }: { admin: boolean }) {
  const [search, setSearch] = useState("");
  const d = useDebounced(search);
  const [page, setPage] = useState(1);
  const [active, setActive] = useState("true");
  const q = useQuery({ queryKey: ["suppressions", d, page, active], queryFn: () => get(`/api/suppressions${qs({ search: d, page, active, page_size: 25 })}`), placeholderData: (x) => x });
  const [adding, setAdding] = useState(false);
  const [lifting, setLifting] = useState<any | null>(null);
  return (
    <Card title="Suppression list (do-not-contact)" subtitle="Applies to every campaign. Imports can add to it but never remove from it. Entries are never deleted, only deactivated with a reason." pad={false}
      actions={admin && <button className="btn sm" onClick={() => setAdding(true)}><Icon name="plus" size={13} />Add</button>}>
      <div className="filters"><input className="input search" placeholder="Search…" aria-label="Search suppressions" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} /><select className="select" aria-label="Active" value={active} onChange={(e) => setActive(e.target.value)}><option value="true">Active</option><option value="false">Lifted</option></select></div>
      <QueryState q={q}>
        {q.data?.items.length === 0 ? <Empty title="Nobody is suppressed yet">People who unsubscribe, bounce permanently or ask not to be contacted appear here automatically.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Address / domain</th><th>Why</th><th>Source</th><th>Added</th><th /></tr></thead>
            <tbody>{q.data?.items.map((s: any) => <tr key={s.id}><td><span className="mono">{s.value}</span> {s.kind === "domain" && <Badge>domain</Badge>}</td><td><StatusBadge status={s.reason} tone="bad" /></td><td className="small"><div className="ell" style={{ maxWidth: 240 }} title={s.source}>{s.source}</div></td><td className="nw">{ago(s.created_at)}</td>
              <td className="r">{admin && s.active && <button className="btn sm ghost" onClick={() => setLifting(s)}>Lift…</button>}{!s.active && <span className="xs muted" title={s.deactivate_reason}>lifted {ago(s.deactivated_at)}</span>}</td></tr>)}</tbody></table></div>)}
      </QueryState>
      <Pager page={page} pageSize={25} total={q.data?.total ?? 0} onPage={setPage} />
      {adding && <AddSuppression onClose={() => setAdding(false)} />}
      {lifting && <LiftSuppression s={lifting} onClose={() => setLifting(null)} />}
    </Card>
  );
}

function AddSuppression({ onClose }: { onClose: () => void }) {
  const [v, setV] = useState({ kind: "email", value: "", reason: "manual", notes: "" });
  const refresh = useRefresh();
  const toast = useToast();
  const m = useMutation({ mutationFn: () => post("/api/suppressions", v), onSuccess: () => { toast.ok("Added. Queued emails to them are cancelled."); refresh("suppressions", "contacts", "overview"); onClose(); }, onError: (e: any) => toast.error(e.message) });
  return (
    <Modal title="Never contact" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn danger" disabled={!v.value.trim() || m.isPending} onClick={() => m.mutate()}>Add to suppression list</button></>}>
      <div className="col gap-12">
        <div className="grid g2"><Field label="What">{(i) => <select id={i} className="select" value={v.kind} onChange={(e) => setV({ ...v, kind: e.target.value })}><option value="email">One email address</option><option value="domain">A whole domain</option></select>}</Field>
          <Field label="Reason">{(i) => <select id={i} className="select" value={v.reason} onChange={(e) => setV({ ...v, reason: e.target.value })}><option value="manual">My decision</option><option value="unsubscribe">Asked to unsubscribe</option><option value="complaint">Complained</option><option value="legal">Legal request</option><option value="do_not_contact">Do not contact</option></select>}</Field></div>
        <Field label={v.kind === "email" ? "Email address" : "Domain (e.g. example.com)"}>{(i) => <input id={i} className="input" value={v.value} onChange={(e) => setV({ ...v, value: e.target.value })} />}</Field>
        <Field label="Notes (optional)">{(i) => <input id={i} className="input" value={v.notes} onChange={(e) => setV({ ...v, notes: e.target.value })} />}</Field>
      </div>
    </Modal>
  );
}

function LiftSuppression({ s, onClose }: { s: any; onClose: () => void }) {
  const [confirmValue, setConfirmValue] = useState("");
  const [reason, setReason] = useState("");
  const [force, setForce] = useState(false);
  const refresh = useRefresh();
  const toast = useToast();
  const m = useMutation({ mutationFn: () => post(`/api/suppressions/${s.id}/lift`, { reason, confirm_value: confirmValue, force }), onSuccess: () => { toast.ok("Suppression lifted and recorded in the audit trail"); refresh("suppressions", "contacts"); onClose(); }, onError: (e: any) => toast.error(e.message) });
  return (
    <Modal title="Lift a suppression?" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Keep suppressed</button><button className="btn danger" disabled={m.isPending || confirmValue.toLowerCase() !== s.value || reason.trim().length < 10 || (s.protected && !force)} onClick={() => m.mutate()}>Lift suppression</button></>}>
      <div className="col gap-12">
        {s.protected ? <Banner tone="bad" title="This person asked not to be contacted">The entry exists because of “{label(s.reason)}”. Lifting it risks contacting someone who opted out. Only do this if they have clearly asked, in writing, to hear from you again.</Banner> : <Banner tone="warn">Lifting allows this address to be contacted again once it passes every other check.</Banner>}
        <Field label={`Type ${s.value} to confirm`}>{(i) => <input id={i} className="input mono" value={confirmValue} onChange={(e) => setConfirmValue(e.target.value)} autoComplete="off" />}</Field>
        <Field label="Reason (kept in the audit trail)">{(i) => <textarea id={i} className="textarea" style={{ minHeight: 60 }} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
        {s.protected && <label className="checkbox"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /><span>I understand and I am certain.</span></label>}
      </div>
    </Modal>
  );
}

function AuditLog() {
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["audit"], queryFn: () => get("/api/audit?page_size=100"), enabled: open });
  return (
    <Card title="Audit log" subtitle="Who changed what. Append-only: the database refuses edits and deletions." actions={<button className="btn sm" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show latest 100"}</button>} pad={false}>
      {open && <QueryState q={q}><div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}><table className="t"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Details</th></tr></thead>
        <tbody>{q.data?.map((a: any) => <tr key={a.id}><td className="nw">{fmtDate(a.at)}</td><td>{a.actor}</td><td className="mono">{a.action}</td><td className="small"><div className="ell" style={{ maxWidth: 360 }} title={JSON.stringify(a.details)}>{JSON.stringify(a.details)}</div></td></tr>)}</tbody></table></div></QueryState>}
    </Card>
  );
}

/* ── Connections & credentials ───────────────────────────────────────────────────────────────── */
function Connections({ s }: { s: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const toast = useToast();
  const e = s.env;
  const smtp = useMutation({ mutationFn: () => post("/api/sending/test-smtp", {}), onSuccess: (r: any) => (r.ok ? toast.ok("SMTP login works. Nothing was sent.") : toast.error(r.error)), onError: (x: any) => toast.error(x.message) });
  const [testTo, setTestTo] = useState(s.groups.sender.from_email || e.smtp.from_email || "");
  const testMail = useMutation({ mutationFn: () => post("/api/sending/test-email", { to: testTo }), onSuccess: (r: any) => (r.ok ? toast.ok(r.note) : toast.error(`${r.note} ${r.response}`)), onError: (x: any) => toast.error(x.message) });
  const imap = useMutation({ mutationFn: () => post("/api/replies/test-imap", {}), onSuccess: (r: any) => (r.ok ? toast.ok(`IMAP works (${r.messages_in_mailbox} messages in the mailbox)`) : toast.error(r.error)), onError: (x: any) => toast.error(x.message) });
  const row = (name: string, ok: boolean, detail?: ReactNode) => <tr key={name}><td className="mono">{name}</td><td>{ok ? <Badge tone="ok"><Icon name="check" size={12} />configured</Badge> : <Badge tone="warn"><Icon name="alert" size={12} />missing</Badge>}</td><td className="small">{detail}</td></tr>;
  return (
    <div className="col gap-16">
      {e.admin_password_in_env && <Banner tone="warn" title="Remove ADMIN_PASSWORD from .env">The first admin password is still stored in plain text in the server's <code>.env</code>. It is only needed on the very first start. Delete that line (and restart) so it is not left lying around. To change your password later use <code>python -m app.cli set-password</code>.</Banner>}
      <Banner tone="info" title="Secrets are never shown here">Passwords and API keys live only in the server's <code>.env</code> file. This page can tell you whether each one is set, not what it is. To change one, edit <code>.env</code> and restart the containers.</Banner>
      <Card title="Credentials & connections" pad={false}>
        <div className="table-wrap"><table className="t"><thead><tr><th>Setting</th><th>Status</th><th>Notes</th></tr></thead><tbody>
          {row("SECRET_KEY", e.secrets.SECRET_KEY, "Signs unsubscribe links. Must be a long random value in production.")}
          {row("SMTP (SMTP_HOST, SMTP_PASSWORD)", e.smtp.configured, e.smtp.host ? `${e.smtp.host}:${e.smtp.port} (${e.smtp.security})` : "To send email through your own mailbox.")}
          {row("IMAP (IMAP_HOST, IMAP_PASSWORD)", e.imap.configured, e.imap.host ? `${e.imap.host}:${e.imap.port}, mailbox ${e.imap.mailbox}` : "To see replies and bounces.")}
          {row("ANTHROPIC_API_KEY", e.secrets.ANTHROPIC_API_KEY, "Optional. Without it, research and drafting use free fixed rules.")}
          {row("TAVILY_API_KEY", e.secrets.TAVILY_API_KEY, "Optional. Adds web-search signals to research.")}
          <tr><td className="mono">LIVE_SENDING_ENABLED</td><td>{e.live_sending_enabled_in_env ? <Badge tone="warn">true</Badge> : <Badge tone="ok">false (safe)</Badge>}</td><td className="small">Server-side master switch for real email.</td></tr>
          <tr><td className="mono">PUBLIC_BASE_URL</td><td><span className="mono small">{e.public_base_url}</span></td><td className="small">{e.https_unsubscribe_possible ? "One-click HTTPS unsubscribe links are included." : "Not HTTPS, so emails carry a mailto: unsubscribe and an in-body opt-out line only. Deploy behind HTTPS to add one-click links."}</td></tr>
        </tbody></table></div>
      </Card>
      {admin && <div className="row"><button className="btn" disabled={!e.smtp.host || smtp.isPending} onClick={() => smtp.mutate()}>Test SMTP login</button><button className="btn" disabled={!e.imap.host || imap.isPending} onClick={() => imap.mutate()}>Test IMAP login</button></div>}
      {admin && e.smtp.configured && (
        <Card title="Send a test email to yourself" subtitle="Proves your mailbox settings work. It can only go to your own addresses, never to a contact.">
          <div className="row wrap"><input className="input grow" type="email" aria-label="Your own address" value={testTo} onChange={(ev) => setTestTo(ev.target.value)} /><button className="btn primary" disabled={!testTo || testMail.isPending} onClick={() => testMail.mutate()}>Send test email</button></div>
          <p className="xs muted mt-8">Then open it in your inbox: check it is not in spam, and use “show original” to confirm SPF and DKIM say PASS.</p>
        </Card>)}
      <p className="xs muted">Environment: {e.environment}.</p>
    </div>
  );
}
