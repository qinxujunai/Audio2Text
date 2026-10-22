import React, { Component, type ErrorInfo, type ReactNode } from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./styles.css";

const STALE_ASSET_RECOVERY_KEY = "wanxiang:stale-asset-recovery";

function recoverFromStaleAsset(error: unknown) {
  const message =
    typeof error === "string"
      ? error
      : error instanceof Error
        ? error.message
        : String(error || "");
  if (
    !/dynamically imported module|failed to fetch.*module|loading chunk|import\(\)/i.test(
      message,
    )
  ) {
    return;
  }

  try {
    if (window.sessionStorage.getItem(STALE_ASSET_RECOVERY_KEY) === "1") return;
    window.sessionStorage.setItem(STALE_ASSET_RECOVERY_KEY, "1");
  } catch {
    // A locked-down browser may not expose sessionStorage; the current page
    // should still remain usable rather than fail during error recovery.
  }

  const refreshedUrl = new URL(window.location.href);
  refreshedUrl.searchParams.set("ui_refresh", String(Date.now()));
  window.location.replace(refreshedUrl.toString());
}

type AppErrorBoundaryState = { hasError: boolean };

class AppErrorBoundary extends Component<
  { children: ReactNode },
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Keep the user-facing surface calm. Detailed diagnostics remain in the
    // local runtime logs rather than leaking into the product UI.
  }

  private reloadPage = () => {
    const refreshedUrl = new URL(window.location.href);
    refreshedUrl.searchParams.set("ui_refresh", String(Date.now()));
    window.location.replace(refreshedUrl.toString());
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <main className="processing-stage is-attention" role="alert">
        <section className="processing-shell">
          <div className="processing-copy">
            <span className="eyebrow">页面需要恢复</span>
            <h2>正在恢复万象成文</h2>
            <p>刚才的页面状态没有保留下来，重新加载后即可继续使用。</p>
          </div>
          <div className="processing-actions">
            <button className="retry-button" onClick={this.reloadPage} type="button">
              重新加载
            </button>
          </div>
        </section>
      </main>
    );
  }
}

window.addEventListener("error", (event) => {
  recoverFromStaleAsset(event.error || event.message);
});
window.addEventListener("unhandledrejection", (event) => {
  recoverFromStaleAsset(event.reason);
});
window.setTimeout(() => {
  try {
    window.sessionStorage.removeItem(STALE_ASSET_RECOVERY_KEY);
  } catch {
    // Ignore storage restrictions.
  }
}, 8000);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </React.StrictMode>,
);
