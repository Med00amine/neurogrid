import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { get } from "../api";
import { Columns, HBars, LineChart, Meter, StatTile } from "../components/charts";
import { useOpenContact } from "../components/ContactDrawer";
import { Banner, Card, Icon, PageHead, QueryState, StatusBadge } from "../components/ui";
import { ago, fmtNum, fmtUsd, label } from "../format";

const M = (m: any) => (m ? m.value : 0);

export default function Overview() {
  const nav = useNavigate();
  const openContact = useOpenContact();
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => get("/api/overview"), refetchInterval: 15_000 });
  const ts = useQuery({ queryKey: ["timeseries"], queryFn: () => get("/api/overview/timeseries?days=14"), refetchInterval: 60_000 });
  const leads = useQuery({ queryKey: ["leads-summary"], queryFn: () => get("/api/leads/summary") });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => get("/api/settings") });
  const d = ov.data;

  return (
    <>
      <PageHead title="Overview">Everything on this page is counted from your database. Labels show where each number comes from: <b>estimate</b> means calculated from a price table, <b>simulated</b> means a dry run, <b>mail-system confirmed</b> means the receiving mail system told us.</PageHead>
      <QueryState q={ov}>
        {d && (
          <div className="col gap-16">
            {d.attention.length > 0 && (
              <Card title="Needs your attention">
                <ul className="list">
                  {d.attention.map((a: any) => (
                    <li key={a.kind} className="row between"><span className="row"><Icon name="alert" size={16} />{a.text}</span><Link className="btn sm" to={a.link}>Open</Link></li>
                  ))}
                </ul>
              </Card>
            )}

            <Checklist d={d} s={settings.data} />

            <section aria-label="Contacts" className="col">
              <h2>Contacts</h2>
              <div className="grid g4">
                <StatTile label="Total contacts" value={fmtNum(M(d.contacts.total))} onClick={() => nav("/contacts")} />
                <StatTile label="Companies" value={fmtNum(M(d.contacts.companies))} />
                <StatTile label="Eligible" value={fmtNum(M(d.contacts.eligible))} sub="marked eligible by you" onClick={() => nav("/contacts?eligibility=eligible")} />
                <StatTile label="Cleared to send now" value={fmtNum(M(d.contacts.sendable_now))} sub="eligible + history attested" />
                <StatTile label="Not reviewed yet" value={fmtNum(M(d.contacts.unreviewed))} onClick={() => nav("/contacts?eligibility=unreviewed")} />
                <StatTile label="Review required" value={fmtNum(M(d.contacts.review_required))} onClick={() => nav("/contacts?eligibility=review_required")} />
                <StatTile label="Excluded" value={fmtNum(M(d.contacts.excluded))} />
                <StatTile label="Suppressed" value={fmtNum(M(d.contacts.suppressed))} sub="never contacted again" />
              </div>
              {M(d.contacts.history_unknown) > 0 && <Banner tone="warn" title={`${fmtNum(M(d.contacts.history_unknown))} contacts have an unknown email history`}>A fresh database does not mean nobody was emailed before. These contacts are blocked from sending until you import your sent log or attest that they were never contacted (Contacts → Import / bulk actions).</Banner>}
            </section>

            <div className="grid g-wide-narrow">
              <Card title="Contacts by review state" subtitle="Where your list stands before anything can be sent">
                <HBars total={M(d.contacts.total)} rows={[
                  { name: "Not reviewed", value: M(d.contacts.unreviewed) },
                  { name: "Review required", value: M(d.contacts.review_required) },
                  { name: "Eligible", value: M(d.contacts.eligible) },
                  { name: "Excluded", value: M(d.contacts.excluded) },
                ]} />
              </Card>
              <Card title="Spend this month" subtitle="Estimates from the price table in Settings">
                {settings.data ? (
                  <div className="col gap-16">
                    <Meter label="AI (LLM) spend" value={d.spend.llm_month_usd.value} max={settings.data.groups.budget.monthly_llm_usd} format={(n) => fmtUsd(n)} />
                    <Meter label="Search spend" value={d.spend.search_month_usd.value} max={settings.data.groups.budget.monthly_search_usd} format={(n) => fmtUsd(n)} />
                    <p className="xs muted">Sending email through your own mailbox has no per-email charge from this app.</p>
                  </div>
                ) : <p className="muted small">Loading…</p>}
              </Card>
            </div>

            <section aria-label="Research and drafts" className="col">
              <h2>Research &amp; drafts</h2>
              <div className="grid g4">
                <StatTile label="Research jobs queued" value={M(d.research.queued)} onClick={() => nav("/jobs")} />
                <StatTile label="Running" value={M(d.research.running)} />
                <StatTile label="Completed" value={M(d.research.completed)} sub={`${M(d.research.companies_researched)} companies researched`} />
                <StatTile label="Failed" value={M(d.research.failed)} tone={M(d.research.failed) ? "bad" : undefined} />
                <StatTile label="Drafts awaiting review" value={M(d.drafts.awaiting_review)} onClick={() => nav("/studio")} />
                <StatTile label="Drafts approved" value={M(d.drafts.approved)} />
              </div>
            </section>

            <section aria-label="Email delivery" className="col">
              <h2>Email (real sends)</h2>
              <div className="grid g4">
                {(["queued", "submitted", "delivered", "bounced", "rejected", "failed", "unknown"] as const).map((k) => (
                  <StatTile key={k} label={label(k === "submitted" ? "accepted by mail server" : k)} value={fmtNum(M(d.email_live[k]))} basis={d.email_live[k].basis}
                    sub={k === "submitted" ? "Not proof of delivery" : k === "delivered" ? "Rare with plain SMTP" : k === "unknown" ? "Needs manual review" : undefined}
                    tone={k === "unknown" && M(d.email_live[k]) ? "bad" : undefined} />
                ))}
              </div>
              <h3 className="mt-8">Rehearsals (dry runs: nothing was sent)</h3>
              <div className="grid g4">
                {Object.entries(d.email_dry_run).length === 0 ? <p className="muted small">No rehearsal has been run yet.</p> :
                  Object.entries(d.email_dry_run).map(([k, v]: any) => <StatTile key={k} label={label(k)} value={fmtNum(v.value)} basis="simulated" />)}
              </div>
            </section>

            <section aria-label="Outcomes" className="col">
              <h2>Replies &amp; deals</h2>
              <div className="grid g4">
                <StatTile label="Replies" value={M(d.replies.replies)} onClick={() => nav("/replies")} />
                <StatTile label="Positive replies" value={M(d.replies.positive)} sub="interested / question / referral" />
                <StatTile label="Meetings" value={M(d.pipeline.meetings)} onClick={() => nav("/pipeline")} />
                <StatTile label="Proposals sent" value={M(d.pipeline.proposals)} />
                <StatTile label="Deals won" value={M(d.pipeline.won)} />
                <StatTile label="Won value" value={fmtNum(M(d.pipeline.won_value))} sub="as entered, mixed currencies" />
                <StatTile label="Unsubscribes" value={M(d.replies.unsubscribes)} />
              </div>
            </section>

            <div className="grid g2">
              <Card title="Pipeline" subtitle="Contacts per stage">
                <HBars rows={["researched", "qualified", "draft_ready", "approved", "contacted", "replied", "interested", "meeting_scheduled", "proposal_sent", "won", "lost"].map((s) => ({ name: label(s), value: (d.pipeline.by_stage.value[s] ?? 0) }))} emptyText="No contact has moved into the pipeline yet." />
              </Card>
              <Card title="Lead score distribution" subtitle="Evidence-based ranking, not a sales prediction">
                {leads.data && leads.data.scored_contacts > 0 ? <Columns data={leads.data.histogram.map((h: any) => ({ label: h.range, value: h.count }))} unit="contacts" /> : <p className="muted small">No contact has been scored yet. Scores appear after you research a company and approve some findings.</p>}
              </Card>
            </div>

            <Card title="Activity, last 14 days" subtitle="Emails accepted by your mail server, and replies received">
              {ts.data && ts.data.series.every((s: any) => s.values.every((v: number) => v === 0))
                ? <p className="muted small">Nothing has been sent or received yet.</p>
                : ts.data && <LineChart labels={ts.data.labels} series={[{ ...ts.data.series[0], color: "var(--series-1)" }, { ...ts.data.series[1], color: "var(--series-2)" }]} />}
            </Card>

            <Card title="Recent events" subtitle="The append-only history of everything that happened" pad={false}>
              {d.recent_events.length === 0 ? <div className="card-pad muted small">No events yet.</div> : (
                <ul className="list" style={{ padding: "0 16px" }}>
                  {d.recent_events.map((e: any) => (
                    <li key={e.id} className="row between wrap">
                      <span><b>{label(e.type)}</b> <span className="muted small">· {e.source}</span></span>
                      <span className="row">{e.contact_id && <button className="btn sm ghost" onClick={() => openContact(e.contact_id)}>Contact #{e.contact_id}</button>}<span className="muted small">{ago(e.at)}</span></span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}
      </QueryState>
    </>
  );
}

function Checklist({ d, s }: { d: any; s: any }) {
  const total = M(d.contacts.total);
  const steps = [
    { done: total > 0, text: "Import your contacts", to: "/contacts" },
    { done: !!s && s.readiness_dry_run.length === 0, text: "Fill in your sender identity, postal address and data-source sentence", to: "/settings" },
    { done: total > 0 && M(d.contacts.history_unknown) < total, text: "Resolve email history (import your sent log, or attest none were emailed)", to: "/contacts" },
    { done: M(d.contacts.eligible) > 0, text: "Review a small batch and mark them eligible", to: "/contacts" },
    { done: M(d.research.companies_researched) > 0, text: "Research those companies and approve the findings", to: "/research" },
    { done: M(d.drafts.approved) > 0, text: "Generate and approve drafts", to: "/studio" },
    { done: Object.keys(d.email_dry_run).length > 0, text: "Run a dry-run campaign (nothing is sent)", to: "/campaigns" },
    { done: !!s && s.env.smtp.configured && s.env.imap.configured, text: "Connect your mailbox (SMTP to send, IMAP to see replies and bounces)", to: "/settings" },
    { done: !!s && s.env.live_sending_enabled_in_env && s.groups.sending.live_mode_enabled, text: "Only when you are ready: enable live sending", to: "/settings" },
  ];
  const done = steps.filter((x) => x.done).length;
  if (done === steps.length) return null;
  const next = steps.findIndex((x) => !x.done);
  return (
    <Card title="Getting started" subtitle={`${done} of ${steps.length} steps done. These are computed from your real data.`}>
      <div role="list">
        {steps.map((st, i) => (
          <div role="listitem" key={i} className={`check-item ${st.done ? "done" : ""}`}>
            <span className="dot">{st.done ? <Icon name="check" size={12} /> : i + 1}</span>
            <span className="grow" style={{ fontWeight: i === next ? 650 : 400 }}>{st.text}</span>
            {i === next && <Link className="btn sm primary" to={st.to}>Do this next</Link>}
          </div>
        ))}
      </div>
    </Card>
  );
}
