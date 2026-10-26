import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { del, get, post, put, qs } from "../api";
import { useAuth } from "../auth";
import { useOpenContact, useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Empty, Field, Icon, Modal, PageHead, Pager, QueryState, SlideOver, StatusBadge, Tabs, useDebounced, useToast } from "../components/ui";
import { ago, fmtDate, fmtNum, fmtUsd, label } from "../format";

export default function Studio() {
  const [tab, setTab] = useState<"drafts" | "templates">("drafts");
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const loc = useLocation();
  const nav = useNavigate();
  const [gen, setGen] = useState<null | { contact_ids?: number[]; filter?: any }>(null);
  useEffect(() => {
    const st = loc.state as any;
    if (st?.contactIds) { setGen({ contact_ids: st.contactIds }); nav(loc.pathname, { replace: true, state: null }); }
  }, [loc.state]);
  return (
    <>
      <PageHead title="Email studio" actions={admin && <button className="btn primary" onClick={() => setGen({})}><Icon name="plus" size={14} />Generate drafts…</button>}>
        Every draft shows the research behind it. Claims about the company must link to findings you approved; claims about you must match your sender profile. Nothing is sent from here: approved drafts only become emails when you confirm a campaign.
      </PageHead>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "drafts", label: "Drafts" }, { id: "templates", label: "Templates" }]} />
      <div className="mt-16">{tab === "drafts" ? <Drafts /> : <Templates admin={admin} />}</div>
      {gen && <GenerateModal initial={gen} onClose={() => setGen(null)} />}
    </>
  );
}

