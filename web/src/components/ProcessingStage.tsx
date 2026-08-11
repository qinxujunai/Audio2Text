import { LogIn, RotateCcw } from "lucide-react";
import { motion } from "motion/react";

type ProcessingPhase = "bootstrapping" | "processing" | "failed";

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
};

export function safeProgressDetail(raw: string | undefined): string {
  if (!raw) return "";
  const text = raw.trim();
  if (/^[a-zA-Z_]+Error/i.test(text)) return "";
  if (/Traceback|Exception|FileNotFound|\.py"/i.test(text)) return "";
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
}: ProcessingStageProps) {
  const normalizedProgress = Math.min(
    100,
    Math.max(phase === "bootstrapping" ? 18 : progressPercent || 0, 0),
  );
  const safeDetail = safeProgressDetail(progressDetail);

  return (
    <section
      className={
        phase === "failed" ? "processing-stage is-failed" : "processing-stage"
      }
    >
      <div className="processing-shell">
        {phase !== "failed" ? (
          <div className="processing-indicator" aria-hidden="true">
            <span className="processing-indicator-ring" />
            <span className="processing-indicator-core" />
          </div>
        ) : null}

        <div className="processing-copy">
          {phase === "failed" ? (
            <span className="eyebrow">{eyebrow}</span>
          ) : null}
          <h2>{title}</h2>
          <p>{description}</p>
        </div>

        {phase !== "failed" ? (
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

        {phase === "failed" ? (
          <div className="processing-actions">
            {onRefreshSession ? (
              <button className="retry-button" onClick={onRefreshSession} type="button">
                <LogIn size={16} aria-hidden="true" />
                打开平台完成验证
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
