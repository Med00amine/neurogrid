import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post, qs } from "../api";
import { useAuth } from "../auth";
import { useOpenContact, useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Empty, Icon, PageHead, Pager, QueryState, SlideOver, StatusBadge, useToast } from "../components/ui";
import { ago, fmtDate, label } from "../format";

const CLASSES = ["interested", "info_request", "referral", "not_interested", "unsubscribe", "auto_response", "uncertain"];

export default function Replies() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [cls, setCls] = useState("");
  const [reviewed, setReviewed] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const refresh = useRefresh();
  const toast = useToast();
  const status = useQuery({ queryKey: ["replies-status"], queryFn: () => get("/api/replies/status"), refetchInterval: 20000 });
  const q = useQuery({ queryKey: ["replies", cls, reviewed, page], queryFn: () => get(`/api/replies${qs({ classification: cls, reviewed, page, page_size: 25 })}`), refetchInterval: 15000, placeholderData: (x) => x });
  const poll = useMutation({ mutationFn: () => post("/api/replies/poll", {}), onSuccess: () => { toast.ok("Checking your mailbox now. See the Job monitor."); setTimeout(() => refresh("replies", "replies-status", "overview"), 3000); }, onError: (e: any) => toast.error(e.message) });
  const counts = q.data?.counts ?? {};

  return (
    <>
      <PageHead title="Replies & bounces" actions={admin && <button className="btn" disabled={!status.data?.imap_configured || poll.isPending} onClick={() => poll.mutate()}><Icon name="refresh" size={14} />Check mailbox now</button>}>
        Replies, delivery failures and out-of-office messages from your mailbox (read-only: nothing is moved, marked or deleted). The label on each reply is a <b>suggestion</b>. You can correct it, and an explicit “stop contacting me” takes effect immediately.
      </PageHead>
      {status.data && !status.data.imap_configured && <Banner tone="warn" title="Reply tracking is not connected">Add <code>IMAP_HOST</code>, <code>IMAP_USERNAME</code> and <code>IMAP_PASSWORD</code> to the server's <code>.env</code> and restart. Until then replies and bounces will not appear and unsubscribe requests sent by email will not be seen. You must read your inbox yourself.</Banner>}
      {status.data?.imap_configured && <p className="small muted" style={{ marginBottom: 12 }}>Mailbox {status.data.mailbox} on {status.data.host} · last checked {status.data.last_checked ? ago(status.data.last_checked) : "never"}.</p>}
      <Card pad={false}>
        <div className="filters">
          <button className={`btn sm ${cls === "" && reviewed === "" ? "primary" : ""}`} onClick={() => { setCls(""); setReviewed(""); setPage(1); }}>All</button>
          <button className={`btn sm ${reviewed === "false" ? "primary" : ""}`} onClick={() => { setReviewed("false"); setCls(""); setPage(1); }}>To review</button>
          {CLASSES.map((c) => <button key={c} className={`btn sm ${cls === c ? "primary" : ""}`} onClick={() => { setCls(c); setReviewed(""); setPage(1); }}>{label(c)}{counts[c] ? ` (${counts[c]})` : ""}</button>)}
        </div>
        <QueryState q={q}>
          {q.data?.items.length === 0 ? <Empty title="Nothing here">Replies show up after you have sent emails and your mailbox is connected.</Empty> : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>From</th><th>Subject</th><th>Type</th><th>Label (suggested)</th><th>Contact</th><th>Received</th></tr></thead>
              <tbody>{q.data?.items.map((r: any) => (
                <tr key={r.id} className="click" onClick={() => setOpen(r.id)} style={{ fontWeight: r.reviewed ? 400 : 600 }}>
                  <td><div className="ell" style={{ maxWidth: 200 }}>{r.from_name || r.from_email}</div></td>
                  <td><div className="ell" style={{ maxWidth: 260 }}>{r.subject}<div className="xs muted ell" style={{ fontWeight: 400 }}>{r.snippet}</div></div></td>
                  <td>{r.kind === "bounce" ? <Badge tone="bad">bounce</Badge> : r.kind === "auto_reply" ? <Badge>automatic</Badge> : <Badge tone="info">reply</Badge>}</td>
                  <td>{r.kind === "bounce" ? <span className="small">{r.dsn?.status} {r.dsn?.action}</span> : <span className="row gap-4"><StatusBadge status={r.classification} />{r.classification_source === "user" && <span className="xs muted">you</span>}{r.classification_source === "llm" && <span className="xs muted">AI</span>}</span>}</td>
                  <td>{r.contact ? <span className="small">{r.contact.name || r.contact.email}</span> : <Badge tone="warn">no match</Badge>}</td>
                  <td className="nw">{ago(r.received_at)}</td></tr>))}</tbody>
            </table></div>)}
        </QueryState>
        <Pager page={page} pageSize={25} total={q.data?.total ?? 0} onPage={setPage} />
      </Card>
      {open && <ReplyPanel id={open} onClose={() => setOpen(null)} admin={admin} />}
    </>
  );
}

