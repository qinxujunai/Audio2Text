import { FolderOpen, LogIn, RotateCcw } from "lucide-react";
import { motion } from "motion/react";

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
  const normalizedProgress = Math.min(
    100,
    Math.max(phase === "bootstrapping" ? 18 : progressPercent || 0, 0),
  );
  const safeDetail = safeProgressDetail(progressDetail);

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

        {phase === "bootstrapping" || phase === "processing" ? (
          <div
            className="processing-meter"
            aria-label={`当前进度 ${normalizedProgress}%`}
          >
            <div className="processing-meter-track">
              <motion.span
                className="processing-meter-fill"
                initial={{ width: 0 }}
                animate={{ width: `${normalizedProgress}%` }}
                transition={{ duration: 0.45, ease: "easeOut" }}
              />
            </div>
            <div className="processing-meter-meta">
              <span>{safeDetail || stageLabel || ""}</span>
              <strong>{normalizedProgress}%</strong>
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
