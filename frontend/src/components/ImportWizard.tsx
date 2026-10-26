import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post, upload } from "../api";
import { fmtDuration, fmtNum, label } from "../format";
import { useRefresh } from "./ContactDrawer";
import { Banner, Field, Modal, Progress, StatusBadge, useToast } from "./ui";

type Kind = "contacts" | "history" | "suppressions";
const KINDS: { id: Kind; title: string; help: string }[] = [
  { id: "contacts", title: "Contacts", help: "Your prospect list (email, name, company). New contacts only: existing ones are never overwritten." },
  { id: "history", title: "Sent history", help: "A log of people you already emailed with an older tool. They are blocked from automatic sending, and bounces/unsubscribes in it become suppressions." },
  { id: "suppressions", title: "Do-not-contact list", help: "Emails or domains that must never be contacted. This can only ever add people to the suppression list." },
];

export default function ImportWizard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [kind, setKind] = useState<Kind>("contacts");
  const [prev, setPrev] = useState<any>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [extras, setExtras] = useState<string[]>([]);
  const [sheet, setSheet] = useState<string>("");
  const [note, setNote] = useState("");
  const [attest, setAttest] = useState<"unknown" | "attested_unsent">("unknown");
  const [started, setStarted] = useState<{ batch_id: number; job_id: string } | null>(null);
  const file = useRef<File | null>(null);
  const toast = useToast();
  const refresh = useRefresh();

  const up = useMutation({
    mutationFn: async (sheetName?: string) => {
      const fd = new FormData();
      fd.append("kind", kind);
      if (sheetName) fd.append("sheet", sheetName);
      fd.append("file", file.current!);
      return upload("/api/imports/upload", fd);
    },
    onSuccess: (r: any) => { setPrev(r); setMapping(r.suggested_mapping); setExtras(r.suggested_extra_columns); setStep(2); },
    onError: (e: any) => toast.error(e.message),
  });
  const start = useMutation({
    mutationFn: () => post("/api/imports/start", { file_ref: prev.file_ref, kind, mapping, extra_columns: extras, sheet: sheet || null, provenance_note: note, history_attestation: kind === "contacts" ? attest : "unknown", filename: prev.filename }),
    onSuccess: (r: any) => { setStarted(r); setStep(4); },
    onError: (e: any) => toast.error(e.message),
  });

  const missing = (prev?.fields ?? []).filter((f: any) => f.required && !mapping[f.field]);
  const dup = Object.values(mapping).filter(Boolean).length !== new Set(Object.values(mapping).filter(Boolean)).size;

  return (
    <Modal wide title={step === 4 ? "Importing" : `Import: step ${step} of 3`} onClose={step === 4 ? () => { refresh("contacts", "contacts-facets", "overview", "imports"); onClose(); } : onClose}
      footer={step === 1 ? <button className="btn" onClick={onClose}>Cancel</button> : step === 2 ? <>
        <button className="btn" onClick={() => setStep(1)}>Back</button>
        <button className="btn primary" disabled={missing.length > 0 || dup} onClick={() => setStep(3)}>Continue</button></> : step === 3 ? <>
        <button className="btn" onClick={() => setStep(2)}>Back</button>
        <button className="btn primary" disabled={start.isPending || (kind === "contacts" && attest === "attested_unsent" && note.trim().length < 10)} onClick={() => start.mutate()}>{start.isPending ? "Starting…" : `Import ${fmtNum(prev?.total_rows)} rows`}</button></> : null}>
      {step === 1 && (
        <div className="col gap-16">
          <div className="grid g3">
            {KINDS.map((k) => (
              <label key={k.id} className="card card-pad col gap-4" style={{ cursor: "pointer", borderColor: kind === k.id ? "var(--accent)" : undefined, boxShadow: kind === k.id ? "0 0 0 2px var(--accent)" : undefined }}>
                <span className="row"><input type="radio" name="kind" checked={kind === k.id} onChange={() => setKind(k.id)} /><b>{k.title}</b></span>
                <span className="small sec">{k.help}</span>
              </label>
            ))}
          </div>
          <Field label="Spreadsheet file (.xlsx or .csv)" hint="You'll see a preview and choose columns before anything is saved. Files stay on this computer.">
            {(id) => <input id={id} className="input" type="file" accept=".xlsx,.csv" onChange={(e) => { file.current = e.target.files?.[0] ?? null; if (file.current) up.mutate(undefined); }} />}
          </Field>
          {up.isPending && <p className="small sec">Reading file…</p>}
        </div>
      )}
      {step === 2 && prev && (
        <div className="col gap-16">
          {prev.previously_imported.length > 0 && <Banner tone="warn" title="This exact file was imported before">Re-importing is safe: existing contacts are not changed and nothing is duplicated.</Banner>}
          <div className="row between wrap">
            <span><b>{prev.filename}</b> · {fmtNum(prev.total_rows)} rows · {prev.headers.length} columns</span>
            {prev.sheets.length > 1 && <select className="select" style={{ width: 200 }} value={sheet} aria-label="Sheet" onChange={(e) => { setSheet(e.target.value); up.mutate(e.target.value); }}>{prev.sheets.map((s: string) => <option key={s}>{s}</option>)}</select>}
          </div>
          <div className="table-wrap card" style={{ maxHeight: 220, overflow: "auto" }}>
            <table className="t"><thead><tr>{prev.headers.map((h: string) => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>{prev.sample_rows.slice(0, 6).map((r: any, i: number) => <tr key={i}>{prev.headers.map((h: string) => <td key={h}><div className="ell" style={{ maxWidth: 200 }}>{String(r[h] ?? "")}</div></td>)}</tr>)}</tbody></table>
          </div>
          <div className="grid g2">
            {prev.fields.map((f: any) => (
              <Field key={f.field} label={`${label(f.field)}${f.required ? " *" : ""}`} hint={f.description}>
                {(id) => <select id={id} className="select" value={mapping[f.field] ?? ""} onChange={(e) => setMapping({ ...mapping, [f.field]: e.target.value || null })}><option value="">— not in my file —</option>{prev.headers.map((h: string) => <option key={h} value={h}>{h}</option>)}</select>}
              </Field>
            ))}
          </div>
          {dup && <Banner tone="bad">The same column is used for two fields.</Banner>}
          {kind === "contacts" && (
            <div className="col gap-4"><b className="small">Also keep these columns (optional)</b>
              <div className="row wrap">{prev.headers.filter((h: string) => !Object.values(mapping).includes(h)).map((h: string) => (
                <label key={h} className="chip"><input type="checkbox" checked={extras.includes(h)} onChange={(e) => setExtras(e.target.checked ? [...extras, h] : extras.filter((x) => x !== h))} />{h}</label>))}</div>
              <span className="xs muted">Unselected columns are not stored (data minimisation).</span></div>
          )}
        </div>
      )}
      {step === 3 && prev && (
        <div className="col gap-16">
          {kind === "contacts" ? (
            <>
              <Field label="Where does this list come from?" hint="Recorded with the import. Be specific (source, date, how the addresses were obtained). It also helps you answer a person who asks how you got their address.">
                {(id) => <textarea id={id} className="textarea" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Exported from Apollo on 2026-09-20 for AI consultancies in the EU" />}
              </Field>
              <fieldset className="col gap-12" style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="small" style={{ fontWeight: 650 }}>Have these people been emailed before?</legend>
                <label className="card card-pad checkbox"><input type="radio" name="att" checked={attest === "unknown"} onChange={() => setAttest("unknown")} /><span><b>I'm not sure / some may have been</b> (recommended)<br /><span className="small sec">Contacts are imported with history “unknown” and blocked from sending until you import your sent log or confirm they are new.</span></span></label>
                <label className="card card-pad checkbox"><input type="radio" name="att" checked={attest === "attested_unsent"} onChange={() => setAttest("attested_unsent")} /><span><b>I confirm none of them has ever been emailed by me or any tool</b><br /><span className="small sec">Recorded in the audit trail with your name. If this is wrong, people could receive a second cold email. Requires a description of the list's origin above.</span></span></label>
              </fieldset>
            </>
          ) : kind === "history" ? (
            <Banner tone="info" title="What happens to a sent-history import">Everyone in it is marked “previously contacted” and cannot receive an automatic first email. Rows with a bounce or unsubscribe status are added to the suppression list.</Banner>
          ) : (
            <Banner tone="info" title="What happens to a do-not-contact import">Every address (or domain) is added to the suppression list. This cannot re-enable anyone.</Banner>
          )}
          <p className="small sec">Ready to import <b>{fmtNum(prev.total_rows)}</b> rows as <b>{KINDS.find((k) => k.id === kind)?.title.toLowerCase()}</b>. Importing never sends email.</p>
        </div>
      )}
      {step === 4 && started && <Progress4 started={started} />}
    </Modal>
  );
}

function Progress4({ started }: { started: { batch_id: number; job_id: string } }) {
  const job = useQuery({ queryKey: ["job", started.job_id], queryFn: () => get(`/api/jobs/${started.job_id}`), refetchInterval: (q) => (["completed", "failed", "partial", "cancelled"].includes(q.state.data?.status) ? false : 1000) });
  const batch = useQuery({ queryKey: ["import", started.batch_id], queryFn: () => get(`/api/imports/${started.batch_id}`), refetchInterval: (q) => (q.state.data?.status === "completed" || q.state.data?.status === "failed" ? false : 1500) });
  const j = job.data, b = batch.data;
  const finished = j && ["completed", "failed", "partial", "cancelled"].includes(j.status);
  return (
    <div className="col gap-16" aria-live="polite">
      <div className="row between"><b>{j?.title ?? "Import"}</b>{j && <StatusBadge status={j.status} />}</div>
      <Progress value={j?.progress_done ?? 0} max={j?.progress_total || b?.total_rows || 1} />
      <div className="small sec num">{fmtNum(j?.progress_done)} of {fmtNum(j?.progress_total || b?.total_rows)} rows {j?.duration_seconds != null && `· ${fmtDuration(j.duration_seconds)}`}</div>
      {finished && j.status === "completed" && b && (
        <Banner tone="ok" title="Import finished">
          {fmtNum(b.inserted)} added · {fmtNum(b.duplicate_in_file + b.duplicate_existing)} duplicates skipped · {fmtNum(b.invalid)} invalid rows{b.updated ? ` · ${fmtNum(b.updated)} updated` : ""}. No email was sent.
        </Banner>)}
      {finished && j.status !== "completed" && <Banner tone="bad" title={`Import ${j.status}`}>{j.error_message}{j.suggested_action && <div className="mt-8">{j.suggested_action}</div>}</Banner>}
      {b && b.issues.length > 0 && finished && (
        <details><summary className="small">Rows that were not imported ({b.issues.length}{b.issues.length === 200 ? "+" : ""})</summary>
          <ul className="list small">{b.issues.slice(0, 30).map((i: any, k: number) => <li key={k}>Row {i.row}: {label(i.issue)} <span className="muted">{i.raw?.email ? `(${i.raw.email})` : ""}</span></li>)}</ul></details>)}
    </div>
  );
}