function ReplyPanel({ id, onClose, admin }: { id: number; onClose: () => void; admin: boolean }) {
  const q = useQuery({ queryKey: ["reply", id], queryFn: () => get(`/api/replies/${id}`) });
  const [cls, setCls] = useState("");
  const [lost, setLost] = useState(false);
  const refresh = useRefresh();
  const toast = useToast();
  const openContact = useOpenContact();
  const r = q.data;
  const save = useMutation({ mutationFn: () => post(`/api/replies/${id}/classify`, { classification: cls || r.classification, mark_lost: lost }), onSuccess: () => { toast.ok("Saved"); refresh("reply", "replies", "overview", "pipeline", "contact"); }, onError: (e: any) => toast.error(e.message) });
  return (
    <SlideOver wide onClose={onClose} title={r?.subject ?? "Reply"} subtitle={r && <span>{r.from_name} &lt;{r.from_email}&gt; · {fmtDate(r.received_at)}</span>}>
      <QueryState q={q}>
        {r && (
          <>
            {r.kind === "bounce" && <Banner tone="bad" title="Delivery failure notice">Status {r.dsn?.status ?? "?"}, action “{r.dsn?.action}”. {r.dsn?.diagnostic}</Banner>}
            {r.weak_match && <Banner tone="warn" title="Matched by address only">There were no thread headers, so this was matched on the sender's address{r.match_method?.includes("subject") ? " and subject" : ""}. Check it really belongs to the email below.</Banner>}
            {!r.contact && r.kind !== "bounce" && <Banner tone="warn" title="Not matched to anyone you emailed">It may be a stranger or someone writing from a different address. If it is an opt-out request, that address has been suppressed anyway.</Banner>}
            <div className="row wrap"><span className="small sec">Match: {r.match_method ? label(r.match_method) : "none"}</span>{r.contact && <button className="btn sm" onClick={() => openContact(r.contact.id)}>Open {r.contact.name || r.contact.email}</button>}</div>

            <Card title="What they wrote">
              <div className="mail">{r.new_text || <span className="muted">(empty)</span>}</div>
              <details className="mt-8"><summary className="small">Full message including quoted text</summary><pre className="mail small mt-8">{r.body}</pre></details>
            </Card>
            {r.original && <Card title="Your email they replied to" subtitle={`Sent ${fmtDate(r.original.submitted_at)}`}><details><summary className="small">{r.original.subject}</summary><div className="mail small mt-8">{r.original.body}</div></details></Card>}

            {r.kind !== "bounce" && (
              <Card title="Classification" subtitle="A suggestion. The text above is the truth.">
                <div className="col gap-12">
                  <div className="row wrap"><StatusBadge status={r.classification} /><span className="small sec">suggested by {r.classification_source === "llm" ? "AI" : r.classification_source === "user" ? "you" : "rules"}{r.confidence != null && ` (${Math.round(r.confidence * 100)}% sure)`}</span></div>
                  {r.reasons?.length > 0 && <div className="small muted">{r.reasons.join(" · ")}</div>}
                  {admin && (
                    <div className="row wrap">
                      <select className="select" style={{ width: 220 }} aria-label="Correct the classification" value={cls || r.classification || ""} onChange={(e) => setCls(e.target.value)}>{CLASSES.map((c) => <option key={c} value={c}>{label(c)}</option>)}</select>
                      {(cls || r.classification) === "not_interested" && <label className="chip"><input type="checkbox" checked={lost} onChange={(e) => setLost(e.target.checked)} />also mark the deal lost</label>}
                      <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>{r.reviewed ? "Update" : "Confirm"}</button>
                    </div>)}
                  {(cls || r.classification) === "unsubscribe" && <Banner tone="info">“Unsubscribe” adds this address to the global suppression list so nothing can be sent to it again.</Banner>}
                </div>
              </Card>)}
          </>
        )}
      </QueryState>
    </SlideOver>
  );
}
