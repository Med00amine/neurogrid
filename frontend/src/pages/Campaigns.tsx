import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post, qs } from "../api";
import { useAuth } from "../auth";
import { useOpenContact, useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Confirm, Empty, Field, Icon, Modal, PageHead, Pager, Progress, QueryState, SlideOver, StatusBadge, Tabs, useToast } from "../components/ui";
import { ago, fmtDate, fmtNum, label } from "../format";

export default function Campaigns() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [open, setOpen] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const q = useQuery({ queryKey: ["campaigns"], queryFn: () => get("/api/campaigns"), refetchInterval: 5000 });
  const st = useQuery({ queryKey: ["sending-status"], queryFn: () => get("/api/sending/status") });
  return (
    <>
      <PageHead title="Campaigns" actions={admin && <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" size={14} />New campaign</button>}>
        A campaign takes people who passed every check and have an approved draft, and sends one at a time within your daily cap and sending window. <b>Nothing is sent until you confirm</b>, and every send re-checks eligibility and suppression.
      </PageHead>
      {st.data && (
        <div className="grid g3" style={{ marginBottom: 16 }}>
          <Card title="Sent today" subtitle="Real emails accepted by your mail server"><div className="row between"><b style={{ fontSize: 26 }} className="num">{st.data.sent_today}</b><span className="muted">cap {st.data.daily_cap}/day</span></div><Progress value={st.data.sent_today} max={st.data.daily_cap} /></Card>
          <Card title="Sending window"><div className="small">{st.data.window.start}–{st.data.window.end}, {st.data.window.days.length === 7 ? "every day" : `${st.data.window.days.length} days a week`}<br />{st.data.in_window_now ? <Badge tone="ok">open now</Badge> : <Badge>closed now</Badge>}</div></Card>
          <Card title="What your mail provider can confirm"><div className="small sec">{st.data.what_is_confirmable.submitted}</div></Card>
        </div>)}
      <Card pad={false}>
        <QueryState q={q}>
          {q.data?.length === 0 ? <Empty title="No campaigns yet">Start with a <b>dry run</b>: it rehearses everything (selection, checks, queue) without sending anything.</Empty> : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Campaign</th><th>Mode</th><th>Status</th><th style={{ width: 200 }}>Progress</th><th className="r">Recipients</th><th>Created</th></tr></thead>
              <tbody>{q.data?.map((c: any) => {
                const done = (c.message_counts.submitted ?? 0) + (c.message_counts.delivered ?? 0) + (c.message_counts.replied ?? 0) + (c.message_counts.bounced ?? 0) + (c.message_counts.rejected ?? 0) + (c.message_counts.failed ?? 0) + (c.message_counts.cancelled ?? 0) + (c.message_counts.suppressed ?? 0);
                const total = Object.values(c.message_counts).reduce((a: number, b: any) => a + b, 0) as number;
                return (
                  <tr key={c.id} className="click" onClick={() => setOpen(c.id)}>
                    <td><b>{c.name}</b>{c.step > 1 && <Badge>follow-up {c.step - 1}</Badge>}</td>
                    <td>{c.mode === "live" ? <Badge tone="warn"><Icon name="alert" size={12} />Live</Badge> : <StatusBadge status="dry_run" text="Dry run" />}</td>
                    <td><StatusBadge status={c.status} />{c.paused_reason && <div className="xs muted ell" style={{ maxWidth: 220 }} title={c.paused_reason}>{c.paused_reason}</div>}</td>
                    <td>{total > 0 ? <><Progress value={done} max={total} /><span className="xs muted">{done} of {total} done</span></> : <span className="muted small">not started</span>}</td>
                    <td className="r num">{c.recipient_counts.included}<span className="muted"> / {c.recipient_counts.excluded} excluded</span></td><td className="nw">{ago(c.created_at)}</td></tr>);
              })}</tbody>
            </table></div>)}
        </QueryState>
      </Card>
      {open && <CampaignPanel id={open} onClose={() => setOpen(null)} admin={admin} />}
      {creating && <NewCampaign onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setOpen(id); }} liveReady={!!st.data?.live_mode_enabled && !!st.data?.live_enabled_in_env} />}
    </>
  );
}

