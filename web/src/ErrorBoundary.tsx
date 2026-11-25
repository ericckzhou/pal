import { Component, ReactNode } from "react";

type State = { error: Error | null };

// Shows the error instead of a blank page if any component crashes while rendering.
export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("pal UI crashed:", error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="login">
        <h1>Something broke</h1>
        <pre className="error">{String(this.state.error.stack ?? this.state.error)}</pre>
        <button className="primary" onClick={() => location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
