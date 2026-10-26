import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, patch } from "../api";
import { useAuth } from "../auth";
import { ago, fmtDate, label } from "../format";
import { Banner, Badge, Confirm, Field, QueryState, SlideOver, StatusBadge, Tabs, useToast } from "./ui";

export function useOpenContact() {
  const [params, setParams] = useSearchParams();
  return (id: number | null) => {
    const p = new URLSearchParams(params);
    if (id === null) p.delete("contact"); else p.set("contact", String(id));
    setParams(p, { replace: false });
  };
}

export function useRefresh() {
  const qc = useQueryClient();
  return (...keys: string[]) => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
}

const STAGES = ["new", "researched", "qualified", "draft_ready", "approved", "contacted", "replied", "interested", "meeting_scheduled", "proposal_sent", "won", "lost"];

export default function ContactDrawer() {
  const [params] = useSearchParams();
  const open = useOpenContact();
  const id = Number(params.get("contact")) || null;
  if (!id) return null;
  return <Drawer id={id} onClose={() => open(null)} />;
}

function Drawer({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["contact", id], queryFn: () => get(`/api/contacts/${id}`) });
  const [tab, setTab] = useState<"overview" | "activity" | "work">("overview");
  const c = q.data;
  return (
    <SlideOver wide onClose={onClose}
      title={c ? (c.name || c.email) : "Contact"}
      subtitle={c && <span>{c.email} · {c.company?.name || c.company?.domain}</span>}>
      <QueryState q={q}>
        {c && (
          <>
            <div className="row wrap">
              <StatusBadge status={c.eligibility_status} />
              <Badge tone={c.history_status === "attested_unsent" ? "ok" : c.history_status === "unknown" ? "bad" : "warn"} title="Has this person been emailed before?">History: {label(c.history_status)}</Badge>
              <StatusBadge status={c.pipeline_stage} />
              {c.is_suppressed && <StatusBadge status="suppressed" />}
              <Badge>{c.country ? `Country: ${c.country}` : "Country unknown"}</Badge>
              {c.score !== null && <Badge tone="info" title="Evidence score, not a probability of a sale">Score {c.score}</Badge>}
            </div>
            <Tabs value={tab} onChange={setTab} tabs={[{ id: "overview", label: "Review" }, { id: "work", label: "Notes, tasks, deals" }, { id: "activity", label: "Activity" }]} />
            {tab === "overview" && <ReviewTab c={c} />}
            {tab === "work" && <WorkTab c={c} />}
            {tab === "activity" && <ActivityTab c={c} />}
          </>
        )}
      </QueryState>
    </SlideOver>
  );
}

