import { Activity } from "lucide-react";

type HeaderStatus = "idle" | "processing" | "done" | "failed";

type HeaderProps = {
  status: HeaderStatus;
  featureName: string;
  onReset: () => void;
};

export function Header({ status, featureName, onReset }: HeaderProps) {
  return (
    <header className="app-header">
      <button className="brand-lockup" onClick={onReset} type="button">
        <span className="brand-mark" aria-hidden="true">
          <span />
        </span>
        <span className="brand-copy">
          <strong>Praxis AI</strong>
          <span>/</span>
          <small>{featureName}</small>
        </span>
      </button>

      <div className="header-meta">
        {status === "processing" ? (
          <span className="status-chip">
            <Activity className="icon-spin" size={14} />
            {"\u5904\u7406\u4E2D"}
          </span>
        ) : null}
      </div>
    </header>
  );
}
