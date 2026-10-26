import { useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Empty, Field, Icon, Modal, PageHead, Pager, QueryState, SlideOver, StatusBadge, useDebounced, useToast } from "../components/ui";
import { ago, fmtDate, fmtNum, fmtUsd, label } from "../format";

export default function Research() {
  const [params, setParams] = useSearchParams();
  const loc = useLocation();
  const nav = useNavigate();
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [search, setSearch] = useState("");
  const d = useDebounced(search);
  const [researched, setResearched] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [startFor, setStartFor] = useState<null | { contact_ids?: number[]; company_ids?: number[]; filter?: any }>(null);
  const q = useQuery({ queryKey: ["companies", d, researched, page], queryFn: () => get(`/api/companies${qs({ search: d, researched, page })}`), placeholderData: (x) => x });
  const companyId = Number(params.get("company")) || null;

  // arriving from the Contacts page with a selection
  useEffect(() => {
    const st = loc.state as any;
    if (st && (st.contactIds || st.filter)) { setStartFor({ contact_ids: st.contactIds ?? undefined, filter: st.filter ?? undefined }); nav(loc.pathname, { replace: true, state: null }); }
  }, [loc.state]);

  const items: any[] = q.data?.items ?? [];
  return (
    <>
      <PageHead title="Company research" actions={admin && <button className="btn primary" disabled={sel.size === 0} onClick={() => setStartFor({ company_ids: [...sel] })}><Icon name="research" size={14} />Research {sel.size || ""} selected</button>}>
        For each company this reads its own public website (respecting robots.txt), and optionally one web search, then saves what it says <b>with the exact quote and source link</b>. Nothing is used in an email until you approve it.
      </PageHead>
      <Card pad={false}>
        <div className="filters">
          <input className="input search" placeholder="Search company or domain…" aria-label="Search companies" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
          <select className="select" aria-label="Research status" value={researched} onChange={(e) => { setResearched(e.target.value); setPage(1); }}><option value="">Research: all</option><option value="true">Researched</option><option value="false">Not researched</option></select>
        </div>
        <QueryState q={q}>
          {items.length === 0 ? <Empty title="No companies found">Companies are created when you import contacts. Select contacts on the Contacts page and choose Research…</Empty> : (
            <div className="table-wrap"><table className="t">
              <thead><tr>{admin && <th style={{ width: 36 }} />}<th>Company</th><th>Domain</th><th>Country</th><th>Type</th><th className="r">Contacts</th><th>Research</th></tr></thead>
              <tbody>{items.map((c) => (
                <tr key={c.id} className="click" onClick={() => setParams({ company: String(c.id) })}>
                  {admin && <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${c.name || c.domain}`} checked={sel.has(c.id)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(c.id) : n.delete(c.id); setSel(n); }} /></td>}
                  <td><div className="ell" style={{ maxWidth: 240 }}>{c.name || <span className="muted">–</span>}</div></td>
                  <td className="mono">{c.domain}</td><td>{c.country ?? <span className="muted">?</span>}</td>
                  <td>{c.company_type ? label(c.company_type) : <span className="muted">unknown</span>}</td><td className="r num">{c.contacts}</td>
                  <td className="nw">{c.research ? <span className="row gap-4"><StatusBadge status="completed" text="Researched" /><span className="muted small">{ago(c.research.at)} · {c.research.mode === "llm" ? "AI" : "rules"}</span></span> : <span className="muted small">not yet</span>}</td>
                </tr>))}</tbody>
            </table></div>)}
        </QueryState>
        <Pager page={page} pageSize={50} total={q.data?.total ?? 0} onPage={setPage} />
      </Card>
      {companyId && <CompanyPanel id={companyId} onClose={() => setParams({})} onStart={(ids) => setStartFor({ company_ids: ids })} />}
      {startFor && <StartResearch target={startFor} onClose={() => setStartFor(null)} onStarted={() => { setStartFor(null); setSel(new Set()); }} />}
    </>
  );
}

function StartResearch({ target, onClose, onStarted }: { target: any; onClose: () => void; onStarted: () => void }) {
  const toast = useToast();
  const nav = useNavigate();
  const [force, setForce] = useState(false);
  const est = useQuery({ queryKey: ["research-estimate", target], queryFn: () => post("/api/research/estimate", target) });
  const start = useMutation({
    mutationFn: () => post("/api/research/start", { ...target, force }),
    onSuccess: (r: any) => { toast.ok(`Research started for ${r.companies} companies${r.skipped_contacts ? ` (${r.skipped_contacts} contacts skipped: excluded, suppressed or free-mail)` : ""}`); onStarted(); nav("/jobs"); },
    onError: (e: any) => toast.error(e.message),
  });
  const e = est.data;
  return (
    <Modal title="Start research" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={start.isPending || !e} onClick={() => start.mutate()}>Start research</button></>}>
      <QueryState q={est}>
        {e && (
          <div className="col gap-12">
            <p><b>{fmtNum(e.companies)}</b> compan{e.companies === 1 ? "y" : "ies"} will be researched in the background. You can keep working; progress is in the Job monitor.</p>
            <Banner tone={e.mode === "llm" ? "info" : "ok"} title={e.mode === "llm" ? `AI extraction with ${e.model}` : "Rule-based extraction (free)"}>
              {e.mode === "llm" ? <>Estimated cost <b>{fmtUsd(e.estimated_llm_usd)}</b>{e.estimated_search_usd > 0 && <> + search <b>{fmtUsd(e.estimated_search_usd)}</b></>}. {e.assumption}</> : <>No AI key is configured (or AI is off), so the app quotes what the website says using fixed rules. It finds fewer insights but invents nothing. {e.estimated_search_usd > 0 && <>Search will cost about <b>{fmtUsd(e.estimated_search_usd)}</b>.</>}</>}
            </Banner>
            {e.companies > 25 && <Banner tone="warn">That's a lot for a first run. Start with 10 to 20 companies to see the quality and cost.</Banner>}
            <label className="checkbox"><input type="checkbox" checked={force} onChange={(e2) => setForce(e2.target.checked)} /><span>Re-research even if recent results exist (costs money again; reviews of unchanged findings are carried over)</span></label>
          </div>
        )}
      </QueryState>
    </Modal>
  );
}

function CompanyPanel({ id, onClose, onStart }: { id: number; onClose: () => void; onStart: (ids: number[]) => void }) {
  const q = useQuery({ queryKey: ["company", id], queryFn: () => get(`/api/companies/${id}`) });
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const refresh = useRefresh();
  const toast = useToast();
  const [site, setSite] = useState("");
  const c = q.data;
  useEffect(() => { if (c) setSite(c.website_url || ""); }, [c?.website_url]);
  const after = () => refresh("company", "companies", "leads", "leads-summary", "contacts", "overview");
  const review = useMutation({ mutationFn: (v: any) => post(`/api/research/findings/${v.id}/review`, v.body), onSuccess: after, onError: (e: any) => toast.error(e.message) });
  const approveAll = useMutation({ mutationFn: () => post(`/api/research/runs/${c.run.id}/approve-verified`, {}), onSuccess: (r: any) => { toast.ok(`${r.approved} verified facts approved`); after(); }, onError: (e: any) => toast.error(e.message) });
  const saveSite = useMutation({ mutationFn: () => patch(`/api/companies/${id}`, { website_url: site }), onSuccess: () => { toast.ok("Website saved. Re-run research to use it."); after(); }, onError: (e: any) => toast.error(e.message) });
  const [viewSource, setViewSource] = useState<number | null>(null);
  const [editing, setEditing] = useState<any>(null);
  const [country, setCountry] = useState<{ code: string } | null>(null);
  const applyCountry = useMutation({ mutationFn: (code: string) => post(`/api/companies/${id}/apply-country`, { country: code, reason: "Approved location finding from company research" }), onSuccess: (r: any) => { toast.ok(`Country set on ${r.updated_contacts} contacts`); setCountry(null); after(); }, onError: (e: any) => toast.error(e.message) });

  const byCat: Record<string, any[]> = {};
  c?.findings.forEach((f: any) => (byCat[f.category] ??= []).push(f));
  const srcById = Object.fromEntries((c?.sources ?? []).map((s: any) => [s.id, s]));
  const pending = c?.findings.filter((f: any) => f.status === "pending" && f.verified && f.kind === "fact").length ?? 0;

  return (
    <SlideOver wide onClose={onClose} title={c?.name || c?.domain || "Company"} subtitle={c && <span className="mono">{c.domain}</span>}>
      <QueryState q={q}>
        {c && (
          <>
            <div className="row between wrap">
              <div className="row wrap"><Badge>{c.country ? `Country ${c.country}` : "Country unknown"}</Badge><Badge>{c.company_type ? label(c.company_type) : "Type not confirmed"}</Badge><Badge>{c.contacts.length} contact{c.contacts.length === 1 ? "" : "s"}</Badge></div>
              {admin && <button className="btn" onClick={() => onStart([c.id])}><Icon name="refresh" size={14} />{c.run ? "Re-run research" : "Research this company"}</button>}
            </div>
            {admin && <div className="row"><input className="input grow" value={site} onChange={(e) => setSite(e.target.value)} placeholder="https://company.com" aria-label="Official website" /><button className="btn" disabled={site === (c.website_url || "")} onClick={() => saveSite.mutate()}>Save website</button></div>}

            {!c.run ? <Banner tone="info" title="Not researched yet">Use “Research this company” to read its public website.</Banner> : (
              <>
                <Card title="What was done" subtitle={`Run #${c.run.id} · ${fmtDate(c.run.finished_at)}`}>
                  <div className="col gap-8">
                    <div className="row wrap small"><Badge tone={c.run.mode === "llm" ? "info" : "ok"}>{c.run.mode === "llm" ? `AI extraction (${c.run.model})` : "Rule-based extraction"}</Badge><span>{c.run.pages_fetched} page(s) read</span>{c.run.search_queries > 0 && <span>{c.run.search_queries} search</span>}<span>Cost ≈ {fmtUsd(c.run.cost_usd_est)} <span className="basis estimate">estimate</span></span>{c.run.input_tokens > 0 && <span className="muted">{fmtNum(c.run.input_tokens)} in / {fmtNum(c.run.output_tokens)} out tokens</span>}</div>
                    {c.run.summary && <p><span className="muted small">Summary{c.run.mode === "llm" ? " (written by AI from the sources, verify it)" : " (the site's own description)"}:</span> {c.run.summary}</p>}
                    {c.run.uncertainties.length > 0 && <div><b className="small">Unresolved / to double-check</b><ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>{c.run.uncertainties.map((u: string, i: number) => <li key={i}>{u}</li>)}</ul></div>}
                  </div>
                </Card>

                <Card title="Sources" subtitle="Every finding below links to one of these. Retrieval time is when the page was read." pad={false}>
                  <ul className="list" style={{ padding: "0 16px" }}>
                    {c.sources.map((s: any) => (
                      <li key={s.id} className="row between wrap"><div className="grow" style={{ minWidth: 0 }}>
                        <div className="ell"><a href={s.url} target="_blank" rel="noopener noreferrer nofollow">{s.title || s.url}</a> <Badge>{s.type === "official_site" ? "official site" : "search result"}</Badge>{s.injection_flag && <Badge tone="bad" title="This page contains text that looks like instructions to an AI. It was flagged and not trusted."><Icon name="alert" size={12} />suspicious text</Badge>}</div>
                        <div className="xs muted ell">{s.url} · retrieved {fmtDate(s.retrieved_at)} · HTTP {s.http_status ?? "–"}</div></div>
                        <button className="btn sm ghost" onClick={() => setViewSource(s.id)}>View saved text</button></li>))}
                    {c.sources.length === 0 && <li className="muted small">No page could be read.</li>}
                  </ul>
                </Card>

                <Card title="Findings" subtitle="Approve what is true and useful. Rejected or unreviewed findings never reach an email." actions={admin && pending > 0 ? <button className="btn sm primary" onClick={() => approveAll.mutate()}>Approve {pending} verified facts</button> : undefined}>
                  {Object.entries(byCat).map(([cat, fs]) => (
                    <div key={cat} className="mt-12"><h3 className="sec" style={{ textTransform: "capitalize" }}>{label(cat)}</h3>
                      <div className="col gap-12 mt-8">{fs.map((f: any) => (
                        <div key={f.id} data-testid="finding" data-category={f.category} className="card card-pad col gap-8" style={{ opacity: f.status === "rejected" || f.status === "auto_rejected" ? 0.65 : 1 }}>
                          <div className="row between wrap">
                            <div className="row wrap"><StatusBadge status={f.kind} text={f.kind === "fact" ? "Stated on the site" : "Inference (not stated)"} /><StatusBadge status={f.confidence} text={`${label(f.confidence)} confidence`} /><StatusBadge status={f.status} />{f.service_tags.map((t: string) => <Badge key={t} tone="info">{label(t)}</Badge>)}</div>
                          </div>
                          <p>{f.edited_statement || f.statement}{f.edited_statement && <span className="muted small"> (edited by you; original: {f.statement})</span>}</p>
                          {f.evidence.map((e: any, i: number) => (
                            <div key={i} className="quote"><span title={e.verified ? "This exact text was found in the saved page" : "This text was NOT found in the saved page"} style={{ color: e.verified ? "var(--ok-fg)" : "var(--bad-fg)", fontWeight: 600 }}>{e.verified ? "✓ verified" : "✗ not found in source"}</span> “{e.excerpt}” {e.source_id && srcById[e.source_id] && <a className="small" href={srcById[e.source_id].url} target="_blank" rel="noopener noreferrer nofollow">source ↗</a>}</div>))}
                          {f.review_note && <div className="xs muted">{f.review_note}</div>}
                          {admin && f.status !== "auto_rejected" && (
                            <div className="row wrap">
                              <button className="btn sm" disabled={f.status === "approved"} onClick={() => review.mutate({ id: f.id, body: { action: "approve" } })}><Icon name="check" size={13} />Approve</button>
                              <button className="btn sm" onClick={() => setEditing(f)}>Edit</button>
                              <button className="btn sm danger" disabled={f.status === "rejected"} onClick={() => review.mutate({ id: f.id, body: { action: "reject" } })}>Reject</button>
                              {f.status !== "pending" && <button className="btn sm ghost" onClick={() => review.mutate({ id: f.id, body: { action: "reset" } })}>Reset</button>}
                              {f.category === "location" && (f.status === "approved" || f.status === "edited") && /\(([A-Z]{2})\)/.test(f.statement) && <button className="btn sm" onClick={() => setCountry({ code: f.statement.match(/\(([A-Z]{2})\)/)![1] })}>Use this country for the contacts…</button>}
                            </div>)}
                        </div>))}</div></div>))}
                  {c.findings.length === 0 && <p className="muted small">Nothing was found.</p>}
                </Card>
              </>
            )}

            <Card title="Contacts at this company" pad={false}>
              <ul className="list" style={{ padding: "0 16px" }}>{c.contacts.map((x: any) => <li key={x.id} className="row between"><span>{x.name || x.email} <span className="muted small">{x.email}</span></span><span className="row"><StatusBadge status={x.eligibility_status} /><StatusBadge status={x.stage} /></span></li>)}</ul>
            </Card>
          </>
        )}
      </QueryState>
      {viewSource && <SourceText id={viewSource} onClose={() => setViewSource(null)} />}
      {editing && <EditFinding f={editing} onClose={() => setEditing(null)} onSave={(text) => { review.mutate({ id: editing.id, body: { action: "edit", edited_statement: text } }); setEditing(null); }} />}
      {country && <Modal title="Set country for this company's contacts?" onClose={() => setCountry(null)} footer={<><button className="btn" onClick={() => setCountry(null)}>Cancel</button><button className="btn primary" onClick={() => applyCountry.mutate(country.code)}>Set {country.code}</button></>}><p>The country decides which outreach rules apply. Only set it if the finding is right (a company's website can mention a country it merely does business in). Contacts whose country you set by hand are not changed.</p></Modal>}
    </SlideOver>
  );
}

function SourceText({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["source", id], queryFn: () => get(`/api/research/sources/${id}`) });
  return (
    <Modal wide title="Saved page text (untrusted)" onClose={onClose} footer={<button className="btn" onClick={onClose}>Close</button>}>
      <QueryState q={q}>{q.data && <>
        <Banner tone="info">This is the cleaned text saved when the page was read on {fmtDate(q.data.retrieved_at)}. Quotes are checked against it. It may be removed after your retention period (Settings).</Banner>
        <pre className="mail mt-12" style={{ maxHeight: 420, overflow: "auto" }}>{q.data.text ?? "(text removed after the retention period)"}</pre></>}</QueryState>
    </Modal>
  );
}

function EditFinding({ f, onClose, onSave }: { f: any; onClose: () => void; onSave: (t: string) => void }) {
  const [text, setText] = useState(f.edited_statement || f.statement);
  return (
    <Modal title="Edit finding" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={text.trim().length < 5} onClick={() => onSave(text)}>Save as edited</button></>}>
      <div className="col gap-12">
        <Banner tone="warn">Your wording replaces the original in emails. Keep it true to the quoted evidence below; the quote itself cannot be changed.</Banner>
        <Field label="Statement">{(id) => <textarea id={id} className="textarea" value={text} onChange={(e) => setText(e.target.value)} />}</Field>
        {f.evidence.map((e: any, i: number) => <div key={i} className="quote">“{e.excerpt}”</div>)}
      </div>
    </Modal>
  );
}
