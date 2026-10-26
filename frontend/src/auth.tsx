import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { get, post, setCsrf } from "./api";

export type User = { id: number; email: string; role: "admin" | "viewer" };
type AuthState = { user: User | null; loading: boolean; login: (email: string, password: string) => Promise<void>; logout: () => Promise<void> };

const Ctx = createContext<AuthState>({ user: null, loading: true, login: async () => {}, logout: async () => {} });
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  useEffect(() => {
    get<{ user: User; csrf_token: string }>("/api/auth/me")
      .then((r) => { setCsrf(r.csrf_token); setUser(r.user); })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
    const expired = () => { setCsrf(null); setUser(null); qc.clear(); };
    window.addEventListener("auth:expired", expired);
    return () => window.removeEventListener("auth:expired", expired);
  }, [qc]);

  const login = useCallback(async (email: string, password: string) => {
    const r = await post<{ user: User; csrf_token: string }>("/api/auth/login", { email, password });
    setCsrf(r.csrf_token);
    setUser(r.user);
  }, []);

  const logout = useCallback(async () => {
    try { await post("/api/auth/logout"); } catch { /* the session may already be gone */ }
    setCsrf(null);
    setUser(null);
    qc.clear();
  }, [qc]);

  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>;
}
