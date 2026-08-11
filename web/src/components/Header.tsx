import { Activity, Download } from "lucide-react";

type HeaderStatus = "idle" | "processing" | "done" | "failed";

type HeaderProps = {
  status: HeaderStatus;
  featureName: string;
  showWindowsDownload: boolean;
  onReset: () => void;
};

const WINDOWS_DOWNLOAD_URL =
  "https://github.com/qinxujunai/Audio2Text/releases/latest/download/Wanxiang-Windows-x64-Setup.exe";

export function Header({
  status,
  featureName,
  showWindowsDownload,
  onReset,
}: HeaderProps) {
  return (
    <header className="app-header">
      <button className="brand-lockup" onClick={onReset} type="button">
        <span className="brand-mark" aria-hidden="true">
          <img src="/brand-mark.svg" alt="" />
        </span>
        <span className="brand-copy">
          <strong>Praxis AI</strong>
          <span>/</span>
          <small>{featureName}</small>
        </span>
      </button>

      <div className="header-meta">
        {showWindowsDownload ? (
          <a className="header-download" href={WINDOWS_DOWNLOAD_URL}>
            <Download size={16} aria-hidden="true" />
            <span>Windows 版</span>
          </a>
        ) : null}
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
