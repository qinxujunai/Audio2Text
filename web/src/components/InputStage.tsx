import { ArrowRight, Clipboard, FileUp, History, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { CaptureListItem } from "../types";

type InputStageProps = {
  input: string;
  subtitle: string;
  selectedFile: File | null;
  submitting: boolean;
  supportedExtensions: string[];
  maxUploadSizeMb: number;
  freeDurationMinutes: number;
  captures: CaptureListItem[];
  currentCaptureId?: string | null;
  onInputChange: (value: string) => void;
  onSubmitText: () => void;
  onPaste: () => void;
  onSelectFile: (file: File | null) => void;
  onSubmitFile: () => void;
  onOpenCapture: (captureId: string) => void;
  onClearHistory: () => void;
  onDeleteCapture: (item: CaptureListItem) => void;
};

const COLLAPSED_HISTORY_COUNT = 4;

function platformLabel(platform?: string | null) {
  return (
    {
      youtube: "YouTube",
      bilibili: "哔哩哔哩",
      xiaoyuzhou: "小宇宙",
      douyin: "抖音",
      xiaohongshu: "小红书",
      wechat_article: "微信公众号",
      local_file: "本地文件",
      generic_web: "网页文章",
    }[platform || ""] || "内容"
  );
}

function contentTypeLabel(type?: string | null) {
  return (
    {
      video: "视频",
      audio: "音频",
      article: "文章",
      image_article: "图文",
      webpage: "网页",
    }[type || ""] || "内容"
  );
}

function isLikelyUrl(value?: string | null) {
  if (!value) return false;
  const text = value.trim();
  return /^https?:\/\//i.test(text) || /^(xhslink\.com|b23\.tv|v\.douyin\.com|youtu\.be)\//i.test(text);
}

function sanitizePreview(value?: string | null) {
  const text = (value || "").replace(/\s+/g, " ").trim();
  if (!text || isLikelyUrl(text)) return "";
  return text;
}

function friendlyFallbackTitle(item: CaptureListItem) {
  const platform = platformLabel(item.source_platform);
  const type = contentTypeLabel(item.content_type);
  if (item.source_platform === "wechat_article") return "微信公众号图文";
  if (item.source_platform === "local_file") return "本地文件";
  if (type === "图文") return `${platform}图文`;
  if (type === "视频") return `${platform}视频`;
  if (type === "音频") return `${platform}音频`;
  return platform;
}

function displayTitle(item: CaptureListItem) {
  const preferred = [item.title, item.preview_text].map((value) => sanitizePreview(value)).find(Boolean);
  return preferred || friendlyFallbackTitle(item);
}

function clampText(value: string, limit = 34) {
  return value.length > limit ? `${value.slice(0, limit)}...` : value;
}

function fileSizeLabel(bytes: number) {
  const mb = bytes / 1024 / 1024;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function extensionSummary(extensions: string[]) {
  if (!extensions.length) return "";
  if (extensions.length <= 5) return extensions.join(" / ");
  return `${extensions.slice(0, 5).join(" / ")} 等 ${extensions.length} 种格式`;
}

export function InputStage({
  input,
  subtitle,
  selectedFile,
  submitting,
  supportedExtensions,
  maxUploadSizeMb,
  freeDurationMinutes,
  captures,
  currentCaptureId,
  onInputChange,
  onSubmitText,
  onPaste,
  onSelectFile,
  onSubmitFile,
  onOpenCapture,
  onClearHistory,
  onDeleteCapture,
}: InputStageProps) {
  const [showAllHistory, setShowAllHistory] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const extensionLabel = useMemo(() => extensionSummary(supportedExtensions), [supportedExtensions]);

  useEffect(() => {
    setShowAllHistory(false);
  }, [captures.length]);

  const visibleCaptures = useMemo(
    () => (showAllHistory ? captures : captures.slice(0, COLLAPSED_HISTORY_COUNT)),
    [captures, showAllHistory],
  );

  function clearInput() {
    if (!input.trim() || submitting) return;
    onInputChange("");
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
    });
  }

  return (
    <section className="input-stage">
      <div className="input-stage-inner">
        <div className="hero-copy">
          <h1>把链接或文件，直接变成文字。</h1>
          <p>{subtitle}</p>
        </div>

        <form
          className="hero-form"
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmitText();
          }}
        >
          <div className="hero-form-glow" aria-hidden="true" />
          <div className="hero-form-card">
            {input.trim() ? (
              <button
                className="hero-input-clear"
                disabled={submitting}
                onClick={clearInput}
                type="button"
                aria-label="清空输入内容"
                title="清空输入内容"
              >
                <X size={14} />
              </button>
            ) : null}

            <textarea
              ref={textareaRef}
              value={input}
              onChange={(event) => onInputChange(event.target.value)}
              onPaste={(event) => {
                const text = event.clipboardData.getData("text/plain");
                if (text.trim()) {
                  event.preventDefault();
                  onInputChange(text);
                }
              }}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && input.trim() && !submitting) {
                  event.preventDefault();
                  void onSubmitText();
                  return;
                }
                if (event.key === "Escape" && input.trim() && !submitting) {
                  event.preventDefault();
                  clearInput();
                }
              }}
              placeholder="粘贴公开链接，比如 YouTube、哔哩哔哩、小宇宙、抖音、小红书或微信公众号文章"
              rows={6}
            />

            <div className="hero-form-footer">
              <div className="hero-form-tools">
                <button
                  className="text-tool"
                  onClick={() => onPaste()}
                  type="button"
                >
                  <Clipboard size={16} />
                  从剪贴板粘贴
                </button>

                <label className="text-tool file-tool">
                  <FileUp size={16} />
                  上传本地文件
                  <input
                    type="file"
                    hidden
                    accept=".mp3,.m4a,.wav,.flac,.mp4,.mov,.mkv"
                    onChange={(event) => onSelectFile(event.target.files?.[0] || null)}
                  />
                </label>
              </div>

              <button className="hero-submit" disabled={submitting || !input.trim()} type="submit" aria-label="开始处理">
                <ArrowRight size={18} />
              </button>
            </div>
          </div>
        </form>

        {!selectedFile ? (
          <div className="hero-support">
            <p>公开内容入页，片刻之后，自会落字成文。</p>
          </div>
        ) : null}

        {selectedFile ? (
          <div className="selected-file-card">
            <div>
              <strong title={selectedFile.name}>{selectedFile.name}</strong>
              <span>{extensionLabel ? `${fileSizeLabel(selectedFile.size)} · ${extensionLabel}` : fileSizeLabel(selectedFile.size)}</span>
              <small>{`${maxUploadSizeMb} MB 以内可直接处理，音视频建议控制在 ${freeDurationMinutes} 分钟内。`}</small>
            </div>
            <div className="selected-file-actions">
              <button className="inline-action ghost" onClick={() => onSelectFile(null)} disabled={submitting} type="button">
                移除文件
              </button>
              <button className="inline-action" onClick={onSubmitFile} disabled={submitting} type="button">
                处理这个文件
              </button>
            </div>
          </div>
        ) : null}

        <div className="recent-inline">
          <div className="recent-inline-head">
            <div>
              <span>最近回看</span>
              <small>保留刚刚整理过的内容，方便随时回到结果页。</small>
            </div>

            <div className="recent-inline-actions">
              {captures.length > COLLAPSED_HISTORY_COUNT ? (
                <button className="inline-action ghost" onClick={() => setShowAllHistory((value) => !value)} type="button">
                  {showAllHistory ? "收起" : "查看更多"}
                </button>
              ) : null}

              <button className="inline-action ghost" onClick={onClearHistory} disabled={!captures.length || submitting} type="button">
                清空记录
              </button>
            </div>
          </div>

          {visibleCaptures.length ? (
            <div className={`recent-inline-list ${showAllHistory ? "is-expanded" : "is-collapsed"}`}>
              {visibleCaptures.map((item) => (
                <div
                  key={item.id}
                  className={item.id === currentCaptureId ? "recent-inline-card-shell is-active" : "recent-inline-card-shell"}
                >
                  <button
                    className={item.id === currentCaptureId ? "recent-inline-card is-active" : "recent-inline-card"}
                    onClick={() => onOpenCapture(item.id)}
                    type="button"
                  >
                    <div className="recent-inline-meta">
                      <History size={13} />
                      <span>{platformLabel(item.source_platform)}</span>
                    </div>
                    <strong>{clampText(displayTitle(item), showAllHistory ? 44 : 34)}</strong>
                  </button>

                  <button
                    className="recent-inline-delete"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteCapture(item);
                    }}
                    type="button"
                    aria-label={`删除${displayTitle(item)}`}
                    title="删除这条记录"
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="recent-inline-empty">还没有历史记录。先贴一个链接或上传文件试试。</div>
          )}
        </div>
      </div>
    </section>
  );
}
