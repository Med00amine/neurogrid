import { ReactNode, useRef, useState } from "react";
import { compact, fmtNum } from "../format";
import { Basis } from "./ui";

/* Mark specs (dataviz skill): bars ≤ 24px thick with a 4px rounded data end and a square baseline; lines 2px;
   end dots ≥ 8px with a 2px surface ring; hairline solid grid; labels in text tokens, never the series colour. */

export function StatTile({ label, value, basis, sub, hero, onClick, tone }: {
  label: string; value: ReactNode; basis?: string; sub?: ReactNode; hero?: boolean; onClick?: () => void; tone?: "bad" | "warn";
}) {
  const Tag: any = onClick ? "button" : "div";
  return (
    <Tag className="card tile" onClick={onClick} style={{ textAlign: "left", font: "inherit", color: "inherit", cursor: onClick ? "pointer" : undefined }}>
      <span className="label">{label}{basis && basis !== "database" && <Basis basis={basis} />}</span>
      <span className="value" style={{ fontSize: hero ? 40 : undefined, color: tone === "bad" ? "var(--bad-fg)" : undefined }}>{value}</span>
      {sub && <span className="sub">{sub}</span>}
    </Tag>
  );
}

function niceMax(v: number): number {
  if (v <= 4) return 4;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 4 ? 4 : n <= 5 ? 5 : 10) * mag;
}

function useTip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; content: ReactNode } | null>(null);
  const show = (e: { clientX: number; clientY: number }, content: ReactNode) => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, content });
  };
  return { ref, tip, show, hide: () => setTip(null) };
}

export type BarRow = { name: string; value: number; hint?: string };

export function HBars({ rows, total, emptyText = "Nothing to show yet." }: { rows: BarRow[]; total?: number; emptyText?: string }) {
  const { ref, tip, show, hide } = useTip();
  const max = Math.max(1, ...rows.map((r) => r.value));
  const sum = total ?? rows.reduce((a, r) => a + r.value, 0);
  if (rows.every((r) => r.value === 0)) return <p className="muted small">{emptyText}</p>;
  return (
    <div className="chart-wrap" ref={ref}>
      <div className="hbars" role="list">
        {rows.map((r) => (
          <div key={r.name} className="hbar" role="listitem" onMouseMove={(e) => show(e, <><b>{r.name}</b>: {fmtNum(r.value)}{sum ? ` (${Math.round((r.value / sum) * 100)}%)` : ""}{r.hint && <div className="muted">{r.hint}</div>}</>)} onMouseLeave={hide}>
            <span className="name" title={r.name}>{r.name}</span>
            <span className="track"><span className="fill" style={{ width: r.value ? `${Math.max(1, (r.value / max) * 100)}%` : 0 }} /></span>
            <span className="val">{compact(r.value)}</span>
          </div>
        ))}
      </div>
      {tip && <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.content}</div>}
    </div>
  );
}