function Drafts() {
  const [status, setStatus] = useState("needs_review");
  const [search, setSearch] = useState("");
  const d = useDebounced(search);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["drafts", status, d, page], queryFn: () => get(`/api/drafts${qs({ status, search: d, page, page_size: 25 })}`), refetchInterval: 8000, placeholderData: (x) => x });
  const counts = q.data?.counts ?? {};
  return (
    <Card pad={false}>
      <div className="filters">
        {["needs_review", "approved", "rejected", ""].map((s) => <button key={s || "all"} className={`btn sm ${status === s ? "primary" : ""}`} onClick={() => { setStatus(s); setPage(1); }}>{s ? label(s) : "All"} {s && counts[s] !== undefined ? `(${counts[s]})` : ""}</button>)}
        <input className="input search" placeholder="Search recipient or company…" aria-label="Search drafts" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
      </div>
      <QueryState q={q}>
        {(q.data?.items.length ?? 0) === 0 ? <Empty title="No drafts here">Draft emails appear after you research a company and approve at least one finding. Use “Generate drafts…”.</Empty> : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Recipient</th><th>Company</th><th>Subject</th><th>Status</th><th>Written by</th><th>Checks</th><th className="r">Cost</th><th>Updated</th></tr></thead>
            <tbody>{q.data?.items.map((r: any) => (
              <tr key={r.id} className="click" onClick={() => setOpen(r.id)}>
                <td><div className="ell" style={{ maxWidth: 200 }}>{r.contact.name || r.contact.email}</div></td><td><div className="ell" style={{ maxWidth: 160 }}>{r.company.name}</div></td>
                <td><div className="ell" style={{ maxWidth: 260 }}>{r.subject}</div></td><td><StatusBadge status={r.status} />{r.step > 1 && <Badge>follow-up {r.step - 1}</Badge>}</td>
                <td>{r.source === "llm" ? "AI" : r.source === "user" ? "You" : "Template"}</td>
                <td className="nw">{r.blockers > 0 ? <Badge tone="bad">{r.blockers} blocker{r.blockers > 1 ? "s" : ""}</Badge> : r.warnings > 0 ? <Badge tone="warn">{r.warnings} warning{r.warnings > 1 ? "s" : ""}</Badge> : <Badge tone="ok">clean</Badge>}</td>
                <td className="r num">{r.cost_usd_est > 0 ? fmtUsd(r.cost_usd_est) : <span className="muted">free</span>}</td><td className="nw">{ago(r.updated_at)}</td></tr>))}</tbody>
          </table></div>)}
      </QueryState>
      <Pager page={page} pageSize={25} total={q.data?.total ?? 0} onPage={setPage} />
      {open && <DraftEditor id={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}

function DraftEditor({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["draft", id], queryFn: () => get(`/api/drafts/${id}`) });
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const refresh = useRefresh();
  const toast = useToast();
  const openContact = useOpenContact();
  const d = q.data;
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [ack, setAck] = useState(false);
  const [regen, setRegen] = useState(false);
  const [view, setView] = useState<"edit" | "preview">("edit");
  useEffect(() => { if (d) { setSubject(d.version.subject); setBody(d.version.body); setAck(false); } }, [d?.version.id]);
  const dirty = !!d && (subject !== d.version.subject || body !== d.version.body);
  const after = () => refresh("draft", "drafts", "overview", "contacts");
  const save = useMutation({ mutationFn: () => post(`/api/drafts/${id}/edit`, { subject, body }), onSuccess: () => { toast.ok("Saved as a new version. Any earlier approval is void."); after(); }, onError: (e: any) => toast.error(e.message) });
  const approve = useMutation({ mutationFn: () => post(`/api/drafts/${id}/approve`, { acknowledge_warnings: ack }), onSuccess: () => { toast.ok("Approved. This exact text can now be used in a campaign."); after(); }, onError: (e: any) => toast.error(e.message) });
  const reject = useMutation({ mutationFn: () => post(`/api/drafts/${id}/reject`, {}), onSuccess: () => { toast.ok("Draft rejected"); after(); onClose(); }, onError: (e: any) => toast.error(e.message) });
  const blockers = d?.version.blockers ?? [], warnings = d?.version.warnings ?? [];
  const serious = warnings.filter((w: any) => !["role_unknown", "name_check"].includes(w.code));

  return (
    <SlideOver wide onClose={onClose} title={d ? `Draft for ${d.contact.name || d.contact.email}` : "Draft"} subtitle={d && <span className="row wrap"><StatusBadge status={d.status} />{d.status === "approved" && (d.approval_valid ? <Badge tone="ok">approval matches the text</Badge> : <Badge tone="bad">approval no longer valid</Badge>)}<span>version {d.version.no} · {d.version.source === "llm" ? `AI (${d.version.model})` : d.version.source === "user" ? "edited by you" : "template"}</span></span>}>
      <QueryState q={q}>
        {d && (
          <>
            <div className="row wrap between">
              <span className="small"><button className="btn sm ghost" onClick={() => openContact(d.contact.id)}>{d.contact.email}</button> · {d.company?.name} · {d.contact.country ?? "country unknown"} · role: {d.contact.role_title || "unknown"}</span>
              <div className="row"><button className={`btn sm ${view === "edit" ? "primary" : ""}`} onClick={() => setView("edit")}>Edit</button><button className={`btn sm ${view === "preview" ? "primary" : ""}`} onClick={() => setView("preview")}>Exactly what will be sent</button></div>
            </div>
            {blockers.length > 0 && <Banner tone="bad" title="Cannot be approved until fixed"><ul style={{ margin: 0, paddingLeft: 18 }}>{blockers.map((b: any, i: number) => <li key={i}>{b.text}</li>)}</ul></Banner>}
            {warnings.length > 0 && <Banner tone="warn" title="Check before approving"><ul style={{ margin: 0, paddingLeft: 18 }}>{warnings.map((w: any, i: number) => <li key={i}>{w.text}</li>)}</ul></Banner>}

            {view === "edit" ? (
              <div className="col gap-12">
                <Field label="Subject">{(fid) => <input id={fid} className="input" disabled={!admin} value={subject} onChange={(e) => setSubject(e.target.value)} />}</Field>
                <Field label="Message" hint="Write plain paragraphs separated by a blank line. Your signature, postal address, data-source sentence and opt-out line are added automatically (see the preview).">{(fid) => <textarea id={fid} className="textarea" style={{ minHeight: 230 }} disabled={!admin} value={body} onChange={(e) => setBody(e.target.value)} />}</Field>
                <div className="small muted">{body.trim().split(/\s+/).filter(Boolean).length} words</div>
              </div>
            ) : (
              <div className="col gap-8">
                <div className="small sec">From: <b>{d.preview.from}</b></div><div className="small sec">Subject: <b>{d.preview.subject}</b></div>
                <div className="mail">{d.preview.body}</div>
                <details><summary className="small">Headers added</summary><pre className="mail small">{Object.entries(d.preview.headers).map(([k, v]) => `${k}: ${v}`).join("\n")}</pre></details>
                {dirty && <Banner tone="warn">You have unsaved edits. This preview shows the last saved version.</Banner>}
              </div>
            )}

            <Card title="Why this prospect is a fit" subtitle="The approved research this email may use. Click a source to read the page yourself.">
              {d.findings.length === 0 ? <p className="muted small">There are no approved findings for this company, so a personalised email cannot be written. Review the research first.</p> : (
                <div className="col gap-12">{d.findings.map((f: any) => (
                  <div key={f.id} className="col gap-4" style={{ opacity: f.cited ? 1 : 0.75 }}>
                    <div className="row wrap"><Badge tone={f.cited ? "info" : "neutral"}>{f.cited ? "used in this email" : "available"}</Badge><StatusBadge status={f.kind} text={f.kind === "fact" ? "fact" : "inference"} /><StatusBadge status={f.confidence} text={`${f.confidence} confidence`} /><span className="small">{label(f.category)}</span></div>
                    <div className="small">{f.statement}</div>
                    {f.evidence.map((e: any, i: number) => <div key={i} className="quote small">“{e.excerpt}” {e.source && <><a href={e.source.url} target="_blank" rel="noopener noreferrer nofollow">{e.source.title || "source"} ↗</a> <span className="muted">retrieved {fmtDate(e.source.retrieved_at)}</span></>}</div>)}
                  </div>))}</div>)}
            </Card>

            <Card title="Which claim rests on what" subtitle="Each paragraph is linked to the research or sender statement that supports it.">
              <ul className="list">{d.version.claims.map((p: any, i: number) => (
                <li key={i} className="small"><div className="row wrap"><Badge tone={p.type === "company_fact" ? "info" : p.type === "user_edit" ? "warn" : "neutral"}>{label(p.type)}</Badge>
                  {(p.finding_ids ?? []).map((f: number) => <Badge key={f} tone="info">finding #{f}</Badge>)}{(p.sender_refs ?? []).map((r: number) => <Badge key={r}>your statement #{r}</Badge>)}</div><div className="mt-8">{p.text}</div></li>))}</ul>
              <div className="small muted mt-8">Statements you may make about yourself: {d.sender_statements.map((s: any) => `#${s.index} ${s.text}${s.is_proof_point ? " (proof point)" : ""}`).join(" · ") || "none"}</div>
            </Card>

            <div className="small sec">Cost of this version: <b>{d.version.cost_usd_est > 0 ? fmtUsd(d.version.cost_usd_est) : "free (template)"}</b> <span className="basis estimate">estimate</span>{d.version.input_tokens > 0 && ` · ${fmtNum(d.version.input_tokens)} in / ${fmtNum(d.version.output_tokens)} out tokens`} · Regenerating with AI would cost about {fmtUsd(d.estimates.regenerate_usd)} ({d.estimates.model}).</div>

            {d.versions.length > 1 && <details><summary className="small">Version history ({d.versions.length})</summary><ul className="list small">{d.versions.map((v: any) => <li key={v.id}>v{v.no} · {v.source} · {v.created_by} · {ago(v.created_at)}</li>)}</ul></details>}

            {admin && (
              <div className="col gap-12" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                {serious.length > 0 && blockers.length === 0 && <label className="checkbox"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /><span>I have read the warnings above and accept them.</span></label>}
                <div className="row wrap">
                  <button className="btn" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Save edits</button>
                  <button className="btn" onClick={() => setRegen(true)}><Icon name="refresh" size={14} />Regenerate…</button>
                  <span className="grow" />
                  <button className="btn danger" onClick={() => reject.mutate()}>Reject draft</button>
                  <button className="btn primary" disabled={dirty || blockers.length > 0 || (serious.length > 0 && !ack) || d.status === "approved" && d.approval_valid || approve.isPending} onClick={() => approve.mutate()}><Icon name="check" size={14} />{d.status === "approved" && d.approval_valid ? "Approved" : "Approve this exact text"}</button>
                </div>
                {dirty && <span className="xs muted">Save your edits first; approval is tied to the exact saved text.</span>}
              </div>
            )}
            {regen && <RegenModal id={id} onClose={() => setRegen(false)} aiAvailable={d.estimates.ai_available} cost={d.estimates.regenerate_usd} model={d.estimates.model} onDone={after} />}
          </>
        )}
      </QueryState>
    </SlideOver>
  );
}

function RegenModal({ id, onClose, aiAvailable, cost, model, onDone }: { id: number; onClose: () => void; aiAvailable: boolean; cost: number; model: string; onDone: () => void }) {
  const tpls = useQuery({ queryKey: ["templates"], queryFn: () => get("/api/templates") });
  const [tpl, setTpl] = useState<string>("");
  const [tone, setTone] = useState("");
  const [length, setLength] = useState("");
  const [useAi, setUseAi] = useState(aiAvailable);
  const toast = useToast();
  const m = useMutation({ mutationFn: () => post(`/api/drafts/${id}/regenerate`, { template_id: tpl ? Number(tpl) : null, tone: tone || null, length: length || null, use_llm: useAi }), onSuccess: () => { toast.ok("New version created. It needs approval again."); onDone(); onClose(); }, onError: (e: any) => toast.error(e.message) });
  return (
    <Modal title="Regenerate this draft" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={m.isPending} onClick={() => m.mutate()}>{m.isPending ? "Writing…" : "Regenerate"}</button></>}>
      <div className="col gap-12">
        <p className="small sec">Creates a new version (the old one stays in the history). Your manual edits are not carried over.</p>
        <Field label="Template">{(i) => <select id={i} className="select" value={tpl} onChange={(e) => setTpl(e.target.value)}><option value="">Keep current</option>{tpls.data?.map((t: any) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>}</Field>
        <div className="grid g2"><Field label="Tone">{(i) => <select id={i} className="select" value={tone} onChange={(e) => setTone(e.target.value)}><option value="">Template default</option><option value="direct">Direct</option><option value="friendly">Friendly</option><option value="formal">Formal</option></select>}</Field>
          <Field label="Length">{(i) => <select id={i} className="select" value={length} onChange={(e) => setLength(e.target.value)}><option value="">Template default</option><option value="short">Short (about 90 words)</option><option value="medium">Medium (about 140 words)</option></select>}</Field></div>
        <label className="checkbox"><input type="checkbox" checked={useAi} disabled={!aiAvailable} onChange={(e) => setUseAi(e.target.checked)} /><span>Use AI ({model}), about {fmtUsd(cost)} {!aiAvailable && <span className="muted">(not configured: a template draft will be used)</span>}</span></label>
      </div>
    </Modal>
  );
}

function GenerateModal({ initial, onClose }: { initial: { contact_ids?: number[]; filter?: any }; onClose: () => void }) {
  const tpls = useQuery({ queryKey: ["templates"], queryFn: () => get("/api/templates") });
  const [tpl, setTpl] = useState("");
  const [step, setStep] = useState(1);
  const [useAi, setUseAi] = useState(true);
  const [scope, setScope] = useState<"given" | "ready">(initial.contact_ids ? "given" : "ready");
  const nav = useNavigate();
  const toast = useToast();
  const target = scope === "given" ? { contact_ids: initial.contact_ids } : { filter: step > 1 ? { stage: "contacted" } : { researched: "true", eligibility: "eligible", history: "attested_unsent" } };
  const body = { ...target, template_id: Number(tpl), step, use_llm: useAi };
  const est = useQuery({ queryKey: ["draft-estimate", body], enabled: !!tpl, queryFn: () => post("/api/drafts/estimate", body) });
  const go = useMutation({ mutationFn: () => post("/api/drafts/generate", body), onSuccess: (r: any) => { toast.ok(`Generating ${r.contacts} draft(s) in the background`); onClose(); nav("/jobs"); }, onError: (e: any) => toast.error(e.message) });
  useEffect(() => { if (tpls.data && !tpl) setTpl(String(tpls.data[0]?.id ?? "")); }, [tpls.data]);
  return (
    <Modal title="Generate drafts" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!tpl || go.isPending} onClick={() => go.mutate()}>Generate</button></>}>
      <div className="col gap-12">
        <p className="small sec">Drafts are created for review. Contacts without approved research are skipped (they cannot be personalised). Nothing is approved or sent.</p>
        {initial.contact_ids && <div className="row"><label className="chip"><input type="radio" checked={scope === "given"} onChange={() => setScope("given")} />The {initial.contact_ids.length} contacts I selected</label><label className="chip"><input type="radio" checked={scope === "ready"} onChange={() => setScope("ready")} />All eligible + researched</label></div>}
        {!initial.contact_ids && <Banner tone="info">Drafting for eligible contacts whose company is researched and who have no active draft yet (at most 200 per run).</Banner>}
        <Field label="Template">{(i) => <select id={i} className="select" value={tpl} onChange={(e) => setTpl(e.target.value)}>{tpls.data?.map((t: any) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>}</Field>
        <Field label="Which email in the sequence?">{(i) => <select id={i} className="select" value={step} onChange={(e) => setStep(Number(e.target.value))}><option value={1}>First email</option><option value={2}>Follow-up 1 (only to people who did not reply)</option><option value={3}>Follow-up 2</option></select>}</Field>
        <label className="checkbox"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /><span>Use AI for natural wording (falls back to templates if no key is configured)</span></label>
        {est.data && <Banner tone={est.data.mode === "llm" ? "info" : "ok"}>{fmtNum(est.data.drafts)} draft(s) · {est.data.mode === "llm" ? <>about <b>{fmtUsd(est.data.total_usd)}</b> with {est.data.model} <span className="basis estimate">estimate</span></> : "free template mode"}</Banner>}
      </div>
    </Modal>
  );
}

function Templates({ admin }: { admin: boolean }) {
  const q = useQuery({ queryKey: ["templates"], queryFn: () => get("/api/templates") });
  const [edit, setEdit] = useState<any | null>(null);
  return (
    <Card pad={false} title="Reusable templates" subtitle="Tone, length and positioning guide how drafts are written. They never override the evidence rules." actions={admin && <button className="btn sm" onClick={() => setEdit({})}><Icon name="plus" size={13} />New</button>}>
      <QueryState q={q}>
        <ul className="list" style={{ padding: "0 16px" }}>{q.data?.map((t: any) => (
          <li key={t.id} className="row between wrap"><div><b>{t.name}</b><div className="small muted">{label(t.positioning)} · {t.tone} · {t.length}{t.cta_text ? ` · CTA: ${t.cta_text}` : ""}</div></div>{admin && <button className="btn sm" onClick={() => setEdit(t)}>Edit</button>}</li>))}</ul>
      </QueryState>
      {edit && <TemplateModal t={edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

function TemplateModal({ t, onClose }: { t: any; onClose: () => void }) {
  const [v, setV] = useState({ name: t.name ?? "", positioning: t.positioning ?? "direct_solution", tone: t.tone ?? "direct", length: t.length ?? "short", instructions: t.instructions ?? "", subject_hint: t.subject_hint ?? "", cta_text: t.cta_text ?? "" });
  const refresh = useRefresh();
  const toast = useToast();
  const save = useMutation({ mutationFn: () => (t.id ? put(`/api/templates/${t.id}`, v) : post("/api/templates", v)), onSuccess: () => { toast.ok("Template saved"); refresh("templates"); onClose(); }, onError: (e: any) => toast.error(e.message) });
  const remove = useMutation({ mutationFn: () => del(`/api/templates/${t.id}`), onSuccess: () => { refresh("templates"); onClose(); } });
  const set = (k: string) => (e: any) => setV({ ...v, [k]: e.target.value });
  return (
    <Modal title={t.id ? "Edit template" : "New template"} onClose={onClose} footer={<>{t.id && <button className="btn danger" onClick={() => remove.mutate()}>Delete</button>}<button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={v.name.length < 2} onClick={() => save.mutate()}>Save</button></>}>
      <div className="col gap-12">
        <Field label="Name">{(i) => <input id={i} className="input" value={v.name} onChange={set("name")} />}</Field>
        <div className="grid g3">
          <Field label="Positioning">{(i) => <select id={i} className="select" value={v.positioning} onChange={set("positioning")}><option value="agency_capacity">Agency: capacity</option><option value="direct_solution">Company: solution</option></select>}</Field>
          <Field label="Tone">{(i) => <select id={i} className="select" value={v.tone} onChange={set("tone")}><option value="direct">Direct</option><option value="friendly">Friendly</option><option value="formal">Formal</option></select>}</Field>
          <Field label="Length">{(i) => <select id={i} className="select" value={v.length} onChange={set("length")}><option value="short">Short</option><option value="medium">Medium</option></select>}</Field>
        </div>
        <Field label="Subject idea" hint="{company} is replaced by the company name.">{(i) => <input id={i} className="input" value={v.subject_hint} onChange={set("subject_hint")} />}</Field>
        <Field label="Next step sentence (low pressure)">{(i) => <textarea id={i} className="textarea" style={{ minHeight: 60 }} value={v.cta_text} onChange={set("cta_text")} />}</Field>
        <Field label="Extra instructions for the AI (optional)">{(i) => <textarea id={i} className="textarea" style={{ minHeight: 70 }} value={v.instructions} onChange={set("instructions")} />}</Field>
      </div>
    </Modal>
  );
}
