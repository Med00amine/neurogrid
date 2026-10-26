import { useMemo, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { download, get, post, qs } from "../api";
import { useAuth } from "../auth";
import { useOpenContact, useRefresh } from "../components/ContactDrawer";
import ImportWizard from "../components/ImportWizard";
import { Badge, Banner, Card, Empty, Field, Icon, Modal, PageHead, Pager, QueryState, StatusBadge, Tabs, useDebounced, useToast } from "../components/ui";
import { ago, fmtNum, label } from "../format";

const ELIG = ["unreviewed", "review_required", "eligible", "excluded"];
const HIST = ["unknown", "attested_unsent", "previously_contacted"];
const STAGES = ["new", "researched", "qualified", "draft_ready", "approved", "contacted", "replied", "interested", "meeting_scheduled", "proposal_sent", "won", "lost", "do_not_contact"];
const SORTABLE: Record<string, string> = { Contact: "email", Name: "name", Company: "company", Country: "country", Stage: "stage", Score: "score" };

export default function Contacts() {
  const [tab, setTab] = useState<"contacts" | "imports">("contacts");
  const [wizard, setWizard] = useState(false);
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const toast = useToast();

  return (
    <>
      <PageHead title="Contacts" actions={admin && <>
        <button className="btn" onClick={() => download(`/api/contacts/export?format=csv`, "contacts.csv").catch((e) => toast.error(e.message))}><Icon name="download" size={14} />Export CSV</button>
        <button className="btn primary" onClick={() => setWizard(true)}><Icon name="upload" size={14} />Import…</button>
      </>}>
        Your prospect database. Nothing here is emailed automatically. A contact only becomes sendable after you review it, resolve its email history and its country's rules.
      </PageHead>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "contacts", label: "All contacts" }, { id: "imports", label: "Import history" }]} />
      <div className="mt-16">{tab === "contacts" ? <ContactsTable /> : <ImportsTable />}</div>
      {wizard && <ImportWizard onClose={() => setWizard(false)} />}
    </>
  );
}

