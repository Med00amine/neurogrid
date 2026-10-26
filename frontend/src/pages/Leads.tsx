import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { del, get, post, put, qs } from "../api";
import { useAuth } from "../auth";
import { Columns, HBars } from "../components/charts";
import { useOpenContact, useRefresh } from "../components/ContactDrawer";
import { Badge, Banner, Card, Empty, Field, Icon, Modal, PageHead, Pager, QueryState, SlideOver, StatusBadge, useToast } from "../components/ui";
import { fmtNum, label } from "../format";

const COMPANY_TYPES = ["ai_consultancy", "software_agency", "aws_consultancy", "product_company", "other"];
const TAGS = ["python_fastapi", "ai_llm", "rag_documents", "integrations", "aws_devops", "ai_testing"];

export default function Leads() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const nav = useNavigate();
  const openContact = useOpenContact();
  const [segment, setSegment] = useState<number | "">("");
  const [minScore, setMinScore] = useState("0");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [detail, setDetail] = useState<number | null>(null);
  const [editSeg, setEditSeg] = useState<any | null>(null);
  const summary = useQuery({ queryKey: ["leads-summary"], queryFn: () => get("/api/leads/summary") });
  const segs = useQuery({ queryKey: ["segments"], queryFn: () => get("/api/segments") });
  const q = useQuery({
    queryKey: ["leads", segment, minScore, page],
    queryFn: () => get(`/api/contacts${qs({ min_score: minScore, segment_id: segment, sort: "score", desc: "true", page, page_size: 25 })}`),
    placeholderData: (x) => x,
  });
  const items: any[] = q.data?.items ?? [];
  const bands = summary.data?.bands ?? {};

  return (
    <>
      <PageHead title="Leads & scoring" actions={admin && <button className="btn" onClick={() => setEditSeg({})}><Icon name="plus" size={14} />New segment</button>}>
        An explainable ranking of how well the saved, approved evidence supports contacting each person. <b>It is not a prediction that anyone will reply or buy.</b>
      </PageHead>
      <div className="grid g-wide-narrow">
        <Card title="Score distribution" subtitle={summary.data ? `${fmtNum(summary.data.scored_contacts)} contacts scored` : ""}>
          {summary.data && summary.data.scored_contacts > 0 ? <Columns data={summary.data.histogram.map((h: any) => ({ label: h.range, value: h.count }))} unit="contacts" /> : <p className="muted small">No contact is scored yet. Research a company and approve its findings; scores appear automatically.</p>}
        </Card>
        <Card title="By evidence strength">
          <HBars rows={[{ name: "Strong (70+)", value: bands["strong evidence"] ?? 0 }, { name: "Some (40–69)", value: bands["some evidence"] ?? 0 }, { name: "Weak (<40)", value: bands["weak evidence"] ?? 0 }]} emptyText="Nothing scored yet." />
        </Card>
      </div>

      <h2 className="mt-24">Segments</h2>
      <div className="grid g4 mt-8">
        {segs.data?.map((s: any) => (
          <button key={s.id} className="card card-pad col gap-4" style={{ textAlign: "left", font: "inherit", color: "inherit", cursor: "pointer", boxShadow: segment === s.id ? "0 0 0 2px var(--accent)" : undefined }} onClick={() => { setSegment(segment === s.id ? "" : s.id); setPage(1); }} aria-pressed={segment === s.id}>
            <div className="row between"><b>{s.name}</b><Badge tone="info">{fmtNum(s.members)}</Badge></div>
            <span className="small sec">{s.description}</span>
            <span className="xs muted">Email style: {label(s.positioning)}</span>
            {admin && <span className="row"><span className="btn sm ghost" role="button" tabIndex={0} onClick={(e) => { e.stopPropagation(); setEditSeg(s); }} onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); setEditSeg(s); } }}>Edit</span></span>}
          </button>))}
      </div>

      <Card pad={false} title={segment ? `Leads in “${segs.data?.find((s: any) => s.id === segment)?.name}”` : "Scored leads"} actions={
        <div className="row">
          <select className="select" style={{ width: 150 }} aria-label="Minimum score" value={minScore} onChange={(e) => { setMinScore(e.target.value); setPage(1); }}><option value="0">Any score</option><option value="40">40+</option><option value="70">70+</option></select>
          {admin && <button className="btn sm primary" disabled={sel.size === 0} onClick={() => nav("/studio", { state: { contactIds: [...sel] } })}>Draft emails for {sel.size || ""} selected</button>}
        </div>}>
        <div style={{ marginTop: 12 }} />
        <QueryState q={q}>
          {items.length === 0 ? <Empty title="No scored leads">Leads are scored once their company has been researched. Try “Company research”.</Empty> : (
            <div className="table-wrap"><table className="t">
              <thead><tr>{admin && <th style={{ width: 36 }} />}<th>Contact</th><th>Company</th><th>Type</th><th>Review</th><th className="r">Score</th><th>Evidence</th></tr></thead>
              <tbody>{items.map((c) => (
                <tr key={c.id} className="click" onClick={() => setDetail(c.id)}>
                  {admin && <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${c.email}`} checked={sel.has(c.id)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(c.id) : n.delete(c.id); setSel(n); }} /></td>}
                  <td><div className="ell" style={{ maxWidth: 240 }}>{c.name || c.email}<div className="xs muted">{c.email}</div></div></td>
                  <td><div className="ell" style={{ maxWidth: 180 }}>{c.company?.name}</div></td><td>{c.company?.type ? label(c.company.type) : <span className="muted">?</span>}</td>
                  <td><StatusBadge status={c.eligibility_status} /></td>
                  <td className="r num"><b>{c.score}</b></td>
                  <td><Badge tone={c.score >= 70 ? "ok" : c.score >= 40 ? "warn" : "neutral"}>{c.score >= 70 ? "Strong" : c.score >= 40 ? "Some" : "Weak"}</Badge></td>
                </tr>))}</tbody>
            </table></div>)}
        </QueryState>
        <Pager page={page} pageSize={25} total={q.data?.total ?? 0} onPage={setPage} />
      </Card>
      {detail && <LeadPanel id={detail} onClose={() => setDetail(null)} onOpenContact={() => { openContact(detail); }} />}
      {editSeg && <SegmentModal seg={editSeg} onClose={() => setEditSeg(null)} />}
    </>
  );
}

function LeadPanel({ id, onClose, onOpenContact }: { id: number; onClose: () => void; onOpenContact: () => void }) {
  const q = useQuery({ queryKey: ["lead", id], queryFn: () => get(`/api/leads/${id}`) });
  const d = q.data;
  return (
    <SlideOver onClose={onClose} title="Why this score" subtitle={d && <span>{d.total}/100 · {d.band}</span>}>
      <QueryState q={q}>
        {d && (
          <>
            <Banner tone="info">{d.disclaimer}</Banner>
            {Object.entries(d.components).map(([k, v]: any) => (
              <div key={k} className="col gap-4">
                <div className="row between"><b>{label(k)}</b><span className="num">{v.points} / {v.max}</span></div>
                <div className="meter" aria-hidden><i style={{ width: `${v.max ? (v.points / v.max) * 100 : 0}%` }} /></div>
                <div className="small sec">{v.rationale}{v.unknown && <Badge>unknown</Badge>}</div>
                {v.finding_ids.map((fid: number) => d.findings[fid] && (
                  <div key={fid} className="quote small"><b>{label(d.findings[fid].category)}</b> ({d.findings[fid].kind}): {d.findings[fid].statement}
                    {d.findings[fid].evidence.map((e: any, i: number) => <div key={i} className="xs muted">“{e.excerpt}” {e.verified ? "✓" : "✗"}</div>)}</div>))}
              </div>))}
            <button className="btn" onClick={onOpenContact}>Open contact</button>
          </>
        )}
      </QueryState>
    </SlideOver>
  );
}

function SegmentModal({ seg, onClose }: { seg: any; onClose: () => void }) {
  const f = seg.filter ?? {};
  const [name, setName] = useState(seg.name ?? "");
  const [desc, setDesc] = useState(seg.description ?? "");
  const [pos, setPos] = useState(seg.positioning ?? "direct_solution");
  const [types, setTypes] = useState<string[]>(f.company_types ?? []);
  const [tags, setTags] = useState<string[]>(f.service_tags ?? []);
  const [countries, setCountries] = useState((f.countries ?? []).join(", "));
  const [keywords, setKeywords] = useState((f.keywords ?? []).join(", "));
  const [minScore, setMinScore] = useState(String(f.min_score ?? ""));
  const refresh = useRefresh();
  const toast = useToast();
  const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const body = { name, description: desc, positioning: pos, filter: { company_types: types, service_tags: tags, countries: split(countries), keywords: split(keywords), ...(minScore ? { min_score: Number(minScore) } : {}) } };
  const save = useMutation({ mutationFn: () => (seg.id ? put(`/api/segments/${seg.id}`, body) : post("/api/segments", body)), onSuccess: () => { toast.ok("Segment saved"); refresh("segments", "leads"); onClose(); }, onError: (e: any) => toast.error(e.message) });
  const remove = useMutation({ mutationFn: () => del(`/api/segments/${seg.id}`), onSuccess: () => { refresh("segments", "leads"); onClose(); } });
  const toggle = (arr: string[], set: (v: string[]) => void, v: string) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  return (
    <Modal title={seg.id ? "Edit segment" : "New segment"} onClose={onClose} footer={<>{seg.id && <button className="btn danger" onClick={() => remove.mutate()}>Delete</button>}<button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={name.trim().length < 2 || save.isPending} onClick={() => save.mutate()}>Save</button></>}>
      <div className="col gap-12">
        <Field label="Name">{(id) => <input id={id} className="input" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Description">{(id) => <input id={id} className="input" value={desc} onChange={(e) => setDesc(e.target.value)} />}</Field>
        <Field label="Email style for this segment" hint="Agencies are pitched extra engineering capacity; other companies a solution to a specific need.">{(id) => <select id={id} className="select" value={pos} onChange={(e) => setPos(e.target.value)}><option value="agency_capacity">Agency: extra engineering capacity</option><option value="direct_solution">Company: help with a specific need</option></select>}</Field>
        <div className="field"><span className="lbl">Company type (confirmed during research)</span><div className="row wrap">{COMPANY_TYPES.map((t) => <label key={t} className="chip"><input type="checkbox" checked={types.includes(t)} onChange={() => toggle(types, setTypes, t)} />{label(t)}</label>)}</div></div>
        <div className="field"><span className="lbl">An approved finding is tagged with (any of)</span><div className="row wrap">{TAGS.map((t) => <label key={t} className="chip"><input type="checkbox" checked={tags.includes(t)} onChange={() => toggle(tags, setTags, t)} />{label(t)}</label>)}</div></div>
        <div className="grid g2"><Field label="Countries (codes, comma separated)">{(id) => <input id={id} className="input" value={countries} onChange={(e) => setCountries(e.target.value)} placeholder="FR, GB" />}</Field><Field label="Company name contains">{(id) => <input id={id} className="input" value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="ai, labs" />}</Field></div>
        <Field label="Minimum score (optional)">{(id) => <input id={id} className="input" type="number" min="0" max="100" value={minScore} onChange={(e) => setMinScore(e.target.value)} />}</Field>
      </div>
    </Modal>
  );
}
