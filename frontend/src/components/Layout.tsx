import { ReactNode, useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api";
import { useAuth } from "../auth";
import ContactDrawer from "./ContactDrawer";
import { Badge, Icon } from "./ui";

const NAV: { to: string; label: string; icon: string; attention?: string }[] = [
  { to: "/overview", label: "Overview", icon: "overview" },
  { to: "/contacts", label: "Contacts", icon: "contacts" },
  { to: "/research", label: "Company research", icon: "research" },
  { to: "/leads", label: "Leads & scoring", icon: "leads" },
  { to: "/studio", label: "Email studio", icon: "studio" },
  { to: "/campaigns", label: "Campaigns", icon: "campaigns", attention: "campaign" },
  { to: "/replies", label: "Replies", icon: "replies", attention: "replies_unreviewed" },
  { to: "/pipeline", label: "Sales pipeline", icon: "pipeline", attention: "tasks_due" },
  { to: "/jobs", label: "Job monitor", icon: "jobs", attention: "jobs_failed" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);

  const status = useQuery({ queryKey: ["sending-status"], queryFn: () => get("/api/sending/status"), refetchInterval: 30_000 });
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => get("/api/overview"), refetchInterval: 15_000 });
  const attention: Record<string, number> = {};
  for (const a of ov.data?.attention ?? []) attention[a.kind] = a.count;
  attention.campaign = (attention.unknown_outcome ?? 0) + (attention.campaign_paused ?? 0);

  const live = !!status.data?.live_mode_enabled && !!status.data?.live_enabled_in_env && status.data?.provider === "smtp";
  const [theme, setTheme] = useState<string>(() => document.documentElement.dataset.theme || "");
  function toggleTheme() {
    const dark = theme === "dark" || (!theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try { localStorage.setItem("theme", next); } catch { /* ignore */ }
  }

  return (
    <div className="shell">
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Sidebar">
        <div className="brand">
          <span className="logo"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M4 17l5-6 4 4 7-9" /></svg></span>
          <span>Acquisition<br /><span className="muted" style={{ fontWeight: 500, fontSize: 12 }}>Studio</span></span>
        </div>
        <nav className="nav col gap-4" aria-label="Main navigation">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon name={n.icon} />
              <span>{n.label}</span>
              {n.attention && attention[n.attention] ? <span className="count" title="Needs attention">{attention[n.attention]}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot col gap-4">
          <div className="nav-sep" />
          <div className="small sec ell" title={user?.email}>{user?.email}</div>
          <div className="xs muted">{user?.role === "admin" ? "Admin" : "Read-only viewer"}</div>
          <div className="row mt-8">
            <button className="btn sm" onClick={toggleTheme} aria-label="Toggle light and dark theme"><Icon name={theme === "dark" ? "sun" : "moon"} size={14} /></button>
            <button className="btn sm grow" onClick={logout}><Icon name="logout" size={14} />Sign out</button>
          </div>
        </div>
      </aside>
      <div className="main">
        <div className="topbar">
          <button className="btn icon menu-btn" onClick={() => setOpen(true)} aria-label="Open menu"><Icon name="menu" /></button>
          <div className="grow" />
          {status.data && (live
            ? <Badge tone="warn" title="Real emails can be sent when you confirm a live campaign."><Icon name="alert" size={12} />Live sending is ENABLED</Badge>
            : <Badge tone="sim" title="Live sending is switched off. Campaigns run as rehearsals; nothing can leave this computer."><Icon name="shield" size={12} />Safe mode: dry-run only</Badge>)}
        </div>
        <main className="content" id="main">{children}</main>
      </div>
      <ContactDrawer />
    </div>
  );
}