function ContactsTable() {
  const [p, setP] = useSearchParams();
  const nav = useNavigate();
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const openContact = useOpenContact();
  const [search, setSearch] = useState(p.get("search") ?? "");
  const dSearch = useDebounced(search);
  const f = {
    search: dSearch, eligibility: p.get("eligibility") ?? "", history: p.get("history") ?? "", stage: p.get("stage") ?? "", country: p.get("country") ?? "",
    suppressed: p.get("suppressed") ?? "", researched: p.get("researched") ?? "", role_account: p.get("role_account") ?? "",
  };
  const page = Number(p.get("page") ?? 1), sort = p.get("sort") ?? "created", desc = p.get("desc") !== "0";
  const params = { ...f, sort, desc: desc ? "true" : "false", page, page_size: 50 };
  const q = useQuery({ queryKey: ["contacts", params], queryFn: () => get(`/api/contacts${qs(params)}`), placeholderData: (x) => x });
  const facets = useQuery({ queryKey: ["contacts-facets"], queryFn: () => get("/api/contacts/facets") });
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [bulk, setBulk] = useState(false);

  const set = (k: string, v: string) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); n.delete("page"); setP(n, { replace: true }); setSel(new Set()); setAllMatching(false); };
  const setPage = (n: number) => { const x = new URLSearchParams(p); x.set("page", String(n)); setP(x, { replace: true }); };
  const items: any[] = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const allOnPage = items.length > 0 && items.every((i) => sel.has(i.id));
  const selectedCount = allMatching ? total : sel.size;
  const activeFilter = useMemo(() => Object.fromEntries(Object.entries({ ...f }).filter(([, v]) => v !== "")), [dSearch, f.eligibility, f.history, f.stage, f.country, f.suppressed, f.researched, f.role_account]);

  function toggleSort(col: string) {
    const key = SORTABLE[col]; if (!key) return;
    const n = new URLSearchParams(p);
    if (sort === key) n.set("desc", desc ? "0" : "1"); else { n.set("sort", key); n.set("desc", "0"); }
    setP(n, { replace: true });
  }

  return (
    <Card pad={false}>
      <div className="filters">
        <input className="input search" placeholder="Search email, name or company…" aria-label="Search contacts" value={search} onChange={(e) => { setSearch(e.target.value); const n = new URLSearchParams(p); n.delete("page"); setP(n, { replace: true }); }} />
        <Select label="Review" value={f.eligibility} onChange={(v) => set("eligibility", v)} options={ELIG.map((x) => [x, `${label(x)}${facets.data ? ` (${fmtNum(facets.data.eligibility[x] ?? 0)})` : ""}`])} />
        <Select label="Email history" value={f.history} onChange={(v) => set("history", v)} options={HIST.map((x) => [x, `${label(x)}${facets.data ? ` (${fmtNum(facets.data.history[x] ?? 0)})` : ""}`])} />
        <Select label="Stage" value={f.stage} onChange={(v) => set("stage", v)} options={STAGES.map((x) => [x, label(x)])} />
        <Select label="Country" value={f.country} onChange={(v) => set("country", v)} options={Object.entries(facets.data?.country ?? {}).map(([c, n]) => [c, `${c === "ZZ" ? "Unknown" : c} (${fmtNum(n as number)})`])} />
        <Select label="Researched" value={f.researched} onChange={(v) => set("researched", v)} options={[["true", "Researched"], ["false", "Not researched"]]} />
        <Select label="Suppressed" value={f.suppressed} onChange={(v) => set("suppressed", v)} options={[["true", "Suppressed"], ["false", "Not suppressed"]]} />
        {Object.keys(activeFilter).length > 0 && <button className="btn sm ghost" onClick={() => { setSearch(""); setP(new URLSearchParams(), { replace: true }); }}>Clear filters</button>}
      </div>
      {admin && selectedCount > 0 && (
        <div className="filters" style={{ background: "var(--accent-soft)" }} role="region" aria-label="Bulk actions">
          <b>{fmtNum(selectedCount)} selected</b>
          {!allMatching && allOnPage && total > items.length && <button className="btn sm ghost" onClick={() => setAllMatching(true)}>Select all {fmtNum(total)} matching the filters</button>}
          <button className="btn sm primary" onClick={() => setBulk(true)}>Bulk actions…</button>
          <button className="btn sm" onClick={() => nav("/research", { state: { contactIds: allMatching ? null : [...sel], filter: allMatching ? activeFilter : null } })}>Research…</button>
          <button className="btn sm ghost" onClick={() => { setSel(new Set()); setAllMatching(false); }}>Clear selection</button>
        </div>
      )}
      <QueryState q={q}>
        {items.length === 0 ? (
          <Empty title={total === 0 && Object.keys(activeFilter).length === 0 ? "No contacts yet" : "No contacts match these filters"}>
            {Object.keys(activeFilter).length === 0 ? "Use Import… to load your spreadsheet. You will see a preview and choose which column is the email before anything is saved." : "Try removing a filter."}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead><tr>
                {admin && <th style={{ width: 36 }}><input type="checkbox" aria-label="Select all on this page" checked={allOnPage} onChange={(e) => { const n = new Set(sel); items.forEach((i) => (e.target.checked ? n.add(i.id) : n.delete(i.id))); setSel(n); setAllMatching(false); }} /></th>}
                {["Contact", "Name", "Company", "Country", "Review", "Email history", "Stage", "Score"].map((h) => (
                  <th key={h} className={SORTABLE[h] ? "sortable" : ""} onClick={() => toggleSort(h)} aria-sort={SORTABLE[h] === sort ? (desc ? "descending" : "ascending") : undefined}>{h}{SORTABLE[h] === sort ? (desc ? " ↓" : " ↑") : ""}</th>
                ))}
              </tr></thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id} className={`click ${sel.has(c.id) ? "sel" : ""}`} onClick={() => openContact(c.id)}>
                    {admin && <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${c.email}`} checked={sel.has(c.id) || allMatching} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(c.id) : n.delete(c.id); setSel(n); setAllMatching(false); }} /></td>}
                    <td><div className="ell" style={{ maxWidth: 260 }} title={c.email}>{c.email}</div></td>
                    <td><div className="ell" style={{ maxWidth: 160 }}>{c.name || <span className="muted">–</span>}</div></td>
                    <td><div className="ell" style={{ maxWidth: 170 }} title={c.company?.domain}>{c.company?.name || <span className="muted">{c.company?.domain}</span>}</div></td>
                    <td className="nw">{c.country ?? <span className="muted">?</span>}</td>
                    <td className="nw"><span className="row gap-4"><StatusBadge status={c.eligibility_status} />{c.is_suppressed && <StatusBadge status="suppressed" />}</span></td>
                    <td className="nw"><Badge tone={c.history_status === "attested_unsent" ? "ok" : c.history_status === "unknown" ? "bad" : "warn"}>{label(c.history_status)}</Badge></td>
                    <td className="nw"><StatusBadge status={c.pipeline_stage} /></td>
                    <td className="nw num">{c.score ?? <span className="muted">–</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
      <Pager page={page} pageSize={50} total={total} onPage={setPage} />
      {bulk && <BulkModal count={selectedCount} ids={allMatching ? [] : [...sel]} filter={allMatching ? activeFilter : {}} onClose={() => setBulk(false)} onDone={() => { setBulk(false); setSel(new Set()); setAllMatching(false); }} />}
    </Card>
  );
}

function Select({ label: lbl, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[][] }) {
  return (
    <select className="select" aria-label={lbl} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{lbl}: all</option>
      {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
    </select>
  );
}

const ACTIONS: Record<string, { title: string; help: string; needsValue?: "status" | "country" | "stage"; reasonLabel: string; minReason: number }> = {
  set_eligibility: { title: "Set review decision", help: "Marks these contacts as eligible, excluded or needs-review. Free-mail addresses and suppressed people can never be made eligible.", needsValue: "status", reasonLabel: "Why? (recorded for every contact)", minReason: 10 },
  attest_history: { title: "I confirm these were never emailed before", help: "Only contacts whose history is currently 'unknown' change. Use it when you are certain none of them were contacted by you or an older tool. If unsure, import your sent log instead.", reasonLabel: "How do you know? (e.g. the list was exported this week and no tool has emailed it)", minReason: 10 },
  set_country: { title: "Set country", help: "The country decides which outreach rules apply. Use it only when you know where the company is (for example from its imprint).", needsValue: "country", reasonLabel: "Why is this the country?", minReason: 5 },
  record_consent: { title: "Record consent", help: "Only if these people actually agreed to receive email from you. This unlocks sending in strict countries, so be accurate.", reasonLabel: "Describe the consent (when and how it was given)", minReason: 10 },
};

function BulkModal({ count, ids, filter, onClose, onDone }: { count: number; ids: number[]; filter: Record<string, any>; onClose: () => void; onDone: () => void }) {
  const [action, setAction] = useState("set_eligibility");
  const [value, setValue] = useState("eligible");
  const [reason, setReason] = useState("");
  const [sure, setSure] = useState(false);
  const refresh = useRefresh();
  const toast = useToast();
  const a = ACTIONS[action];
  const m = useMutation({
    mutationFn: () => post("/api/contacts/bulk", { action, value: a.needsValue ? value : null, reason, ids, filter: ids.length ? {} : filter, expected_count: count }),
    onSuccess: (r: any) => { toast.ok(`${fmtNum(r.changed)} changed, ${fmtNum(r.skipped)} skipped${r.skipped ? ` (${Object.entries(r.skipped_reasons).map(([k, v]) => `${v} ${k}`).join(", ")})` : ""}`); refresh("contacts", "contacts-facets", "overview", "contact"); onDone(); },
    onError: (e: any) => toast.error(e.message),
  });
  return (
    <Modal title={`Bulk action on ${fmtNum(count)} contact${count === 1 ? "" : "s"}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={reason.trim().length < a.minReason || !sure || m.isPending} onClick={() => m.mutate()}>{m.isPending ? "Working…" : "Apply"}</button></>}>
      <div className="col gap-12">
        <Field label="Action">{(id) => <select id={id} className="select" value={action} onChange={(e) => { setAction(e.target.value); setValue(e.target.value === "set_eligibility" ? "eligible" : ""); }}>{Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v.title}</option>)}</select>}</Field>
        <p className="small sec">{a.help}</p>
        {a.needsValue === "status" && <Field label="Decision">{(id) => <select id={id} className="select" value={value} onChange={(e) => setValue(e.target.value)}><option value="eligible">Eligible to contact</option><option value="review_required">Needs more review</option><option value="excluded">Exclude</option></select>}</Field>}
        {a.needsValue === "country" && <Field label="2-letter country code" hint="e.g. FR, DE, GB, IT">{(id) => <input id={id} className="input" maxLength={2} value={value} onChange={(e) => setValue(e.target.value.toUpperCase())} />}</Field>}
        <Field label={a.reasonLabel}>{(id) => <textarea id={id} className="textarea" style={{ minHeight: 70 }} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
        <label className="checkbox"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /><span>I understand this changes <b>{fmtNum(count)}</b> contact{count === 1 ? "" : "s"} and is written to the audit trail.</span></label>
      </div>
    </Modal>
  );
}

