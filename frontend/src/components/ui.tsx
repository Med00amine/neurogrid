import { createContext, ReactNode, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { label } from "../format";

/* ── icons (inline SVG, 24×24 viewBox, stroke currentColor) ───────────────────────────────────── */
const PATHS: Record<string, string> = {
  overview: "M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10",
  contacts: "M16 11a4 4 0 1 0-8 0M4 20c0-3.3 3.6-6 8-6s8 2.7 8 6",
  research: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.5-4.5",
  leads: "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z",
  studio: "M4 20l4-1 11-11a2.1 2.1 0 0 0-3-3L5 16l-1 4zM14 6l3 3",
  campaigns: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
  replies: "M4 4h16v12H8l-4 4zM8 9h8M8 12h5",
  pipeline: "M4 4h4v16H4zM10 4h4v10h-4zM16 4h4v6h-4z",
  jobs: "M12 7v5l3 2M21 12a9 9 0 1 1-3-6.7M21 4v5h-5",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12M18 6L6 18",
  alert: "M12 3l10 18H2zM12 10v5M12 18v.5",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5v.5",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  download: "M12 3v12M7 11l5 5 5-5M4 21h16",
  upload: "M12 21V9M7 13l5-5 5 5M4 3h16",
  refresh: "M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4",
  play: "M7 4l13 8-13 8z",
  pause: "M8 5v14M16 5v14",
  stop: "M6 6h12v12H6z",
  chevron: "M9 6l6 6-6 6",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  ban: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM5.6 5.6l12.8 12.8",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3",
  menu: "M4 6h16M4 12h16M4 18h16",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  plus: "M12 5v14M5 12h14",
  link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  mailopen: "M3 9l9 6 9-6M3 9l9-6 9 6v10H3z",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.info} />
    </svg>
  );
}

/* ── status badges: colour is never the only signal (icon + text label) ──────────────────────── */
type Tone = "ok" | "warn" | "bad" | "info" | "neutral" | "sim";
const TONE: Record<string, Tone> = {
  eligible: "ok", approved: "ok", completed: "ok", delivered: "ok", won: "ok", done: "ok", active: "ok", high: "ok", verified: "ok", interested: "ok", meeting_scheduled: "ok",
  review_required: "warn", needs_review: "warn", paused: "warn", pending: "warn", partial: "warn", medium: "warn", unreviewed: "neutral", queued: "warn", draft: "neutral",
  proposal_sent: "info", replied: "info", contacted: "info", submitted: "info", running: "info", submitting: "info", confirmed: "info", researched: "info", qualified: "info",
  draft_ready: "info", referral: "info", info_request: "info", dry_run: "sim", simulated: "sim", inference: "warn", fact: "neutral",
  excluded: "bad", suppressed: "bad", failed: "bad", rejected: "bad", bounced: "bad", unknown: "bad", do_not_contact: "bad", lost: "bad", auto_rejected: "bad",
  unsubscribe: "bad", not_interested: "bad", low: "bad", critical: "bad", cancelled: "neutral", superseded: "neutral", new: "neutral", uncertain: "neutral", auto_response: "neutral",
};
const TONE_ICON: Partial<Record<Tone, string>> = { ok: "check", warn: "alert", bad: "x", sim: "flask", info: "info" };

export function StatusBadge({ status, text, tone }: { status: string | null | undefined; text?: string; tone?: Tone }) {
  const s = status ?? "unknown";
  const t = tone ?? TONE[s] ?? "neutral";
  const ic = TONE_ICON[t];
  return (
    <span className={`badge ${t === "neutral" ? "" : t}`} data-status={s}>
      {ic && <Icon name={ic} size={12} />}
      {text ?? label(s)}
    </span>
  );
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: Tone; title?: string }) {
  return <span className={`badge ${tone === "neutral" ? "" : tone}`} title={title}>{children}</span>;
}

export function Basis({ basis }: { basis: string }) {
  const text = basis === "provider_confirmed" ? "mail-system confirmed" : basis;
  return <span className={`basis ${basis}`} title={BASIS_HELP[basis] ?? ""}>{text}</span>;
}
const BASIS_HELP: Record<string, string> = {
  database: "A count of rows in this app's database.",
  estimate: "Calculated from a price table or a heuristic. Check your provider's dashboard for real billing.",
  simulated: "Produced by a dry run. Nothing was actually sent.",
  provider_confirmed: "Reported by the mail system itself (a bounce or delivery receipt that was received).",
};