function NewCampaign({ onClose, onCreated, liveReady }: { onClose: () => void; onCreated: (id: number) => void; liveReady: boolean }) {
  const segs = useQuery({ queryKey: ["segments"], queryFn: () => get("/api/segments") });
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"dry_run" | "live">("dry_run");
  const [step, setStep] = useState(1);
  const [segment, setSegment] = useState("");
  const refresh = useRefresh();
  const toast = useToast();
  const create = useMutation({
    mutationFn: () => post("/api/campaigns", { name, mode, step, segment_id: segment ? Number(segment) : null, filter: step === 1 ? { stage: "approved", ...(segment ? {} : {}) } : { stage: "contacted" } }),
    onSuccess: (c: any) => { toast.ok("Campaign created as a draft. Review the recipients before confirming."); refresh("campaigns"); onCreated(c.id); },
    onError: (e: any) => toast.error(e.message),
  });
  return (
    <Modal title="New campaign" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={name.trim().length < 2 || create.isPending} onClick={() => create.mutate()}>Create draft campaign</button></>}>
      <div className="col gap-12">
        <Field label="Name">{(i) => <input id={i} className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. AI consultancies, test batch 1" />}</Field>
        <fieldset className="col gap-8" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small" style={{ fontWeight: 650 }}>Mode</legend>
          <label className="card card-pad checkbox"><input type="radio" name="mode" checked={mode === "dry_run"} onChange={() => setMode("dry_run")} /><span><b>Dry run</b> (recommended first)<br /><span className="small sec">Runs the whole flow with a fake provider. Nothing is sent and contacts are not changed.</span></span></label>
          <label className="card card-pad checkbox" style={{ opacity: liveReady ? 1 : 0.6 }}><input type="radio" name="mode" disabled={!liveReady} checked={mode === "live"} onChange={() => setMode("live")} /><span><b>Live</b> (real emails)<br /><span className="small sec">{liveReady ? "Sends real email through your mailbox after you type a confirmation phrase." : "Disabled: live sending is switched off. Enable it in Settings and in the server's .env when you are ready."}</span></span></label>
        </fieldset>
        <Field label="Which email in the sequence?">{(i) => <select id={i} className="select" value={step} onChange={(e) => setStep(Number(e.target.value))}><option value={1}>First email</option><option value={2}>Follow-up 1</option><option value={3}>Follow-up 2</option></select>}</Field>
        <Field label="Segment (optional)" hint="Only contacts whose stage is “approved” (an approved draft exists) are considered, further limited by this segment.">{(i) => <select id={i} className="select" value={segment} onChange={(e) => setSegment(e.target.value)}><option value="">Every approved contact</option>{segs.data?.map((s: any) => <option key={s.id} value={s.id}>{s.name} ({s.members})</option>)}</select>}</Field>
        <Banner tone="info">You will review every recipient, every exclusion and the exact timeline before anything can start.</Banner>
      </div>
    </Modal>
  );
}