function ImportsTable() {
  const q = useQuery({ queryKey: ["imports"], queryFn: () => get("/api/imports"), refetchInterval: 5000 });
  return (
    <Card pad={false}>
      <QueryState q={q}>
        {q.data?.length === 0 ? <Empty title="No imports yet">Imported files appear here with how many rows were added, skipped or invalid.</Empty> : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>#</th><th>File</th><th>Type</th><th>Status</th><th className="r">Rows</th><th className="r">Added</th><th className="r">Duplicates</th><th className="r">Invalid</th><th>History setting</th><th>When</th></tr></thead>
            <tbody>{q.data?.map((b: any) => (
              <tr key={b.id}><td>{b.id}</td><td><div className="ell" style={{ maxWidth: 220 }} title={b.provenance_note || ""}>{b.filename}</div></td><td>{label(b.kind)}</td><td><StatusBadge status={b.status} /></td>
                <td className="r num">{fmtNum(b.total_rows)}</td><td className="r num">{fmtNum(b.inserted)}</td><td className="r num">{fmtNum(b.duplicate_in_file + b.duplicate_existing)}</td><td className="r num">{fmtNum(b.invalid)}</td>
                <td>{b.kind === "contacts" ? label(b.history_attestation) : "–"}</td><td className="nw">{ago(b.created_at)}</td></tr>))}</tbody>
          </table></div>)}
      </QueryState>
    </Card>
  );
}
