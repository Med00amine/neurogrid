import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import Login from "./pages/Login";

const Overview = lazy(() => import("./pages/Overview"));
const Contacts = lazy(() => import("./pages/Contacts"));
const Research = lazy(() => import("./pages/Research"));
const Leads = lazy(() => import("./pages/Leads"));
const Studio = lazy(() => import("./pages/Studio"));
const Campaigns = lazy(() => import("./pages/Campaigns"));
const Replies = lazy(() => import("./pages/Replies"));
const Pipeline = lazy(() => import("./pages/Pipeline"));
const Jobs = lazy(() => import("./pages/Jobs"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="empty" style={{ minHeight: "100vh" }}><Spinner /></div>;
  if (!user) return <Login />;
  return (
    <Layout>
      <ErrorBoundary key={loc.pathname}>
      <Suspense fallback={<div className="empty"><Spinner /></div>}>
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/contacts" element={<Contacts />} />
          <Route path="/research" element={<Research />} />
          <Route path="/leads" element={<Leads />} />
          <Route path="/studio" element={<Studio />} />
          <Route path="/campaigns" element={<Campaigns />} />
          <Route path="/replies" element={<Replies />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </Suspense>
      </ErrorBoundary>
    </Layout>
  );
}
