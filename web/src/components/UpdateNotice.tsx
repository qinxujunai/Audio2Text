import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { getAppUpdate, type AppUpdateStatus } from "../api";
import { openUpdatePage } from "../desktop";

const DISMISSED_KEY = "wanxiang.update.dismissed";

function readDismissed() {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) || "";
  } catch {
    return "";
  }
}

/** A quiet line on the home page when a newer release is out; one dismissal per version. */
export function UpdateNotice() {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [dismissed, setDismissed] = useState(readDismissed);

  useEffect(() => {
    let active = true;
    getAppUpdate()
      .then((value) => {
        if (active) setStatus(value);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  if (!status?.update_available || !status.latest || !status.url || dismissed === status.latest) {
    return null;
  }

  const latest = status.latest;
  const url = status.url;
  return (
    <div className="update-notice" role="status">
      <span>有新版本 {latest}</span>
      <button className="update-notice-link" type="button" onClick={() => void openUpdatePage(url).catch(() => undefined)}>
        查看更新
      </button>
      <button
        aria-label="不再提醒这个版本"
        className="update-notice-close"
        type="button"
        onClick={() => {
          try {
            window.localStorage.setItem(DISMISSED_KEY, latest);
          } catch {
            // Without storage the notice simply comes back next launch.
          }
          setDismissed(latest);
        }}
      >
        <X size={14} />
      </button>
    </div>
  );
}
