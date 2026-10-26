import { FormEvent, useState } from "react";
import { useAuth } from "../auth";
import { Banner, Field, Icon } from "../components/ui";

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await login(email, password); } catch (err: any) { setError(err.message || "Sign-in failed"); } finally { setBusy(false); }
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <form className="card card-pad col gap-16" style={{ width: "min(400px, 100%)" }} onSubmit={submit} aria-label="Sign in">
        <div className="brand" style={{ padding: 0 }}>
          <span className="logo"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M4 17l5-6 4 4 7-9" /></svg></span>
          Client Acquisition Studio
        </div>
        <div><h1>Sign in</h1><p className="sec mt-8">Your workspace holds personal data about other people, so it is private.</p></div>
        {error && <Banner tone="bad">{error}</Banner>}
        <Field label="Email">{(id) => <input id={id} className="input" type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label="Password">{(id) => <input id={id} className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}</Field>
        <button className="btn primary" disabled={busy || !email || !password}><Icon name="lock" size={16} />{busy ? "Signing in…" : "Sign in"}</button>
        <p className="xs muted">Forgot the password? Run <code>python -m app.cli set-password --email you@example.com</code> on the server.</p>
      </form>
    </div>
  );
}