function CampaignPanel({ id, onClose, admin }: { id: number; onClose: () => void; admin: boolean }) {
  const q = useQuery({ queryKey: ["campaign", id], queryFn: () => get(`/api/campaigns/${id}`), refetchInterval: 4000 });
  const [tab, setTab] = useState<"recipients" | "queue" | "settings">("recipients");
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const refresh = useRefresh();
  const toast = useToast();
  const c = q.data;
  const act = (path: string, msg: string) => ({ mutationFn: () => post(`/api/campaigns/${id}/${path}`, {}), onSuccess: () => { toast.ok(msg); refresh("campaign", "campaigns", "overview"); }, onError: (e: any) => toast.error(e.message) });
  const refreshM = useMutation(act("refresh", "Recipients re-checked"));
  const pause = useMutation(act("pause", "Campaign paused"));
  const resume = useMutation(act("resume", "Campaign resumed"));
  const cancel = useMutation({ ...act("cancel", "Campaign cancelled. Queued emails were cancelled."), onSuccess: () => { toast.ok("Campaign cancelled. Queued emails were cancelled."); setCancelling(false); refresh("campaign", "campaigns", "overview"); } });
  const e = c?.estimate;
  return (
    <SlideOver wide onClose={onClose} title={c?.name ?? "Campaign"} subtitle={c && <span className="row wrap">{c.mode === "live" ? <Badge tone="warn"><Icon name="alert" size={12} />Live: real emails</Badge> : <StatusBadge status="dry_run" text="Dry run: nothing is sent" />}<StatusBadge status={c.status} />{c.step > 1 && <Badge>follow-up {c.step - 1}</Badge>}</span>}>
      <QueryState q={q}>
        {c && e && (
          <>
            {c.paused_reason && c.status === "paused" && <Banner tone="warn" title="Paused">{c.paused_reason}</Banner>}
            {c.gate && !c.gate.allowed && c.status === "running" && <Banner tone="info" title="Waiting">Next send: {c.gate.reason}{c.gate.retry_at ? `, around ${fmtDate(c.gate.retry_at)}` : ""}.</Banner>}
            {c.status === "draft" && c.live_gate && c.mode === "live" && <Banner tone="bad" title="Live sending is not enabled">{c.live_gate}</Banner>}
            {c.status === "draft" && c.readiness.length > 0 && <Banner tone={c.mode === "live" ? "bad" : "warn"} title="Before this can be confirmed"><ul style={{ margin: 0, paddingLeft: 18 }}>{c.readiness.map((r: any) => <li key={r.code}>{r.text}</li>)}</ul></Banner>}

            <div className="grid g4">
              <Card><div className="small sec">Will be sent</div><b style={{ fontSize: 24 }} className="num">{e.included}</b></Card>
              <Card><div className="small sec">Excluded</div><b style={{ fontSize: 24 }} className="num">{e.excluded}</b></Card>
              <Card><div className="small sec">Pace</div><b style={{ fontSize: 24 }} className="num">{c.mode === "live" ? e.emails_per_day : "–"}</b><span className="xs muted"> {c.mode === "live" ? "per day" : "rehearsal"}</span></Card>
              <Card><div className="small sec">Takes about</div><b style={{ fontSize: 24 }} className="num">{c.mode === "live" ? e.calendar_days : 0}</b><span className="xs muted"> {c.mode === "live" ? "calendar days" : "seconds"}</span></Card>
            </div>
            <p className="xs muted">Sending cost: {e.sending_cost_note} Drafting spend so far ≈ ${e.drafting_cost_usd_est.toFixed(4)} <span className="basis estimate">estimate</span>. {e.pacing_note}</p>

            {admin && (
              <div className="row wrap">
                {c.status === "draft" && <><button className="btn" onClick={() => refreshM.mutate()}><Icon name="refresh" size={14} />Re-check recipients</button><button className="btn primary" disabled={e.included === 0} onClick={() => setConfirming(true)}><Icon name="play" size={14} />Review &amp; confirm…</button></>}
                {c.status === "running" && <button className="btn" onClick={() => pause.mutate()}><Icon name="pause" size={14} />Pause</button>}
                {c.status === "paused" && <button className="btn primary" onClick={() => resume.mutate()}><Icon name="play" size={14} />Resume</button>}
                {["draft", "running", "paused"].includes(c.status) && <button className="btn danger" onClick={() => setCancelling(true)}><Icon name="stop" size={14} />Cancel campaign</button>}
              </div>)}

            <Tabs value={tab} onChange={setTab} tabs={[{ id: "recipients", label: "Recipients" }, { id: "queue", label: `Queue (${Object.values(c.message_counts).reduce((a: number, b: any) => a + b, 0)})` }, { id: "settings", label: "Limits & sender" }]} />
            {tab === "recipients" && <Recipients id={id} />}
            {tab === "queue" && <Queue id={id} admin={admin} counts={c.message_counts} />}
            {tab === "settings" && (
              <dl className="kv">
                <dt>Daily cap</dt><dd>{c.daily_cap} emails</dd><dt>Hourly cap</dt><dd>{c.hourly_cap}</dd><dt>Minimum gap</dt><dd>{c.min_interval_seconds}s between emails</dd>
                <dt>Window</dt><dd>{c.window.start}–{c.window.end} ({c.window.tz}), days {c.window.days?.map((d: number) => ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]).join(", ")}</dd>
                <dt>Provider</dt><dd>{label(c.provider)}</dd><dt>Confirmed</dt><dd>{c.confirmed_by ? `${c.confirmed_by}, ${fmtDate(c.confirmed_at)}` : "not yet"}</dd>
                {c.sender_snapshot?.display_name && <><dt>Sender (frozen at confirmation)</dt><dd>{c.sender_snapshot.display_name} &lt;{c.sender_snapshot.from_email}&gt;<br /><span className="small muted">{c.sender_snapshot.postal_address}</span></dd></>}
              </dl>)}
            {confirming && <ConfirmCampaign c={c} onClose={() => setConfirming(false)} onDone={() => { setConfirming(false); refresh("campaign", "campaigns", "overview"); }} />}
            {cancelling && <Confirm title="Cancel this campaign?" danger confirmLabel="Cancel campaign" busy={cancel.isPending} onConfirm={() => cancel.mutate()} onCancel={() => setCancelling(false)}><p>Emails that have not gone out are cancelled and scheduled follow-ups stop. Emails already accepted by the mail server cannot be recalled.</p></Confirm>}
          </>
        )}
      </QueryState>
    </SlideOver>
  );
}

