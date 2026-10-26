import { Component, ReactNode } from "react";

/** A bug in one page must never blank the whole workspace. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error("UI error:", error); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card card-pad col gap-12" role="alert" style={{ maxWidth: 640, margin: "40px auto" }}>
        <h2>Something went wrong on this page</h2>
        <p className="sec">Your data is safe: nothing was changed by this error. You can go back or reload.</p>
        <pre className="mail small" style={{ maxHeight: 160, overflow: "auto" }}>{this.state.error.message}</pre>
        <div className="row"><button className="btn primary" onClick={() => this.setState({ error: null })}>Try again</button><button className="btn" onClick={() => window.location.assign("/overview")}>Go to the overview</button></div>
      </div>
    );
  }
}
