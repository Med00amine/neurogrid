import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post, qs } from "../api";
import { useAuth } from "../auth";
import { useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Empty, PageHead, Pager, Progress, QueryState, SlideOver, StatusBadge, useToast } from "../components/ui";
import { ago, fmtDate, fmtDuration, fmtNum, fmtUsd, label } from "../format";

const STATUSES = ["pending", "running", "completed", "partial", "failed", "cancelled"];

export default function Jobs() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["jobs", status, page], queryFn: () => get(`/api/jobs${qs({ status, page, page_size: 30 })}`), refetchInterval: 3000, placeholderData: (x) => x });
  const counts = q.data?.counts ?? {};
  return (
    <>
      <PageHead title="Job monitor">Long tasks (imports, research, drafting, checking your mailbox) run in the background and survive restarts. If the server stops, running jobs resume where they left off and never repeat finished work.</PageHead>
      <Card pad={false}>
        <div className="filters">
          <button className={`btn sm ${status === "" ? "primary" : ""}`} onClick={() => { setStatus(""); setPage(1); }}>All</button>
          {STATUSES.map((s) => <button key={s} className={`btn sm ${status === s ? "primary" : ""}`} onClick={() => { setStatus(s); setPage(1); }}>{label(s)}{counts[s] ? ` (${counts[s]})` : ""}</button>)}
        </div>
        <QueryState q={q}>
          {q.data?.items.length === 0 ? <Empty title="No jobs">Jobs appear when you import, research or generate drafts.</Empty> : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>ID</th><th>Job</th><th>Status</th><th style={{ width: 190 }}>Progress</th><th>Started</th><th>Duration</th><th className="r">Retries</th><th>Problem</th></tr></thead>
              <tbody>{q.data?.items.map((j: any) => (
                <tr key={j.id} className="click" onClick={() => setOpen(j.id)}>
                  <td className="mono">{j.short_id}</td><td><div className="ell" style={{ maxWidth: 260 }}>{j.title || label(j.type)}</div><div className="xs muted">{label(j.type)}</div></td><td><StatusBadge status={j.status} /></td>
                  <td><Progress value={j.progress_done} max={j.progress_total || Math.max(1, j.progress_done)} /><span className="xs muted num">{fmtNum(j.progress_done)}/{fmtNum(j.progress_total)}{j.progress_failed > 0 && ` · ${j.progress_failed} failed`}</span></td>
                  <td className="nw">{j.started_at ? ago(j.started_at) : "–"}</td><td className="nw">{fmtDuration(j.duration_seconds)}</td><td className="r num">{j.retry_count}</td>
                  <td><div className="ell small" style={{ maxWidth: 240, color: j.error_message ? "var(--bad-fg)" : undefined }} title={j.error_message}>{j.error_message || ""}</div></td></tr>))}</tbody>
            </table></div>)}
        </QueryState>
        <Pager page={page} pageSize={30} total={q.data?.total ?? 0} onPage={setPage} />
      </Card>
      {open && <JobPanel id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function JobPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["job", id], queryFn: () => get(`/api/jobs/${id}`), refetchInterval: 2000 });
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const refresh = useRefresh();
  const toast = useToast();
  const j = q.data;
  const done = () => refresh("job", "jobs", "overview");
  const cancel = useMutation({ mutationFn: () => post(`/api/jobs/${id}/cancel`, {}), onSuccess: () => { toast.info("Cancel requested. It stops at the next safe point."); done(); }, onError: (e: any) => toast.error(e.message) });
  const retry = useMutation({ mutationFn: () => post(`/api/jobs/${id}/retry`, {}), onSuccess: () => { toast.ok("Retrying. Finished items are not repeated."); done(); }, onError: (e: any) => toast.error(e.message) });
  return (
    <SlideOver wide onClose={onClose} title={j?.title || (j ? label(j.type) : "Job")} subtitle={j && <span className="row"><StatusBadge status={j.status} /><span className="mono">{j.id}</span></span>}>
      <QueryState q={q}>
        {j && (
          <>
            {j.error_message && <Banner tone={j.status === "partial" ? "warn" : "bad"} title={j.status === "partial" ? "Finished with problems" : "Failed"}>{j.error_message}{j.suggested_action && <div className="mt-8"><b>What to do:</b> {j.suggested_action}</div>}</Banner>}
            <Progress value={j.progress_done} max={j.progress_total || Math.max(1, j.progress_done)} />
            <dl className="kv"><dt>Type</dt><dd>{label(j.type)}</dd><dt>Started by</dt><dd>{j.created_by || "system"}</dd><dt>Created</dt><dd>{fmtDate(j.created_at)}</dd><dt>Started</dt><dd>{fmtDate(j.started_at)}</dd><dt>Finished</dt><dd>{fmtDate(j.finished_at)}</dd><dt>Duration</dt><dd>{fmtDuration(j.duration_seconds)}</dd><dt>Progress</dt><dd>{fmtNum(j.progress_done)} done, {fmtNum(j.progress_failed)} failed, of {fmtNum(j.progress_total)}</dd><dt>Retries</dt><dd>{j.retry_count} of {j.max_retries} automatic</dd>{j.cost_usd_est > 0 && <><dt>Estimated cost</dt><dd>{fmtUsd(j.cost_usd_est)} <span className="basis estimate">estimate</span></dd></>}{j.error_category && <><dt>Problem type</dt><dd><Badge tone="bad">{label(j.error_category)}</Badge></dd></>}</dl>
            {admin && <div className="row">
              {["pending", "running"].includes(j.status) && <button className="btn danger" disabled={j.cancel_requested} onClick={() => cancel.mutate()}>{j.cancel_requested ? "Cancelling…" : "Cancel job"}</button>}
              {["failed", "partial", "cancelled"].includes(j.status) && <button className="btn primary" onClick={() => retry.mutate()}>Retry safely</button>}
            </div>}
            {Object.keys(j.result || {}).length > 0 && <div><h3>Result</h3><pre className="mail small mt-8">{JSON.stringify(j.result, null, 2)}</pre></div>}
            {j.items.length > 0 && <div><h3>Items ({Object.entries(j.item_counts).map(([k, v]) => `${v} ${k}`).join(", ")})</h3>
              <ul className="list small mt-8">{j.items.map((i: any) => <li key={i.key} className="row between"><span className="mono">{i.key}</span><span className="row"><StatusBadge status={i.status === "done" ? "completed" : i.status} text={label(i.status)} />{i.error && <span className="muted ell" style={{ maxWidth: 260 }} title={i.error}>{i.error}</span>}</span></li>)}</ul></div>}
          </>
        )}
      </QueryState>
    </SlideOver>
  );
}