function ConfirmCampaign({ c, onClose, onDone }: { c: any; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const m = useMutation({ mutationFn: () => post(`/api/campaigns/${c.id}/confirm`, { expected_recipients: c.estimate.included, phrase: c.required_phrase }), onSuccess: () => { toast.ok(c.mode === "live" ? "Campaign started" : "Dry run started"); onDone(); }, onError: (e: any) => toast.error(e.message) });
  return (
    <Confirm title={c.mode === "live" ? "Send real emails?" : "Start the dry run?"} danger={c.mode === "live"} confirmLabel={c.mode === "live" ? `Send ${c.estimate.included} emails` : "Start dry run"} phrase={c.required_phrase} busy={m.isPending} onConfirm={() => m.mutate()} onCancel={onClose}>
      <div className="col gap-8">
        <p><b>{c.estimate.included}</b> recipient{c.estimate.included === 1 ? "" : "s"} will be queued; <b>{c.estimate.excluded}</b> are excluded. Every recipient is checked again at send time and anyone who unsubscribes, bounces or is suppressed before their turn is skipped.</p>
        {c.mode === "live" ? <Banner tone="warn" title="This sends real email">From <b>{c.sender_snapshot?.from_email || "your configured address"}</b>, at most {c.daily_cap} per day, within {c.window.start}–{c.window.end}. About {c.estimate.calendar_days} calendar days. You can pause or cancel at any time.</Banner> : <Banner tone="info">Dry run: nothing leaves this computer and no contact is changed.</Banner>}
      </div>
    </Confirm>
  );
}

function Recipients({ id }: { id: number }) {
  const [status, setStatus] = useState<"included" | "excluded">("excluded");
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["campaign-recipients", id, status, page], queryFn: () => get(`/api/campaigns/${id}/recipients${qs({ status, page, page_size: 25 })}`), placeholderData: (x) => x });
  const openContact = useOpenContact();
  return (
    <div className="col gap-8">
      <div className="row"><button className={`btn sm ${status === "excluded" ? "primary" : ""}`} onClick={() => { setStatus("excluded"); setPage(1); }}>Excluded (why)</button><button className={`btn sm ${status === "included" ? "primary" : ""}`} onClick={() => { setStatus("included"); setPage(1); }}>Included</button></div>
      <QueryState q={q}>
        {q.data?.items.length === 0 ? <p className="muted small">None.</p> : (
          <div className="table-wrap card"><table className="t"><thead><tr><th>Recipient</th><th>{status === "excluded" ? "Reason" : "Email status"}</th></tr></thead>
            <tbody>{q.data?.items.map((r: any) => <tr key={r.contact_id} className="click" onClick={() => openContact(r.contact_id)}><td>{r.name || r.email}<div className="xs muted">{r.email}</div></td><td style={{ whiteSpace: "normal" }}>{status === "excluded" ? <span className="small">{r.exclusion_reason}</span> : r.message_status ? <StatusBadge status={r.message_status} /> : <span className="muted small">not queued yet</span>}</td></tr>)}</tbody></table></div>)}
      </QueryState>
      <Pager page={page} pageSize={25} total={q.data?.total ?? 0} onPage={setPage} />
    </div>
  );
}

function Queue({ id, admin, counts }: { id: number; admin: boolean; counts: Record<string, number> }) {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["campaign-messages", id, status, page], queryFn: () => get(`/api/campaigns/${id}/messages${qs({ status, page, page_size: 25 })}`), refetchInterval: 4000, placeholderData: (x) => x });
  return (
    <div className="col gap-8">
      <div className="row wrap"><button className={`btn sm ${status === "" ? "primary" : ""}`} onClick={() => setStatus("")}>All</button>{Object.entries(counts).map(([s, n]) => <button key={s} className={`btn sm ${status === s ? "primary" : ""}`} onClick={() => { setStatus(s); setPage(1); }}>{label(s)} ({n})</button>)}</div>
      <QueryState q={q}>
        {q.data?.items.length === 0 ? <p className="muted small">Nothing queued yet. Messages are created when you confirm.</p> : (
          <div className="table-wrap card"><table className="t"><thead><tr><th>To</th><th>Status</th><th>Attempts</th><th>Last update</th></tr></thead>
            <tbody>{q.data?.items.map((m: any) => (
              <tr key={m.id} className="click" onClick={() => setOpen(m.id)}><td><div className="ell" style={{ maxWidth: 220 }}>{m.to}</div></td><td><StatusBadge status={m.status} />{m.reason && <div className="xs muted ell" style={{ maxWidth: 260 }} title={m.reason}>{m.reason}</div>}</td><td className="num">{m.attempts}</td><td className="nw">{ago(m.submitted_at || m.bounced_at || m.queued_at)}</td></tr>))}</tbody></table></div>)}
      </QueryState>
      <Pager page={page} pageSize={25} total={q.data?.total ?? 0} onPage={setPage} />
      {open && <MessagePanel id={open} admin={admin} onClose={() => setOpen(null)} />}
    </div>
  );
}

