import { FolderOpen, LogIn, RotateCcw } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

type ProcessingPhase = "bootstrapping" | "processing" | "attention";

type ProcessingStageProps = {
  phase: ProcessingPhase;
  eyebrow: string;
  title: string;
  description: string;
  stageLabel?: string;
  progressPercent?: number;
  progressDetail?: string;
  onReset?: () => void;
  onRetry?: () => void;
  onRefreshSession?: () => void;
  onOpenPlatformSession?: () => void;
  platformActionLabel?: string;
  onDiagnostics?: () => void;
};

export function safeProgressDetail(raw: string | undefined): string {
  if (!raw) return "";
  const text = raw.trim();
  if (/^[a-zA-Z_]+Error/i.test(text)) return "";
  if (
    /Traceback|Exception|FileNotFound|\.py"|https?:\/\/|HTTP\s+Error|KeyError|yt[-_ ]?dlp|ERROR\s*:/i.test(text) ||
    /失败|出错|异常|错误|报错|未能|无法/i.test(text)
  ) {
    return "";
  }
  if (/^\s*[{[]/.test(text)) return "";
  return text.length > 80 ? text.slice(0, 78) + "…" : text;
}

/** "1:05" or "1:02:05": elapsed time that keeps a long wait visibly alive. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

function useProcessingClock(active: boolean, progress: number) {
  const startedAt = useRef<number>(Date.now());
  const progressAt = useRef<{ value: number; at: number }>({ value: progress, at: Date.now() });
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!active) return;
    startedAt.current = Date.now();
    progressAt.current = { value: progress, at: Date.now() };
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
    // A new processing run restarts the clock; progress changes are tracked below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  if (progress !== progressAt.current.value) {
    progressAt.current = { value: progress, at: Date.now() };
  }
  return {
    elapsed: (now - startedAt.current) / 1000,
    stalled: (now - progressAt.current.at) / 1000,
  };
}

export function ProcessingStage({
  phase,
  eyebrow,
  title,
  description,
  stageLabel,
  progressPercent,
  progressDetail,
  onReset,
  onRetry,
  onRefreshSession,
  onOpenPlatformSession,
  platformActionLabel,
  onDiagnostics,
}: ProcessingStageProps) {
  const working = phase === "bootstrapping" || phase === "processing";
  // While the app is still starting there is no real progress yet: show an indeterminate bar, not a made-up number.
  const indeterminate = phase === "bootstrapping" || !progressPercent;
  const normalizedProgress = Math.round(Math.min(100, Math.max(progressPercent || 0, 0)));
  const safeDetail = safeProgressDetail(progressDetail);
  const { elapsed, stalled } = useProcessingClock(working, normalizedProgress);
  const reassurance = working && stalled > 30 ? "进度暂未更新，长内容可能需要更长时间" : "";

  return (
    <section
      aria-busy={phase === "bootstrapping" || phase === "processing"}
      className={phase === "attention" ? "processing-stage is-attention" : "processing-stage"}
    >
      <div className="processing-shell">
        {phase === "bootstrapping" || phase === "processing" ? (
          <div className="processing-indicator" aria-hidden="true">
            <span className="processing-indicator-ring" />
            <span className="processing-indicator-core" />
          </div>
        ) : null}

        <div className="processing-copy" role="status" aria-live="polite" aria-atomic="true">
          {phase === "attention" ? (
            <span className="eyebrow">{eyebrow}</span>
          ) : null}
          <h2>{title}</h2>
          <p>{description}</p>
        </div>

        {working ? (
          <div
            className="processing-meter"
            aria-label={indeterminate ? "正在准备" : `当前进度 ${normalizedProgress}%`}
          >
            <div className={indeterminate ? "processing-meter-track is-indeterminate" : "processing-meter-track"}>
              {indeterminate ? (
                <span className="processing-meter-sweep" />
              ) : (
                <motion.span
                  className="processing-meter-fill"
                  initial={{ width: 0 }}
                  animate={{ width: `${normalizedProgress}%` }}
                  transition={{ duration: 0.45, ease: "easeOut" }}
                />
              )}
            </div>
            <div className="processing-meter-meta">
              <span>{reassurance || safeDetail || stageLabel || ""}</span>
              {indeterminate ? null : <strong>{normalizedProgress}%</strong>}
            </div>
            <div className="processing-meter-time" aria-live="off">
              <span>已用 {formatElapsed(elapsed)}</span>
            </div>
          </div>
        ) : null}

        {phase === "attention" ? (
          <div className="processing-actions">
            {onOpenPlatformSession ? (
              <button className="retry-button" onClick={onOpenPlatformSession} type="button">
                <LogIn size={16} aria-hidden="true" />
                {platformActionLabel || "打开平台完成验证"}
              </button>
            ) : null}
            {onRefreshSession ? (
              <button className="retry-button is-secondary" onClick={onRefreshSession} type="button">
                <RotateCcw size={16} aria-hidden="true" />
                完成后重新检查
              </button>
            ) : null}
            {onRetry ? (
              <button className="retry-button" onClick={onRetry} type="button">
                <RotateCcw size={16} aria-hidden="true" />
                重新处理
              </button>
            ) : null}
            {onReset ? (
              <button className="retry-button is-secondary" onClick={onReset} type="button">
                返回首页
              </button>
            ) : null}
            {onDiagnostics ? (
              <button className="retry-button is-secondary" onClick={onDiagnostics} type="button">
                <FolderOpen size={16} aria-hidden="true" />
                查看诊断
              </button>
            ) : null}
          </div>
        ) : null}

        {phase === "bootstrapping" && onReset ? (
          <button className="retry-button" onClick={onReset} type="button">
            取消，返回首页
          </button>
        ) : null}
      </div>
    </section>
  );
}