export function Banner({ tone = "info", title, children }: { tone?: "info" | "warn" | "bad" | "ok"; title?: string; children?: ReactNode }) {
  const ic = tone === "ok" ? "check" : tone === "info" ? "info" : "alert";
  return (
    <div className={`banner ${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <span style={{ marginTop: 2 }}><Icon name={ic} size={16} /></span>
      <div className="col gap-4">{title && <b>{title}</b>}<div>{children}</div></div>
    </div>
  );
}

export function Spinner() { return <span className="spinner" role="status" aria-label="Loading" />; }

export function Progress({ value, max }: { value: number; max: number }) {
  const w = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return <div className="progress" role="progressbar" aria-valuenow={value} aria-valuemax={max}><i style={{ width: `${w}%` }} /></div>;
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children && <p style={{ maxWidth: 52 + "ch" }}>{children}</p>}{action}</div>;
}

export function Card({ title, subtitle, actions, children, pad = true }: { title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; pad?: boolean }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          <div>{title && <h2>{title}</h2>}{subtitle && <p>{subtitle}</p>}</div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={pad ? "card-pad" : ""}>{children}</div>
    </section>
  );
}

export function PageHead({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div><h1>{title}</h1>{children && <p>{children}</p>}</div>
      {actions && <div className="row wrap">{actions}</div>}
    </header>
  );
}

export function Field({ label: lbl, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{lbl}</label>
      {children(id)}
      {hint && <span className="hint">{hint}</span>}
      {error && <span className="err">{error}</span>}
    </div>
  );
}

export function Switch({ checked, onChange, label: lbl, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className="row" style={{ cursor: disabled ? "not-allowed" : "pointer" }}>
      <span className="switch"><input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /><span /></span>
      <span>{lbl}</span>
    </label>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; count?: number }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={`tab ${value === t.id ? "active" : ""}`} onClick={() => onChange(t.id)}>
          {t.label}{t.count !== undefined ? ` (${t.count})` : ""}
        </button>
      ))}
    </div>
  );
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  return (
    <div className="pager">
      <span className="small sec num">{from.toLocaleString()}–{Math.min(page * pageSize, total).toLocaleString()} of {total.toLocaleString()}</span>
      <div className="row">
        <button className="btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <span className="small sec num">Page {page} / {pages.toLocaleString()}</span>
        <button className="btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}

/* ── overlays ─────────────────────────────────────────────────────────────────────────────────── */
function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEscape(onClose);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <div className="modal-head"><h2>{title}</h2></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function SlideOver({ title, subtitle, onClose, children, wide }: { title: ReactNode; subtitle?: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEscape(onClose);
  return (
    <>
      <div className="overlay" style={{ background: "rgba(0,0,0,.25)", zIndex: 54 }} onMouseDown={onClose} />
      <aside className={`slide ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : "Details"}>
        <div className="slide-head">
          <div className="grow"><h2>{title}</h2>{subtitle && <div className="small sec mt-8">{subtitle}</div>}</div>
          <button className="btn icon ghost" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <div className="slide-body">{children}</div>
      </aside>
    </>
  );
}

export function Confirm({ title, children, confirmLabel = "Confirm", danger, phrase, busy, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel?: string; danger?: boolean; phrase?: string; busy?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const ok = !phrase || typed.trim() === phrase;
  return (
    <Modal title={title} onClose={onCancel}
      footer={<><button className="btn" onClick={onCancel}>Cancel</button><button className={`btn ${danger ? "danger" : "primary"}`} disabled={!ok || busy} onClick={onConfirm}>{busy ? "Working…" : confirmLabel}</button></>}>
      <div className="col gap-12">
        {children}
        {phrase && (
          <Field label={`Type exactly: ${phrase}`}>
            {(id) => <input id={id} className="input mono" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} aria-label="Confirmation phrase" />}
          </Field>
        )}
      </div>
    </Modal>
  );
}

/* ── toasts ───────────────────────────────────────────────────────────────────────────────────── */
type ToastItem = { id: number; tone: "ok" | "bad" | "info"; text: string };
const ToastCtx = createContext<{ push: (tone: ToastItem["tone"], text: string) => void }>({ push: () => {} });
export const useToast = () => {
  const { push } = useContext(ToastCtx);
  return { ok: (t: string) => push("ok", t), error: (t: string) => push("bad", t), info: (t: string) => push("info", t) };
};
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((tone: ToastItem["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, tone, text }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), tone === "bad" ? 9000 : 4500);
  }, []);
  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "bad" ? "alert" : "status"}>
            <Icon name={t.tone === "ok" ? "check" : t.tone === "bad" ? "alert" : "info"} size={16} />
            <div className="grow">{t.text}</div>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ── small hooks ──────────────────────────────────────────────────────────────────────────────── */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

export function QueryState({ q, children, empty }: { q: { isLoading: boolean; isError: boolean; error: unknown; data: unknown }; children: ReactNode; empty?: ReactNode }) {
  if (q.isError) return <div className="card-pad"><Banner tone="bad" title="Could not load this">{(q.error as Error)?.message}</Banner></div>;
  // "no data yet" counts as loading: a placeholder-data query can briefly be 'success' with nothing to show
  if (q.isLoading || q.data === undefined) return <div className="empty"><Spinner /></div>;
  if (!q.data && empty) return <>{empty}</>;
  return <>{children}</>;
}
