import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { HBars } from "../components/charts";
import { useOpenContact, useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Empty, Field, Icon, Modal, PageHead, QueryState, StatusBadge, Tabs, useDebounced, useToast } from "../components/ui";
import { fmtDate, label } from "../format";

const LANES = ["researched", "qualified", "draft_ready", "approved", "contacted", "replied", "interested", "meeting_scheduled", "proposal_sent", "won", "lost", "do_not_contact"];

export default function Pipeline() {
  const [tab, setTab] = useState<"board" | "tasks" | "deals" | "funnel">("board");
  return (
    <>
      <PageHead title="Sales pipeline">Where every conversation stands. Drag a card to change its stage. Follow-ups stop on their own when someone replies, unsubscribes, bounces or you close the deal.</PageHead>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "board", label: "Board" }, { id: "tasks", label: "Tasks & follow-ups" }, { id: "deals", label: "Deals" }, { id: "funnel", label: "Funnel" }]} />
      <div className="mt-16">{tab === "board" ? <Board /> : tab === "tasks" ? <Tasks /> : tab === "deals" ? <Deals /> : <Funnel />}</div>
    </>
  );
}

function Board() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [search, setSearch] = useState("");
  const d = useDebounced(search);
  const q = useQuery({ queryKey: ["pipeline", d], queryFn: () => get(`/api/pipeline/board${qs({ search: d })}`), refetchInterval: 20000 });
  const refresh = useRefresh();
  const toast = useToast();
  const openContact = useOpenContact();
  const [over, setOver] = useState<string | null>(null);
  const [pending, setPending] = useState<null | { id: number; stage: string; name: string }>(null);
  const move = useMutation({ mutationFn: (v: { id: number; stage: string; note?: string }) => post("/api/pipeline/move", { contact_id: v.id, stage: v.stage, note: v.note }), onSuccess: () => { refresh("pipeline", "overview", "contact", "tasks"); setPending(null); }, onError: (e: any) => toast.error(e.message) });
  const cols = (q.data?.columns ?? []).filter((c: any) => LANES.includes(c.stage));
  return (
    <div className="col gap-12">
      <input className="input" style={{ maxWidth: 320 }} placeholder="Search the board…" aria-label="Search the board" value={search} onChange={(e) => setSearch(e.target.value)} />
      <QueryState q={q}>
        {cols.every((c: any) => c.count === 0) ? <Empty title="No one is in the pipeline yet">Contacts appear here after research, drafting or sending. You can also move them by hand from a contact's panel.</Empty> : (
          <div className="board">
            {cols.map((col: any) => (
              <div key={col.stage} className={`lane ${over === col.stage ? "over" : ""}`}
                onDragOver={(e) => { if (admin && col.stage !== "do_not_contact") { e.preventDefault(); setOver(col.stage); } }}
                onDragLeave={() => setOver(null)}
                onDrop={(e) => { e.preventDefault(); setOver(null); const raw = e.dataTransfer.getData("application/x-contact"); if (!raw || col.stage === "do_not_contact") return; const c = JSON.parse(raw); if (c.stage === col.stage) return; if (["won", "lost"].includes(col.stage)) setPending({ id: c.id, stage: col.stage, name: c.name }); else move.mutate({ id: c.id, stage: col.stage }); }}>
                <h3><span>{label(col.stage)}</span><span className="num muted">{col.count}</span></h3>
                {col.stage === "do_not_contact" && <div className="xs muted">Managed by the suppression list.</div>}
                {col.items.map((c: any) => (
                  <div key={c.id} className="cardx" draggable={admin && col.stage !== "do_not_contact"} onDragStart={(e) => e.dataTransfer.setData("application/x-contact", JSON.stringify({ id: c.id, stage: col.stage, name: c.name || c.email }))} onClick={() => openContact(c.id)} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") openContact(c.id); }}>
                    <div className="ell"><b>{c.name || c.email}</b></div><div className="xs muted ell">{c.company}</div>
                    <div className="row between mt-8">{c.score !== null && <Badge tone="info">score {c.score}</Badge>}{c.deal_value != null && <Badge tone="ok">{c.deal_value} {c.currency}</Badge>}</div>
                    {c.next_action && <div className="xs sec mt-8"><Icon name="clock" size={11} /> {c.next_action}{c.next_action_at ? ` · ${fmtDate(c.next_action_at, false)}` : ""}</div>}
                  </div>))}
                {col.count > col.items.length && <div className="xs muted">+{col.count - col.items.length} more (use search)</div>}
              </div>))}
          </div>)}
      </QueryState>
      {pending && <MoveNote p={pending} onClose={() => setPending(null)} onSave={(note) => move.mutate({ id: pending.id, stage: pending.stage, note })} />}
    </div>
  );
}