function ReviewTab({ c }: { c: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const refresh = useRefresh();
  const toast = useToast();
  const open = useOpenContact();
  const [status, setStatus] = useState(c.eligibility_status === "eligible" ? "excluded" : "eligible");
  const [reason, setReason] = useState("");
  const [role, setRole] = useState(c.role_title || "");
  const [suppress, setSuppress] = useState(false);

  const setElig = useMutation({
    mutationFn: () => post(`/api/contacts/${c.id}/eligibility`, { status, reason }),
    onSuccess: () => { toast.ok("Saved to the audit trail"); setReason(""); refresh("contact", "contacts", "overview", "campaign"); },
    onError: (e: any) => toast.error(e.message),
  });
  const saveRole = useMutation({
    mutationFn: () => patch(`/api/contacts/${c.id}`, { role_title: role, role_title_source: "entered manually" }),
    onSuccess: () => { toast.ok("Job title saved"); refresh("contact", "leads"); },
    onError: (e: any) => toast.error(e.message),
  });
  const sup = useMutation({
    mutationFn: () => post("/api/suppressions", { kind: "email", value: c.email, reason: "do_not_contact", notes: "Added from the contact panel" }),
    onSuccess: () => { toast.ok(`${c.email} will never be contacted again`); setSuppress(false); refresh("contact", "contacts", "overview", "suppressions"); },
    onError: (e: any) => toast.error(e.message),
  });

  return (
    <div className="col gap-16">
      {c.can_send.ok
        ? <Banner tone="ok" title="This person passes every send-time check">Eligible, history attested, jurisdiction rules satisfied, not suppressed. These checks run again right before any email goes out.</Banner>
        : <Banner tone="warn" title="This person cannot be emailed yet">
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{c.can_send.reasons.map((r: any) => <li key={r.code}>{r.text}</li>)}</ul>
          </Banner>}
      {c.suppression && <Banner tone="bad" title={`On the suppression list (${label(c.suppression.reason)})`}>Since {fmtDate(c.suppression.created_at)}. Imports can never undo this; lifting it is a deliberate action in Settings.</Banner>}

      <dl className="kv">
        <dt>Company</dt><dd>{c.company?.name} <span className="muted">({c.company?.domain})</span> {c.company?.id && <Link to={`/research?company=${c.company.id}`}>research</Link>}</dd>
        <dt>Source</dt><dd>Import #{c.import_batch_id ?? "–"}, row {c.source_row ?? "–"}</dd>
        <dt>Jurisdiction</dt><dd>{c.policy.name} · {label(c.policy.rule)} {c.policy.rule === "opt_out_permitted" && (c.policy.attested ? <Badge tone="ok">attested</Badge> : <Badge tone="warn">not attested</Badge>)}<div className="xs muted">{c.policy.notes}</div></dd>
        <dt>Consent</dt><dd>{c.consent_basis ? <span>{c.consent_basis} <span className="muted">({fmtDate(c.consent_recorded_at, false)})</span></span> : <span className="muted">none recorded</span>}</dd>
        <dt>Job title</dt><dd>{c.role_title || <span className="muted">unknown (the contact file has no title column)</span>}</dd>
        {c.research_run && <><dt>Research</dt><dd>{c.research_run.summary || "Researched"} <span className="muted">({c.research_run.mode}, {ago(c.research_run.finished_at)})</span></dd></>}
      </dl>

      {c.score_detail && (
        <div className="col gap-4">
          <h3>Why this score</h3>
          {Object.entries(c.score_detail.components).filter(([k]) => k !== "_meta").map(([k, v]: any) => (
            <div key={k} className="small"><b>{label(k)}</b>: {v.points}/{v.max} <span className="muted">— {v.rationale}</span></div>
          ))}
        </div>
      )}

      {admin && (
        <>
          <div className="col gap-12">
            <h3>Review decision</h3>
            <div className="row wrap">
              <select className="select" style={{ width: 200 }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="New eligibility status">
                <option value="eligible">Eligible to contact</option><option value="review_required">Needs more review</option><option value="excluded">Exclude</option>
              </select>
            </div>
            <Field label="Why? (kept in the audit trail)" hint="At least 10 characters, e.g. the basis you relied on.">{(id) => <textarea id={id} className="textarea" style={{ minHeight: 60 }} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
            <div className="row"><button className="btn primary" disabled={reason.trim().length < 10 || setElig.isPending} onClick={() => setElig.mutate()}>Save decision</button></div>
          </div>
          <div className="row wrap">
            <Field label="Job title (if you know it)">{(id) => <input id={id} className="input" value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. CTO" />}</Field>
            <button className="btn" style={{ alignSelf: "end" }} disabled={role === (c.role_title || "")} onClick={() => saveRole.mutate()}>Save title</button>
          </div>
          {!c.suppression && <div><button className="btn danger" onClick={() => setSuppress(true)}>Never contact this person…</button></div>}
        </>
      )}
      {suppress && (
        <Confirm title="Never contact this person?" danger confirmLabel="Add to suppression list" busy={sup.isPending} onConfirm={() => sup.mutate()} onCancel={() => setSuppress(false)}>
          <p>{c.email} is added to the global suppression list. Queued emails are cancelled, follow-ups stop, and no import can re-enable them.</p>
        </Confirm>
      )}
    </div>
  );
}

function ActivityTab({ c }: { c: any }) {
  return (
    <div className="col gap-16">
      <div>
        <h3>Emails</h3>
        {c.messages.length === 0 ? <p className="muted small mt-8">No email has been queued for this person.</p> : (
          <ul className="list">{c.messages.map((m: any) => (
            <li key={m.id}><div className="row between"><span>#{m.id} · step {m.step} · {m.subject}</span><span className="row"><StatusBadge status={m.status} />{m.is_dry_run && <StatusBadge status="dry_run" text="Dry run" />}</span></div>
              {m.reason && <div className="xs muted">{m.reason}</div>}</li>))}</ul>)}
      </div>
      {c.replies.length > 0 && (<div><h3>Replies</h3><ul className="list">{c.replies.map((r: any) => <li key={r.id}>{r.subject} <StatusBadge status={r.classification} /> <span className="muted">{ago(r.received_at)}</span></li>)}</ul></div>)}
      <div>
        <h3>Timeline (append-only)</h3>
        <ul className="timeline mt-8">{c.events.map((e: any) => (
          <li key={e.id}><b>{label(e.type)}</b> <span className="muted">· {e.source}{e.actor ? ` · ${e.actor}` : ""} · {ago(e.at)}</span>
            {e.data && Object.keys(e.data).length > 0 && <div className="xs muted">{Object.entries(e.data).filter(([, v]) => v !== null && typeof v !== "object").map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>}</li>))}</ul>
      </div>
      <div>
        <h3>Eligibility & history decisions</h3>
        <ul className="list">{c.decisions.map((d: any, i: number) => <li key={i} className="small"><b>{label(d.field)}</b>: {d.from ?? "–"} → {d.to} <span className="muted">by {d.by}, {ago(d.at)}{d.reason ? ` — ${d.reason}` : ""}</span></li>)}</ul>
      </div>
    </div>
  );
}

function WorkTab({ c }: { c: any }) {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const refresh = useRefresh();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [task, setTask] = useState({ title: "", due: "" });
  const [deal, setDeal] = useState({ title: "", value: "", currency: "EUR" });
  const [stage, setStage] = useState(c.pipeline_stage);

  const ok = (m: string) => { toast.ok(m); refresh("contact", "pipeline", "tasks", "overview"); };
  const err = (e: any) => toast.error(e.message);
  const addNote = useMutation({ mutationFn: () => post(`/api/contacts/${c.id}/notes`, { body: note }), onSuccess: () => { setNote(""); ok("Note added"); }, onError: err });
  const addTask = useMutation({ mutationFn: () => post("/api/tasks", { contact_id: c.id, title: task.title, due_at: task.due ? new Date(task.due).toISOString() : null }), onSuccess: () => { setTask({ title: "", due: "" }); ok("Task added"); }, onError: err });
  const addDeal = useMutation({ mutationFn: () => post("/api/deals", { contact_id: c.id, title: deal.title, value: deal.value ? Number(deal.value) : null, currency: deal.currency }), onSuccess: () => { setDeal({ title: "", value: "", currency: "EUR" }); ok("Deal created"); }, onError: err });
  const move = useMutation({ mutationFn: () => post("/api/pipeline/move", { contact_id: c.id, stage }), onSuccess: () => ok("Stage updated"), onError: err });

  return (
    <div className="col gap-16">
      {admin && (
        <div className="row wrap">
          <select className="select" style={{ width: 200 }} value={stage} onChange={(e) => setStage(e.target.value)} aria-label="Pipeline stage">
            {STAGES.map((s) => <option key={s} value={s}>{label(s)}</option>)}{c.pipeline_stage === "do_not_contact" && <option value="do_not_contact">Do not contact</option>}
          </select>
          <button className="btn" disabled={stage === c.pipeline_stage || c.pipeline_stage === "do_not_contact"} onClick={() => move.mutate()}>Move stage</button>
        </div>
      )}
      <div>
        <h3>Tasks</h3>
        <ul className="list">{c.tasks.length === 0 && <li className="muted small">No tasks.</li>}{c.tasks.map((t: any) => <li key={t.id} className="row between"><span>{t.title} <span className="muted small">{t.due_at ? `due ${fmtDate(t.due_at, false)}` : ""}</span></span><StatusBadge status={t.status} /></li>)}</ul>
        {admin && <div className="row wrap mt-8"><input className="input grow" placeholder="Next action…" value={task.title} onChange={(e) => setTask({ ...task, title: e.target.value })} aria-label="Task title" /><input className="input" style={{ width: 170 }} type="date" value={task.due} onChange={(e) => setTask({ ...task, due: e.target.value })} aria-label="Due date" /><button className="btn" disabled={task.title.length < 2} onClick={() => addTask.mutate()}>Add</button></div>}
      </div>
      <div>
        <h3>Deals</h3>
        <ul className="list">{c.deals.length === 0 && <li className="muted small">No deals.</li>}{c.deals.map((d: any) => <li key={d.id} className="row between"><span>{d.title} <span className="muted">{d.value !== null ? `${d.value} ${d.currency}` : ""}</span></span><StatusBadge status={d.status} /></li>)}</ul>
        {admin && <div className="row wrap mt-8"><input className="input grow" placeholder="Deal title" value={deal.title} onChange={(e) => setDeal({ ...deal, title: e.target.value })} aria-label="Deal title" /><input className="input" style={{ width: 110 }} type="number" min="0" placeholder="Value" value={deal.value} onChange={(e) => setDeal({ ...deal, value: e.target.value })} aria-label="Deal value" /><input className="input" style={{ width: 70 }} maxLength={3} value={deal.currency} onChange={(e) => setDeal({ ...deal, currency: e.target.value.toUpperCase() })} aria-label="Currency" /><button className="btn" disabled={deal.title.length < 2} onClick={() => addDeal.mutate()}>Add</button></div>}
      </div>
      <div>
        <h3>Notes</h3>
        <ul className="list">{c.notes.length === 0 && <li className="muted small">No notes.</li>}{c.notes.map((n: any) => <li key={n.id}><div className="small">{n.body}</div><div className="xs muted">{n.by} · {ago(n.at)}</div></li>)}</ul>
        {admin && <div className="col mt-8"><textarea className="textarea" style={{ minHeight: 60 }} placeholder="Add a note…" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" /><div><button className="btn" disabled={!note.trim()} onClick={() => addNote.mutate()}>Add note</button></div></div>}
      </div>
    </div>
  );
}
