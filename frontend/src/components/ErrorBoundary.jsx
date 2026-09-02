import { Component } from "react";

export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Unhandled UI error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: "var(--space-6)", maxWidth: 640 }}>
          <h2 style={{ color: "var(--color-destructive)" }}>Something went wrong</h2>
          <p className="status">
            The page hit an unexpected error and stopped rendering. This is usually caused by an unusually large or
            unexpected result set.
          </p>
          <pre
            className="mono"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius)",
              padding: "var(--space-4)",
              fontSize: 11,
              overflow: "auto",
              color: "var(--color-muted-foreground)",
            }}
          >
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <button type="button" className="btn" onClick={() => window.location.reload()}>
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