function MoveNote({ p, onClose, onSave }: { p: { stage: string; name: string }; onClose: () => void; onSave: (note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <Modal title={`Move ${p.name} to “${label(p.stage)}”`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={() => onSave(note)}>Move</button></>}>
      <div className="col gap-12"><p className="small sec">Closing a deal stops all scheduled follow-ups for this person.</p><Field label="Note (optional)">{(i) => <textarea id={i} className="textarea" style={{ minHeight: 60 }} value={note} onChange={(e) => setNote(e.target.value)} placeholder={p.stage === "won" ? "What was agreed?" : "Why was it lost?"} />}</Field></div>
    </Modal>
  );
}

function Tasks() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [status, setStatus] = useState("open");
  const q = useQuery({ queryKey: ["tasks", status], queryFn: () => get(`/api/tasks${qs({ status })}`), refetchInterval: 30000 });
  const refresh = useRefresh();
  const toast = useToast();
  const openContact = useOpenContact();
  const act = useMutation({ mutationFn: (v: { id: number; what: string }) => post(`/api/tasks/${v.id}/${v.what}`, {}), onSuccess: () => refresh("tasks", "overview", "contact"), onError: (e: any) => toast.error(e.message) });
  return (
    <Card pad={false} title="Tasks" subtitle="Follow-up tasks are created automatically after a live send and cancelled when a stop condition appears.">
      <div className="filters">{["open", "done", "cancelled"].map((s) => <button key={s} className={`btn sm ${status === s ? "primary" : ""}`} onClick={() => setStatus(s)}>{label(s)}</button>)}</div>
      <QueryState q={q}>
        {q.data?.items.length === 0 ? <Empty title="No tasks">Nothing here. Tasks appear after live sends and replies.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Task</th><th>Contact</th><th>Due</th><th>Kind</th><th /></tr></thead>
            <tbody>{q.data?.items.map((t: any) => (
              <tr key={t.id}><td>{t.title}{t.cancel_reason && <div className="xs muted">cancelled: {t.cancel_reason}</div>}</td>
                <td>{t.contact ? <button className="btn sm ghost" onClick={() => openContact(t.contact.id)}>{t.contact.name || t.contact.email}</button> : "–"}</td>
                <td className="nw">{t.due_at ? <>{fmtDate(t.due_at, false)} {t.overdue && <Badge tone="bad">overdue</Badge>}</> : "–"}</td><td>{label(t.kind)}{t.auto && <span className="xs muted"> (auto)</span>}</td>
                <td className="r nw">{admin && t.status === "open" && <><button className="btn sm" onClick={() => act.mutate({ id: t.id, what: "complete" })}>Done</button> <button className="btn sm ghost" onClick={() => act.mutate({ id: t.id, what: "cancel" })}>Cancel</button></>}</td></tr>))}</tbody></table></div>)}
      </QueryState>
      <div className="card-pad"><Banner tone="info" title="How follow-ups work">A follow-up task is a reminder. To send one, generate a follow-up draft in Email studio, approve it and run a follow-up campaign. The same checks apply, and at most {2} follow-ups are allowed by default, spaced days apart (Settings).</Banner></div>
    </Card>
  );
}

function Deals() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const q = useQuery({ queryKey: ["deals"], queryFn: () => get("/api/deals") });
  const refresh = useRefresh();
  const toast = useToast();
  const openContact = useOpenContact();
  const upd = useMutation({ mutationFn: (v: { id: number; status: string }) => patch(`/api/deals/${v.id}`, { status: v.status }), onSuccess: () => refresh("deals", "pipeline", "overview"), onError: (e: any) => toast.error(e.message) });
  return (
    <Card pad={false} title="Deals" subtitle="Create a deal from a contact's panel (Notes, tasks, deals).">
      <QueryState q={q}>
        {q.data?.length === 0 ? <Empty title="No deals yet">When a conversation becomes real work, add a deal with its value from the contact's panel.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Deal</th><th>Contact</th><th className="r">Value</th><th>Status</th><th>Source campaign</th><th /></tr></thead>
            <tbody>{q.data?.map((d: any) => (
              <tr key={d.id}><td>{d.title}</td><td><button className="btn sm ghost" onClick={() => openContact(d.contact_id)}>#{d.contact_id}</button></td><td className="r num">{d.value != null ? `${d.value} ${d.currency}` : "–"}</td><td><StatusBadge status={d.status} /></td><td>{d.source_campaign_id ? `#${d.source_campaign_id}` : "–"}</td>
                <td className="r">{admin && d.status === "open" && <><button className="btn sm" onClick={() => upd.mutate({ id: d.id, status: "won" })}>Won</button> <button className="btn sm ghost" onClick={() => upd.mutate({ id: d.id, status: "lost" })}>Lost</button></>}</td></tr>))}</tbody></table></div>)}
      </QueryState>
    </Card>
  );
}

function Funnel() {
  const q = useQuery({ queryKey: ["pipeline-summary"], queryFn: () => get("/api/pipeline/summary") });
  return (
    <div className="grid g2">
      <Card title="Contacts per stage"><QueryState q={q}>{q.data && <HBars rows={q.data.funnel.map((f: any) => ({ name: label(f.stage), value: f.count }))} emptyText="No contacts in the pipeline yet." />}</QueryState></Card>
      <Card title="Deals"><QueryState q={q}>{q.data && (q.data.deals.length === 0 ? <p className="muted small">No deals yet.</p> : <ul className="list">{q.data.deals.map((d: any, i: number) => <li key={i} className="row between"><span><StatusBadge status={d.status} /> {d.count} deal{d.count === 1 ? "" : "s"}</span><b className="num">{d.value.toLocaleString()} {d.currency}</b></li>)}</ul>)}</QueryState></Card>
    </div>
  );
}