function MessagePanel({ id, admin, onClose }: { id: number; admin: boolean; onClose: () => void }) {
  const q = useQuery({ queryKey: ["message", id], queryFn: () => get(`/api/messages/${id}`) });
  const [note, setNote] = useState("");
  const [found, setFound] = useState<any>(null);
  const refresh = useRefresh();
  const toast = useToast();
  const m = q.data;
  const rec = useMutation({ mutationFn: (resolution: string) => post(`/api/messages/${id}/reconcile`, { resolution, note }), onSuccess: () => { toast.ok("Resolved and recorded"); refresh("message", "campaign", "campaigns", "campaign-messages", "overview"); }, onError: (e: any) => toast.error(e.message) });
  const check = useMutation({ mutationFn: () => post(`/api/messages/${id}/check-mailbox`, {}), onSuccess: setFound, onError: (e: any) => toast.error(e.message) });
  return (
    <SlideOver onClose={onClose} title={m ? `Message #${m.id}` : "Message"} subtitle={m && <span className="row"><StatusBadge status={m.status} />{m.dry_run && <StatusBadge status="dry_run" text="Dry run" />}</span>}>
      <QueryState q={q}>
        {m && (
          <>
            {m.status === "unknown" && (
              <Banner tone="bad" title="Outcome unknown: this may or may not have been sent">
                The connection broke after the message was transmitted. It is <b>never retried automatically</b>. Check your Sent folder (or ask the recipient), then record what you found.
                {admin && (
                  <div className="col gap-8 mt-8">
                    <div className="row wrap"><button className="btn sm" disabled={check.isPending} onClick={() => check.mutate()}>Search my mailbox for it</button>{found && <span className="small">{found.found.length ? "Found a copy in your mailbox." : "No copy found."} <span className="muted">{found.hint}</span></span>}</div>
                    <Field label="How did you verify? (required)">{(i) => <input id={i} className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Found it in Sent, 2026-11-03 14:02" />}</Field>
                    <div className="row"><button className="btn sm" disabled={note.trim().length < 5} onClick={() => rec.mutate("was_sent")}>It WAS sent</button><button className="btn sm danger" disabled={note.trim().length < 5} onClick={() => rec.mutate("was_not_sent")}>It was NOT sent</button></div>
                  </div>)}
              </Banner>)}
            <dl className="kv"><dt>To</dt><dd>{m.to}</dd><dt>From</dt><dd>{m.from}</dd><dt>Subject</dt><dd>{m.subject}</dd><dt>Message-ID</dt><dd className="mono small">{m.rfc_message_id}</dd>{m.provider_message_id && <><dt>Server reference</dt><dd className="mono small">{m.provider_message_id}</dd></>}</dl>
            <details open><summary className="small">The exact text that {m.status === "queued" ? "will be" : "was"} sent</summary><div className="mail mt-8">{m.body}</div></details>
            <div><h3>Send attempts</h3>{m.attempts_detail.length === 0 ? <p className="muted small">None yet.</p> : <ul className="list small">{m.attempts_detail.map((a: any) => <li key={a.no}>#{a.no} · <b>{label(a.outcome)}</b> at stage “{a.stage}” {a.smtp_code ? `(code ${a.smtp_code})` : ""} {a.category && <Badge>{label(a.category)}</Badge>}<div className="muted">{a.response}</div></li>)}</ul>}</div>
            <div><h3>History</h3><ul className="timeline mt-8">{m.events.map((e: any, i: number) => <li key={i}><b>{label(e.type)}</b> <span className="muted">· {e.source} · {ago(e.at)}</span></li>)}</ul></div>
          </>
        )}
      </QueryState>
    </SlideOver>
  );
}