export function Columns({ data, height = 200, unit = "contacts" }: { data: { label: string; value: number }[]; height?: number; unit?: string }) {
  const { ref, tip, show, hide } = useTip();
  const [table, setTable] = useState(false);
  const W = 560, H = height, pad = { l: 38, r: 8, t: 10, b: 26 };
  const max = niceMax(Math.max(0, ...data.map((d) => d.value)));
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const band = iw / Math.max(1, data.length);
  const bw = Math.min(24, band - 2);
  const y = (v: number) => pad.t + ih - (v / max) * ih;
  const ticks = [0, max / 2, max];
  if (table) return <DataTable head={["Range", unit]} rows={data.map((d) => [d.label, fmtNum(d.value)])} onBack={() => setTable(false)} />;
  return (
    <div className="chart-wrap" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Column chart of ${unit} by range`}>
        <g className="axis">
          {ticks.map((t) => (<g key={t}><line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} strokeWidth="1" /><text x={pad.l - 6} y={y(t) + 4} textAnchor="end">{compact(Math.round(t))}</text></g>))}
          {data.map((d, i) => (<text key={d.label} x={pad.l + band * i + band / 2} y={H - 8} textAnchor="middle">{d.label}</text>))}
        </g>
        {data.map((d, i) => {
          const x = pad.l + band * i + (band - bw) / 2;
          const h = Math.max(d.value ? 2 : 0, ih - (y(d.value) - pad.t));
          const top = pad.t + ih - h, r = Math.min(4, h);
          return (
            <g key={d.label} onMouseMove={(e) => show(e, <><b>{d.label}</b>: {fmtNum(d.value)} {unit}</>)} onMouseLeave={hide}>
              <rect x={pad.l + band * i} y={pad.t} width={band} height={ih} fill="transparent" />
              {d.value > 0 && <path d={`M${x},${top + h} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${top + h} Z`} fill="var(--series-1)" />}
            </g>
          );
        })}
      </svg>
      {tip && <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.content}</div>}
      <button className="btn sm ghost" onClick={() => setTable(true)}>View as table</button>
    </div>
  );
}

export type Series = { name: string; color: string; values: number[] };

export function LineChart({ labels, series, height = 220, unit = "" }: { labels: string[]; series: Series[]; height?: number; unit?: string }) {
  const { ref, tip, show, hide } = useTip();
  const [idx, setIdx] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const W = 640, H = height, pad = { l: 38, r: 16, t: 12, b: 26 };
  const all = series.flatMap((s) => s.values);
  const max = niceMax(Math.max(0, ...all));
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const n = labels.length;
  const x = (i: number) => pad.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v: number) => pad.t + ih - (v / max) * ih;
  if (table) return <DataTable head={["Date", ...series.map((s) => s.name)]} rows={labels.map((l, i) => [l, ...series.map((s) => fmtNum(s.values[i]))])} onBack={() => setTable(false)} />;
  const move = (e: React.MouseEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    const rel = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((rel - pad.l) / iw) * (n - 1))));
    setIdx(i);
    show(e, <><b>{labels[i]}</b>{series.map((s) => <div key={s.name}><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: s.color, marginRight: 6 }} />{s.name}: {fmtNum(s.values[i])}{unit}</div>)}</>);
  };
  const everyN = Math.max(1, Math.ceil(n / 7));
  return (
    <div className="chart-wrap" ref={ref}>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Line chart over time" onMouseMove={move} onMouseLeave={() => { hide(); setIdx(null); }}>
        <g className="axis">
          {[0, max / 2, max].map((t) => (<g key={t}><line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} strokeWidth="1" /><text x={pad.l - 6} y={y(t) + 4} textAnchor="end">{compact(Math.round(t))}</text></g>))}
          {labels.map((l, i) => (i % everyN === 0 ? <text key={i} x={x(i)} y={H - 8} textAnchor="middle">{l}</text> : null))}
        </g>
        {idx !== null && <line x1={x(idx)} x2={x(idx)} y1={pad.t} y2={pad.t + ih} stroke="var(--border-strong)" strokeWidth="1" />}
        {series.map((s) => (
          <g key={s.name}>
            <polyline fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
            {s.values.length > 0 && <circle cx={x(n - 1)} cy={y(s.values[n - 1])} r="4" fill={s.color} stroke="var(--surface-1)" strokeWidth="2" />}
            {idx !== null && <circle cx={x(idx)} cy={y(s.values[idx])} r="4" fill={s.color} stroke="var(--surface-1)" strokeWidth="2" />}
          </g>
        ))}
      </svg>
      {tip && <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.content}</div>}
      <div className="row between wrap">
        <div className="legend">{series.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}</div>
        <button className="btn sm ghost" onClick={() => setTable(true)}>View as table</button>
      </div>
    </div>
  );
}

function DataTable({ head, rows, onBack }: { head: string[]; rows: (string | number)[][]; onBack: () => void }) {
  return (
    <div>
      <div className="table-wrap" style={{ maxHeight: 260, overflowY: "auto" }}>
        <table className="t"><thead><tr>{head.map((h, i) => <th key={h} className={i ? "r" : ""}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={j ? "r num" : ""}>{c}</td>)}</tr>)}</tbody></table>
      </div>
      <button className="btn sm ghost" onClick={onBack}>View as chart</button>
    </div>
  );
}

export function Meter({ value, max, label, format = (n: number) => String(n) }: { value: number; max: number; label: string; format?: (n: number) => string }) {
  const ratio = max > 0 ? Math.min(1, value / max) : 0;
  return (
    <div className="col gap-4">
      <div className="row between small"><span className="sec">{label}</span><span className="num"><b>{format(value)}</b> <span className="muted">of {format(max)}</span></span></div>
      <div className="meter" role="meter" aria-valuenow={value} aria-valuemax={max} aria-label={label}><i style={{ width: `${ratio * 100}%`, background: ratio >= 0.9 ? "var(--bad-icon)" : ratio >= 0.7 ? "var(--warn-icon)" : "var(--series-1)" }} /></div>
    </div>
  );
}
