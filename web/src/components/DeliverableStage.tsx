import {
  ArrowLeft,
  Check,
  Copy,
  Download,
  Expand,
  ExternalLink,
  FolderOpen,
  Headphones,
  FileText,
  Film,
  Images,
  Pause,
  Play,
  RotateCcw,
  Save,
  SkipBack,
  SkipForward,
  X,
} from "lucide-react";
import {
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
  type SyntheticEvent,
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  calmErrorMessage,
  ensureCaptureCover,
  resolveApiUrl,
  retryCaptureImage,
} from "../api";
import {
  isDesktopApp,
  openCaptureSource,
  openSavedArtifact,
  revealSavedArtifact,
  saveDesktopArtifact,
  type SavedArtifact,
} from "../desktop";
import type { Artifact, CaptureEnvelope, CaptureSourceImage, ResultSegment } from "../types";
import type { ImageViewerItem, ImageViewerMode } from "./ImageViewer";

const ImageViewer = lazy(() => import("./ImageViewer"));
import { ProofreadingButton } from "./LocalTools";

type DeliverableStageProps = {
  capture: CaptureEnvelope;
  onCaptureUpdated: (capture: CaptureEnvelope) => void;
  onNotify: (message: string) => void;
  onReset: () => void;
  onRetry?: () => void;
};

type VideoOrientation = "unknown" | "portrait" | "landscape" | "square-ish";
type ImagePreviewMode = ImageViewerMode;
type ImageGalleryItem = {
  index: number;
  label: string;
  imageSourceUrl: string;
  liveSourceUrl: string | null;
  imageArtifact: Artifact | null;
  liveArtifact: Artifact | null;
  deliveryStatus: "pending" | "ready" | "failed";
  thumbnailUrl: string;
  width: number | null;
  height: number | null;
};
type SaveReceipt = SavedArtifact & { artifactType: string };

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

function bytesLabel(size?: number | null) {
  if (!size) return "";
  const mb = size / 1024 / 1024;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  const kb = size / 1024;
  return `${Math.max(1, Math.round(kb))} KB`;
}

function artifactUrl(artifact: Artifact) {
  return resolveApiUrl(artifact.download_url, true);
}

function artifactPreviewUrl(artifact: Artifact) {
  return resolveApiUrl(artifact.preview_url || `${artifact.download_url}/preview`, true);
}

function ArtifactAction({
  artifact,
  captureId,
  className = "copy-action",
  children,
  onSaved,
  onNotify,
}: {
  artifact: Artifact;
  captureId: string;
  className?: string;
  children: ReactNode;
  onSaved: (receipt: SaveReceipt) => void;
  onNotify: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  if (!isDesktopApp()) {
    return (
      <a
        className={className}
        download
        href={artifactUrl(artifact)}
        onPointerDown={(event) => event.stopPropagation()}
      >
        {children}
      </a>
    );
  }
  return (
    <button
      className={className}
      disabled={busy}
      onClick={async () => {
        if (busy) return;
        setBusy(true);
        try {
          const saved = await saveDesktopArtifact(captureId, artifact.type);
          onSaved({ ...saved, artifactType: artifact.type });
          setSaved(true);
          window.setTimeout(() => setSaved(false), 1800);
        } catch (error) {
          onNotify(calmErrorMessage(error, "文件暂时还没保存好，请稍后再试。"));
        } finally {
          setBusy(false);
        }
      }}
      onPointerDown={(event) => event.stopPropagation()}
      type="button"
    >
      {busy ? (
        "正在保存"
      ) : saved ? (
        <>
          <Check size={14} />
          已保存
        </>
      ) : (
        children
      )}
    </button>
  );
}

function isStaticImageArtifact(artifact: Artifact) {
  return /^image_\d+$/.test(artifact.type);
}

function imageArtifactIndex(type: string) {
  const match = type.match(/^(?:image|image_live)_(\d+)$/);
  return match ? Number(match[1]) : null;
}

function durationLabel(seconds?: number | null) {
  if (!seconds) return "";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
}

function platformLabel(platform: string) {
  return (
    {
      youtube: "YouTube",
      bilibili: "哔哩哔哩",
      xiaoyuzhou: "小宇宙",
      douyin: "抖音",
      xiaohongshu: "小红书",
      wechat_article: "微信公众号",
      generic_web: "网页",
      local_file: "本地文件",
    }[platform] || platform
  );
}

function contentTypeLabel(contentType: string) {
  return (
    {
      audio: "音频",
      video: "视频",
      article: "文章",
      image_article: "图文",
      webpage: "网页",
      unknown: "未知",
    }[contentType] || contentType
  );
}

function factLabel(key: string, fallback: string) {
  return (
    {
      title: "标题",
      platform: "平台",
      content_type: "内容类型",
      author: "作者",
      published_at: "发布时间",
      duration: "时长",
    }[key] || fallback
  );
}

function heroDescription(capture: CaptureEnvelope, hasVideoArtifact: boolean) {
  if (
    capture.source.media_kind === "image_article" ||
    capture.source.platform === "wechat_article"
  ) {
    return capture.capture.asset_preparation_pending
      ? "正文和图片已可查看，下载文件仍在后台准备。"
      : "正文和图片已整理完成，可直接查看、复制或下载。";
  }
  if (capture.source.media_kind === "video") {
    if (hasVideoArtifact) return "文字与视频已整理完成，可直接查看、播放或下载。";
    return capture.capture.asset_preparation_pending
      ? "文字已可查看，原视频正在后台下载。"
      : "文字已整理完成，视频文件暂未就绪。";
  }
  if (capture.source.media_kind === "audio") {
    return "文字已整理完成，可直接复制或下载。";
  }
  return "结果已整理完成，可直接复制或下载。";
}

function looksLikeUrl(value?: string | null) {
  return !!value && /^https?:\/\//i.test(value.trim());
}

function resultSourceUrl(capture: CaptureEnvelope) {
  const candidate = (capture.source.canonical_url || "").trim();
  return looksLikeUrl(candidate) ? candidate : "";
}

function heroTitle(capture: CaptureEnvelope) {
  const sourceTitle = capture.source.title || capture.capture.title || "";
  if (sourceTitle && !looksLikeUrl(sourceTitle)) {
    return sourceTitle;
  }
  if (
    capture.source.media_kind === "image_article" ||
    capture.source.platform === "wechat_article"
  ) {
    return "图文整理结果";
  }
  if (capture.source.media_kind === "video") {
    return "视频整理结果";
  }
  if (capture.source.media_kind === "audio") {
    return "音频整理结果";
  }
  return "整理结果";
}

function shouldShowImages(capture: CaptureEnvelope) {
  return (
    capture.source.media_kind === "image_article" ||
    capture.source.platform === "wechat_article"
  );
}

function buildGalleryItems(capture: CaptureEnvelope): ImageGalleryItem[] {
  const imageArtifactsByIndex = new Map<number, Artifact>();
  const liveArtifactsByIndex = new Map<number, Artifact>();
  for (const artifact of capture.artifacts) {
    const index = imageArtifactIndex(artifact.type);
    if (!index) continue;
    if (isStaticImageArtifact(artifact)) {
      imageArtifactsByIndex.set(index, artifact);
      continue;
    }
    if (/^image_live_\d+$/.test(artifact.type)) {
      liveArtifactsByIndex.set(index, artifact);
    }
  }

  const sourceImages: CaptureSourceImage[] =
    capture.source.images.length > 0
      ? capture.source.images
      : capture.source.image_urls.map((imageUrl, index) => ({
          index: index + 1,
          image_url: imageUrl,
          live_photo_video_url: null,
        }));
  const sourceImagesByIndex = new Map(
    sourceImages.map((item) => [item.index, item]),
  );

  const allIndices = Array.from(
    new Set<number>([
      ...sourceImagesByIndex.keys(),
      ...imageArtifactsByIndex.keys(),
      ...liveArtifactsByIndex.keys(),
    ]),
  ).sort((left, right) => left - right);

  return allIndices
    .map((index) => {
      const sourceItem = sourceImagesByIndex.get(index);
      const imageArtifact = imageArtifactsByIndex.get(index) || null;
      const liveArtifact = liveArtifactsByIndex.get(index) || null;
      const imageSourceUrl = (sourceItem?.image_url || "").trim();
      const liveSourceUrl = (sourceItem?.live_photo_video_url || "").trim();
      const deliveryStatus =
        sourceItem?.delivery_status || imageArtifact?.status || "pending";

      if (!imageArtifact && !imageSourceUrl) {
        return null;
      }

      return {
        index,
        label: imageArtifact?.label || `图片 ${index}`,
        imageSourceUrl,
        liveSourceUrl: liveSourceUrl || null,
        imageArtifact,
        liveArtifact,
        deliveryStatus,
        thumbnailUrl: (sourceItem?.thumbnail_url || "").trim(),
        width: sourceItem?.width || null,
        height: sourceItem?.height || null,
      } satisfies ImageGalleryItem;
    })
    .filter((item): item is ImageGalleryItem => Boolean(item));
}

function preferredImageUrl(item: ImageGalleryItem) {
  if (item.thumbnailUrl) return resolveApiUrl(item.thumbnailUrl, true);
  return preferredFullImageUrl(item);
}

function preferredFullImageUrl(item: ImageGalleryItem) {
  return item.imageArtifact?.status === "ready"
    ? artifactPreviewUrl(item.imageArtifact)
    : "";
}

function imageFallbackUrl(item: ImageGalleryItem) {
  return preferredFullImageUrl(item) || item.imageSourceUrl;
}

function preferredLiveUrl(item: ImageGalleryItem) {
  return item.liveArtifact?.status === "ready"
    ? artifactPreviewUrl(item.liveArtifact)
    : "";
}

function liveFallbackUrl(item: ImageGalleryItem) {
  return "";
}

function MediaImage({
  alt,
  primarySrc,
  fallbackSrc,
  className,
  eager = false,
  onLoad,
}: {
  alt: string;
  primarySrc: string;
  fallbackSrc?: string;
  className?: string;
  eager?: boolean;
  onLoad?: () => void;
}) {
  const resolvedPrimary = primarySrc || fallbackSrc || "";
  const resolvedFallback = fallbackSrc || "";
  const [src, setSrc] = useState(resolvedPrimary);
  const imageRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    setSrc(resolvedPrimary);
  }, [resolvedPrimary]);

  useEffect(() => {
    const image = imageRef.current;
    if (!image || !image.complete) return;
    if (image.naturalWidth > 0) {
      onLoad?.();
      return;
    }
    if (resolvedFallback && src !== resolvedFallback) {
      setSrc(resolvedFallback);
    }
  }, [resolvedFallback, resolvedPrimary, src]);

  if (!resolvedPrimary) {
    return (
      <div
        aria-label={alt}
        className={className ? `${className} is-empty` : "is-empty"}
        role="img"
      >
        <span>图片准备中</span>
      </div>
    );
  }

  return (
    <img
      alt={alt}
      className={className}
      loading={eager ? "eager" : "lazy"}
      ref={imageRef}
      src={src}
      onLoad={() => onLoad?.()}
      onError={() => {
        if (resolvedFallback && src !== resolvedFallback) {
          setSrc(resolvedFallback);
        }
      }}
    />
  );
}

function LiveHoverPreview({ item }: { item: ImageGalleryItem }) {
  const primarySrc = preferredLiveUrl(item);
  const fallbackSrc = liveFallbackUrl(item);
  const [src, setSrc] = useState(primarySrc);

  useEffect(() => {
    setSrc(primarySrc);
  }, [primarySrc]);

  return (
    <div className="image-card-live-preview">
      <video
        autoPlay
        loop
        muted
        playsInline
        poster={preferredImageUrl(item)}
        preload="metadata"
        src={src}
        onError={() => {
          if (fallbackSrc && src !== fallbackSrc) {
            setSrc(fallbackSrc);
          }
        }}
      />
    </div>
  );
}

function ImageGalleryCard({
  item,
  captureId,
  onOpen,
  onSaved,
  onNotify,
  onRetry,
}: {
  item: ImageGalleryItem;
  captureId: string;
  onOpen: (trigger: HTMLButtonElement) => void;
  onSaved: (receipt: SaveReceipt) => void;
  onNotify: (message: string) => void;
  onRetry: () => void;
}) {
  const [showLivePreview, setShowLivePreview] = useState(false);
  const canPreviewLive = item.liveArtifact?.status === "ready";
  const preferredUrl = preferredImageUrl(item);
  const imageReady = item.deliveryStatus === "ready" && Boolean(preferredUrl);
  const [imageLoaded, setImageLoaded] = useState(false);
  const isImageLoading = imageReady && !imageLoaded;

  useEffect(() => {
    setImageLoaded(false);
  }, [preferredUrl]);

  const cardClassName = [
    "image-card",
    showLivePreview ? "is-live-hovered" : "",
    isImageLoading ? "is-image-loading" : "",
    !imageReady ? `is-${item.deliveryStatus}` : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article
      aria-busy={isImageLoading}
      className={cardClassName}
      onMouseEnter={() => {
        if (canPreviewLive) setShowLivePreview(true);
      }}
      onMouseLeave={() => {
        setShowLivePreview(false);
      }}
    >
      <button
        aria-label={`${canPreviewLive ? "LIVE，" : ""}查看图片 ${item.index}`}
        className="image-card-media-button"
        disabled={!imageReady}
        onClick={(event) => onOpen(event.currentTarget)}
        type="button"
      >
        {canPreviewLive ? (
          <span className="image-card-badge">
            <Play size={11} />
            LIVE
          </span>
        ) : null}

        {!imageReady ? (
          <span className="image-card-delivery-state">
            <span aria-hidden="true" className="image-card-delivery-icon">
              <Images size={18} />
            </span>
            {item.deliveryStatus === "failed" ? (
              <>
                <strong>这张图片暂未就绪</strong>
                <span>可以单独重新获取，不影响其他内容。</span>
              </>
            ) : (
              <>
                <strong>图片准备中</strong>
                <span>完成后会在这里原位显示。</span>
              </>
            )}
          </span>
        ) : showLivePreview && canPreviewLive ? (
          <LiveHoverPreview item={item} />
        ) : (
          <MediaImage
            alt={item.label}
            className="image-card-media-image"
            eager
            fallbackSrc={imageFallbackUrl(item)}
            onLoad={() => setImageLoaded(true)}
            primarySrc={preferredUrl}
          />
        )}

        <span aria-hidden="true" className="image-card-view-hint">
          <Expand size={14} />
        </span>
      </button>

      <div className="image-card-footer">
        <div className="image-card-meta">
          <span>{`图片 ${item.index}`}</span>
        </div>

        {item.deliveryStatus === "failed" ? (
          <button className="image-card-action" onClick={onRetry} type="button">
            <RotateCcw size={13} />
            重新获取
          </button>
        ) : item.imageArtifact?.status === "ready" ? (
          <ArtifactAction
            artifact={item.imageArtifact}
            captureId={captureId}
            className="image-card-action"
            onNotify={onNotify}
            onSaved={onSaved}
          >
            <Download size={13} />
            {isDesktopApp() ? "保存" : "下载"}
          </ArtifactAction>
        ) : null}
      </div>
    </article>
  );
}

function VideoPanel({
  previewArtifact,
  sourceArtifact,
  audioArtifact,
  posterUrl,
  captureId,
  onSaved,
  onNotify,
}: {
  previewArtifact: Artifact | null;
  sourceArtifact: Artifact | null;
  audioArtifact: Artifact | null;
  posterUrl?: string | null;
  captureId: string;
  onSaved: (receipt: SaveReceipt) => void;
  onNotify: (message: string) => void;
}) {
  const [orientation, setOrientation] = useState<VideoOrientation>("unknown");
  const [playbackError, setPlaybackError] = useState(false);
  const [aspectRatio, setAspectRatio] = useState<string>("");
  const [preferPreview, setPreferPreview] = useState(Boolean(previewArtifact));
  const activeArtifact =
    (preferPreview ? previewArtifact : sourceArtifact) ||
    sourceArtifact ||
    previewArtifact;
  const downloadArtifact = sourceArtifact || activeArtifact;
  const fileSize = bytesLabel(downloadArtifact?.size_bytes);

  useEffect(() => {
    setPreferPreview(Boolean(previewArtifact));
    setPlaybackError(false);
  }, [previewArtifact?.download_url, sourceArtifact?.download_url]);

  function handleLoadedMetadata(event: SyntheticEvent<HTMLVideoElement>) {
    const { videoWidth, videoHeight } = event.currentTarget;
    if (!videoWidth || !videoHeight) {
      setOrientation("unknown");
      setAspectRatio("");
      return;
    }
    setAspectRatio(`${videoWidth} / ${videoHeight}`);
    if (videoHeight / videoWidth >= 1.15) {
      setOrientation("portrait");
      return;
    }
    if (videoWidth / videoHeight >= 1.15) {
      setOrientation("landscape");
      return;
    }
    setOrientation("square-ish");
  }

  const panelStyle = aspectRatio
    ? ({
        ["--video-aspect-ratio" as "--video-aspect-ratio"]: aspectRatio,
      } as CSSProperties)
    : undefined;

  if (!activeArtifact || !downloadArtifact) {
    return null;
  }

  return (
    <section
      className="media-panel media-panel-playable"
      data-orientation={orientation}
      style={panelStyle}
    >
      <div className="media-panel-head">
        <div>
          <span className="result-section-kicker">视频</span>
          <strong>预览</strong>
        </div>

        <div className="media-panel-actions">
          {audioArtifact ? (
            <ArtifactAction
              artifact={audioArtifact}
              captureId={captureId}
              className="copy-action media-action-audio"
              onNotify={onNotify}
              onSaved={onSaved}
            >
              <Headphones size={14} />
              {isDesktopApp() ? "保存音频" : "下载音频"}
            </ArtifactAction>
          ) : null}
          <ArtifactAction
            artifact={downloadArtifact}
            captureId={captureId}
            onNotify={onNotify}
            onSaved={onSaved}
          >
            <Download size={14} />
            {isDesktopApp() ? "保存视频" : "下载视频"}
          </ArtifactAction>
          {fileSize ? (
            <span className="media-size-pill">{fileSize}</span>
          ) : null}
        </div>
      </div>

      <div className="media-panel-body">
        <div className="video-shell">
          <video
            controls
            preload="auto"
            playsInline
            poster={posterUrl || undefined}
            src={artifactPreviewUrl(activeArtifact)}
            onCanPlay={() => setPlaybackError(false)}
            onError={() => {
              if (
                preferPreview &&
                previewArtifact &&
                sourceArtifact &&
                previewArtifact.download_url !== sourceArtifact.download_url
              ) {
                setPreferPreview(false);
                return;
              }
              setPlaybackError(true);
            }}
            onLoadedMetadata={handleLoadedMetadata}
          >
            当前浏览器不支持直接预览，请使用上方按钮下载原视频。
          </video>
        </div>
      </div>

      {playbackError ? (
        <p className="media-panel-note">
          当前浏览器无法直接内联预览，仍可使用上方按钮下载原视频文件。
        </p>
      ) : null}
    </section>
  );
}

function formatPlaybackTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

function PodcastPanel({
  capture,
  audioArtifact,
  coverArtifact,
  onTimeChange,
  audioRef,
  onSaved,
  onNotify,
}: {
  capture: CaptureEnvelope;
  audioArtifact: Artifact;
  coverArtifact: Artifact | null;
  onTimeChange: (time: number) => void;
  audioRef: MutableRefObject<HTMLAudioElement | null>;
  onSaved: (receipt: SaveReceipt) => void;
  onNotify: (message: string) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(capture.source.duration_seconds || 0);
  const [speed, setSpeed] = useState(1);
  const [panelVisible, setPanelVisible] = useState(true);
  const [playbackStarted, setPlaybackStarted] = useState(false);
  const panelRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => setPanelVisible(entry.intersectionRatio >= 0.55),
      { threshold: [0, 0.55, 1] },
    );
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);

  function seekBy(delta: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = clampNumber(audio.currentTime + delta, 0, audio.duration || duration || 0);
  }

  function togglePlayback() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      setPlaybackStarted(true);
      void audio.play();
    }
    else audio.pause();
  }

  return (
    <>
    <section className="podcast-panel" aria-label="播客播放器" ref={panelRef}>
      <audio
        ref={audioRef}
        preload="metadata"
        src={artifactPreviewUrl(audioArtifact)}
        onDurationChange={(event) => setDuration(event.currentTarget.duration || duration)}
        onEnded={() => setPlaying(false)}
        onPause={() => setPlaying(false)}
        onPlay={() => setPlaying(true)}
        onTimeUpdate={(event) => {
          const nextTime = event.currentTarget.currentTime;
          setCurrentTime(nextTime);
          onTimeChange(nextTime);
        }}
      />
      <div className="podcast-cover-wrap">
        {coverArtifact ? (
          <img alt="节目封面" className="podcast-cover" src={artifactPreviewUrl(coverArtifact)} />
        ) : (
          <div className="podcast-cover is-placeholder" aria-hidden="true">
            <Headphones size={32} />
          </div>
        )}
      </div>
      <div className="podcast-heading">
        <span className="result-section-kicker">播客</span>
        <strong>{capture.source.title || capture.capture.title || "音频节目"}</strong>
        {capture.source.author ? <span>{capture.source.author}</span> : null}
      </div>
      <div className="podcast-controls">
        <button aria-label="后退 15 秒" className="podcast-icon-button" onClick={() => seekBy(-15)} type="button">
          <SkipBack size={18} />
          <span>15</span>
        </button>
        <button aria-label={playing ? "暂停" : "播放"} className="podcast-play-button" onClick={togglePlayback} type="button">
          {playing ? <Pause size={20} /> : <Play size={20} fill="currentColor" />}
        </button>
        <button aria-label="前进 15 秒" className="podcast-icon-button" onClick={() => seekBy(15)} type="button">
          <SkipForward size={18} />
          <span>15</span>
        </button>
      </div>
      <div className="podcast-timeline">
        <span>{formatPlaybackTime(currentTime)}</span>
        <input
          aria-label="播放进度"
          max={Math.max(duration, 0.01)}
          min="0"
          onChange={(event) => {
            const value = Number(event.currentTarget.value);
            if (audioRef.current) audioRef.current.currentTime = value;
            setCurrentTime(value);
          }}
          step="0.1"
          type="range"
          value={Math.min(currentTime, Math.max(duration, 0.01))}
        />
        <span>{formatPlaybackTime(duration)}</span>
      </div>
      <div className="podcast-footer">
        <label>
          <span>倍速</span>
          <select
            aria-label="播放速度"
            onChange={(event) => {
              const value = Number(event.currentTarget.value);
              setSpeed(value);
              if (audioRef.current) audioRef.current.playbackRate = value;
            }}
            value={speed}
          >
            {[0.75, 1, 1.25, 1.5, 2].map((value) => (
              <option key={value} value={value}>{value}x</option>
            ))}
          </select>
        </label>
        <span>{audioArtifact.mime_type === "audio/mp4" ? "M4A" : audioArtifact.mime_type.replace("audio/", "").toUpperCase()}</span>
        <ArtifactAction
          artifact={audioArtifact}
          captureId={capture.capture.id}
          className="copy-action media-action-audio"
          onNotify={onNotify}
          onSaved={onSaved}
        >
          <Headphones size={14} />
          {isDesktopApp() ? "保存音频" : "下载音频"}
        </ArtifactAction>
      </div>
    </section>
    <div
      aria-hidden={panelVisible || !playbackStarted}
      className={
        !panelVisible && playbackStarted
          ? "podcast-compact-bar is-visible"
          : "podcast-compact-bar"
      }
    >
      <button
        aria-label={playing ? "暂停播客" : "继续播放播客"}
        onClick={togglePlayback}
        type="button"
      >
        {playing ? <Pause size={18} /> : <Play size={18} fill="currentColor" />}
      </button>
      <div>
        <strong>{capture.source.title || capture.capture.title || "音频节目"}</strong>
        <span>{`${formatPlaybackTime(currentTime)} / ${formatPlaybackTime(duration)}`}</span>
      </div>
      <progress aria-label="播客播放进度" max={Math.max(duration, 0.01)} value={currentTime} />
    </div>
    </>
  );
}

function TimedTranscript({
  segments,
  activeTime,
  onSeek,
}: {
  segments: ResultSegment[];
  activeTime: number;
  onSeek: (seconds: number) => void;
}) {
  return (
    <div className="timed-transcript" aria-label="带时间的正文">
      {segments.map((segment, index) => {
        const start = segment.start_sec ?? 0;
        const end = segment.end_sec ?? Number.POSITIVE_INFINITY;
        const active = activeTime >= start && activeTime < end;
        return (
          <button
            className={active ? "transcript-segment is-active" : "transcript-segment"}
            key={`${start}-${index}`}
            onClick={() => onSeek(start)}
            type="button"
          >
            <span>{formatPlaybackTime(start)}</span>
            <p>{segment.text}</p>
          </button>
        );
      })}
    </div>
  );
}

function VideoUnavailablePanel({
  notice,
  onRetry,
  pending = false,
}: {
  notice: string;
  onRetry?: () => void;
  pending?: boolean;
}) {
  if (pending) {
    // Text-first: the transcript is ready, the original video is still downloading.
    return (
      <section className="media-panel media-panel-empty is-pending" aria-live="polite">
        <div className="media-panel-head">
          <div>
            <span className="result-section-kicker">视频</span>
            <strong>预览</strong>
          </div>
        </div>
        <div className="media-panel-body">
          <div className="media-panel-empty-card">
            <Film size={22} />
            <strong>正在下载原视频</strong>
            <p>文字已经可以阅读和复制，视频下载好后会出现在这里。</p>
            <span className="media-panel-pending-bar" aria-hidden="true" />
          </div>
        </div>
      </section>
    );
  }
  return (
    <section className="media-panel media-panel-empty">
      <div className="media-panel-head">
        <div>
          <span className="result-section-kicker">视频</span>
          <strong>预览</strong>
        </div>
      </div>

      <div className="media-panel-body">
        <div className="media-panel-empty-card">
          <Film size={22} />
          <strong>暂未拿到可预览视频</strong>
          <p>{notice || "文本已整理完成，但视频文件暂未就绪。"}</p>
          {onRetry ? (
            <button className="copy-action is-primary media-panel-retry" onClick={onRetry} type="button">
              <RotateCcw size={14} aria-hidden="true" />
              重新获取视频
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}

export function DeliverableStage({
  capture,
  onCaptureUpdated,
  onNotify,
  onReset,
  onRetry,
}: DeliverableStageProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">(
    "idle",
  );
  const [activeImageIndex, setActiveImageIndex] = useState<number | null>(null);
  const [previewMode, setPreviewMode] = useState<ImagePreviewMode>("image");
  const [retryingImage, setRetryingImage] = useState<number | null>(null);
  const [saveReceipt, setSaveReceipt] = useState<SaveReceipt | null>(null);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [coverRequested, setCoverRequested] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const viewerTriggerRef = useRef<HTMLButtonElement | null>(null);
  const primaryText =
    capture.result.views.primary || capture.result.primary_text || "";
  const markdownText = capture.result.views.markdown || "";
  const copyPayload = markdownText.trim() || primaryText;
  const isLongTextResult =
    primaryText.trim().length >= 900 ||
    primaryText.split("\n").filter((item) => item.trim()).length >= 8;
  const showImages = shouldShowImages(capture);
  const imageItems = useMemo(() => buildGalleryItems(capture), [capture]);
  const viewerItems = useMemo(
    () =>
      imageItems.filter(
        (item) =>
          item.deliveryStatus === "ready" && Boolean(preferredImageUrl(item)),
      ),
    [imageItems],
  );
  const viewerSlides = useMemo<ImageViewerItem[]>(
    () =>
      viewerItems.map((item) => ({
        index: item.index,
        label: item.label,
        imageSrc: preferredFullImageUrl(item),
        imageFallbackSrc: item.imageSourceUrl,
        liveSrc: preferredLiveUrl(item),
        liveFallbackSrc: liveFallbackUrl(item),
        imageArtifact: item.imageArtifact,
        liveArtifact: item.liveArtifact,
        width: item.width,
        height: item.height,
      })),
    [viewerItems],
  );
  const imagesZip = useMemo(
    () =>
      showImages
        ? capture.artifacts.find((item) => item.type === "images_zip") || null
        : null,
    [capture.artifacts, showImages],
  );
  const textArtifact = useMemo(
    () => capture.artifacts.find((item) => item.type === "txt") || null,
    [capture.artifacts],
  );
  const markdownArtifact = useMemo(
    () => capture.artifacts.find((item) => item.type === "md") || null,
    [capture.artifacts],
  );
  const sourceAudioArtifact = useMemo(
    () =>
      capture.artifacts.find((item) => item.type === "source_audio") || null,
    [capture.artifacts],
  );
  const coverArtifact = useMemo(
    () => capture.artifacts.find((item) => item.type === "cover_image") || null,
    [capture.artifacts],
  );
  const timedSegments = useMemo(
    () =>
      (capture.result.segments || []).filter(
        (item) => typeof item.start_sec === "number" && item.text.trim(),
      ),
    [capture.result.segments],
  );
  const previewVideoArtifact = useMemo(
    () =>
      capture.source.media_kind === "video"
        ? capture.artifacts.find((item) => item.type === "preview_media") ||
          null
        : null,
    [capture.artifacts, capture.source.media_kind],
  );
  const sourceVideoArtifact = useMemo(
    () =>
      capture.source.media_kind === "video"
        ? capture.artifacts.find((item) => item.type === "source_media") || null
        : null,
    [capture.artifacts, capture.source.media_kind],
  );
  const fallbackVideoArtifact = useMemo(
    () =>
      capture.source.media_kind === "video"
        ? capture.artifacts.find((item) =>
            item.mime_type.startsWith("video/"),
          ) || null
        : null,
    [capture.artifacts, capture.source.media_kind],
  );
  const videoArtifact =
    previewVideoArtifact || sourceVideoArtifact || fallbackVideoArtifact;
  const hasVideoPanel = capture.source.media_kind === "video";
  const hasPodcastPanel =
    capture.source.media_kind === "audio" && Boolean(sourceAudioArtifact);
  const hasPlayableVideo = Boolean(videoArtifact);
  const sourceUrl = resultSourceUrl(capture);
  const visibleFacts = useMemo(
    () =>
      capture.result.content_facts.filter((fact) =>
        ["title", "platform", "content_type", "author", "published_at", "duration"].includes(
          fact.key,
        ),
      ),
    [capture.result.content_facts],
  );
  const isImageOnlyResult = showImages && !primaryText.trim();
  const isVideoOnlyResult = hasPlayableVideo && !primaryText.trim();
  const heroHeading = heroTitle(capture);
  const showCopyAction = Boolean(copyPayload.trim()) && !isImageOnlyResult;
  const imagesPending = showImages && capture.capture.asset_preparation_pending;
  const mainGridClass = hasVideoPanel
    ? "deliverable-workspace has-video"
    : hasPodcastPanel
      ? "deliverable-workspace has-audio"
    : showImages
      ? "deliverable-workspace has-images"
      : "deliverable-workspace";

  const titleFact = visibleFacts.find((fact) => fact.key === "title");
  const secondaryFacts = visibleFacts.filter((fact) => fact.key !== "title");
  const factsSection = titleFact || secondaryFacts.length || sourceUrl ? (
    <section className="facts-strip">
      {titleFact ? (
        <div className="facts-strip-main">
          <div className="fact-chip fact-chip-title">
            <span>{factLabel(titleFact.key, titleFact.label)}</span>
            <strong title={titleFact.value}>{titleFact.value}</strong>
          </div>
        </div>
      ) : null}

      {secondaryFacts.length || sourceUrl ? (
        <div className="facts-strip-side">
          {secondaryFacts.map((fact) => (
            <div className={`fact-chip fact-chip-${fact.key}`} key={fact.key}>
              <span>{factLabel(fact.key, fact.label)}</span>
              <strong title={fact.value}>{fact.value}</strong>
            </div>
          ))}
          {sourceUrl ? (
            <button
              className="fact-chip fact-chip-action"
              onClick={() => void handleOpenSource()}
              type="button"
            >
              <span>来源</span>
              <strong>查看来源</strong>
              <ExternalLink size={13} />
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  ) : null;

  async function handleCopy() {
    if (!copyPayload.trim()) return;
    const ok = await copyText(copyPayload);
    setCopyState(ok ? "copied" : "failed");
    window.setTimeout(() => setCopyState("idle"), 2200);
  }

  async function handleRetryImage(imageIndex: number) {
    if (retryingImage != null) return;
    setRetryingImage(imageIndex);
    try {
      const updated = await retryCaptureImage(capture.capture.id, imageIndex);
      onCaptureUpdated(updated);
      onNotify(`图片 ${imageIndex} 已重新获取。`);
    } catch (error) {
      onNotify(calmErrorMessage(error, "这张图片暂时未能重新获取。"));
    } finally {
      setRetryingImage(null);
    }
  }

  async function handleOpenSource() {
    if (!sourceUrl) return;
    if (!isDesktopApp()) {
      window.open(sourceUrl, "_blank", "noopener,noreferrer");
      return;
    }
    try {
      await openCaptureSource(capture.capture.id);
    } catch (error) {
      onNotify(calmErrorMessage(error, "暂时无法打开来源页面。"));
    }
  }

  function seekPodcast(seconds: number) {
    if (!audioRef.current) return;
    audioRef.current.currentTime = seconds;
    setPlaybackTime(seconds);
    void audioRef.current.play();
  }

  useEffect(() => {
    if (activeImageIndex == null) return;
    if (!viewerItems.length || activeImageIndex >= viewerItems.length) {
      setActiveImageIndex(null);
    }
  }, [activeImageIndex, viewerItems.length]);

  useEffect(() => {
    setCoverRequested(false);
  }, [capture.capture.id]);

  useEffect(() => {
    if (!saveReceipt) return undefined;
    const timer = window.setTimeout(() => setSaveReceipt(null), 4000);
    return () => window.clearTimeout(timer);
  }, [saveReceipt]);

  useEffect(() => {
    if (
      coverRequested ||
      capture.source.media_kind !== "audio" ||
      coverArtifact ||
      !capture.source.thumbnail_url
    ) {
      return;
    }
    setCoverRequested(true);
    void ensureCaptureCover(capture.capture.id)
      .then(onCaptureUpdated)
      .catch(() => undefined);
  }, [
    capture.capture.id,
    capture.source.media_kind,
    capture.source.thumbnail_url,
    coverArtifact,
    coverRequested,
    onCaptureUpdated,
  ]);

  return (
    <section className="deliverable-stage">
      <div className="deliverable-stage-inner">
        {isDesktopApp() ? (
          <button
            aria-label="返回首页"
            className="desktop-result-back"
            onClick={onReset}
            title="返回首页"
            type="button"
          >
            <ArrowLeft size={18} />
          </button>
        ) : null}
        <header className="deliverable-hero">
          <div className="deliverable-hero-pill">
            <FileText size={14} />
            已经整理完成
          </div>

          <h1 title={heroHeading}>{heroHeading}</h1>
          <p>{heroDescription(capture, hasPlayableVideo)}</p>

          <div className="deliverable-meta">
            <span>{platformLabel(capture.source.platform)}</span>
            <span>{contentTypeLabel(capture.source.media_kind)}</span>
            {capture.source.duration_seconds ? (
              <span>{durationLabel(capture.source.duration_seconds)}</span>
            ) : null}
          </div>

        </header>

        <div className="deliverable-workspace-shell">
          <div className={mainGridClass}>
            <div className="deliverable-primary">
              <div className="result-surface">
                <div className="result-surface-head">
                  <div className="result-surface-copy">
                    <span className="result-section-kicker">文字</span>
                    <strong>正文</strong>
                  </div>

                  <div className="result-actions">
                    {sourceAudioArtifact && !hasVideoPanel && !hasPodcastPanel ? (
                      <ArtifactAction
                        artifact={sourceAudioArtifact}
                        captureId={capture.capture.id}
                        className="copy-action media-action-audio"
                        onNotify={onNotify}
                        onSaved={setSaveReceipt}
                      >
                        <Headphones size={14} />
                        {isDesktopApp() ? "保存音频" : "下载音频"}
                      </ArtifactAction>
                    ) : null}

                    {textArtifact ? (
                      <ArtifactAction
                        artifact={textArtifact}
                        captureId={capture.capture.id}
                        onNotify={onNotify}
                        onSaved={setSaveReceipt}
                      >
                        <Download size={14} />
                        {isDesktopApp() ? "保存 .txt" : "下载 .txt"}
                      </ArtifactAction>
                    ) : null}

                    {markdownArtifact ? (
                      <ArtifactAction
                        artifact={markdownArtifact}
                        captureId={capture.capture.id}
                        onNotify={onNotify}
                        onSaved={setSaveReceipt}
                      >
                        <Download size={14} />
                        {isDesktopApp() ? "保存 .md" : "下载 .md"}
                      </ArtifactAction>
                    ) : null}

                    {showCopyAction && isDesktopApp() ? <ProofreadingButton captureId={capture.capture.id} /> : null}
                    {showCopyAction ? (
                      <button
                        className="copy-action is-primary"
                        onClick={() => void handleCopy()}
                        type="button"
                        aria-label="复制全文"
                      >
                        <Copy size={14} />
                        {copyState === "copied"
                          ? "已复制"
                          : copyState === "failed"
                            ? "请手动复制"
                            : "复制全文"}
                      </button>
                    ) : null}
                  </div>
                </div>

                <div
                  className={
                    isLongTextResult
                      ? "result-content-shell is-scrollable"
                      : "result-content-shell"
                  }
                >
                  {hasPodcastPanel && timedSegments.length ? (
                    <TimedTranscript
                      activeTime={playbackTime}
                      onSeek={seekPodcast}
                      segments={timedSegments}
                    />
                  ) : primaryText.trim() ? (
                    <div
                      className={
                        isLongTextResult
                          ? "result-prose result-prose-long"
                          : "result-prose"
                      }
                    >
                      {primaryText
                        .split("\n")
                        .map((paragraph, index) =>
                          paragraph.trim() ? (
                            <p key={`paragraph-${index}`}>{paragraph}</p>
                          ) : (
                            <div className="result-gap" key={`gap-${index}`} />
                          ),
                        )}
                    </div>
                  ) : (
                    <div
                      className={
                        isImageOnlyResult || isVideoOnlyResult
                          ? "result-empty result-empty-supported"
                          : "result-empty"
                      }
                    >
                      {isImageOnlyResult ? (
                        <>
                          <strong>这条图文已整理完成。</strong>
                          <p>
                            右侧可直接查看正文图片，打包下载会在准备完成后自动可用。
                          </p>
                          <p>{`本次共保留 ${imageItems.length} 张正文图片。`}</p>
                        </>
                      ) : isVideoOnlyResult ? (
                        <>
                          <strong>这条视频已整理完成。</strong>
                          <p>右侧可直接预览或下载原视频文件。</p>
                        </>
                      ) : (
                        "这条内容暂未拿到可直接展示的正文，但如果是图文或视频，右侧仍可查看对应素材。"
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {hasVideoPanel && hasPlayableVideo ? (
              <VideoPanel
                key={`${previewVideoArtifact?.download_url || "no-preview"}:${sourceVideoArtifact?.download_url || fallbackVideoArtifact?.download_url || "no-source"}`}
                posterUrl={capture.source.thumbnail_url}
                audioArtifact={sourceAudioArtifact}
                captureId={capture.capture.id}
                onNotify={onNotify}
                onSaved={setSaveReceipt}
                previewArtifact={previewVideoArtifact}
                sourceArtifact={sourceVideoArtifact || fallbackVideoArtifact}
              />
            ) : null}

            {hasPodcastPanel && sourceAudioArtifact ? (
              <PodcastPanel
                audioArtifact={sourceAudioArtifact}
                audioRef={audioRef}
                capture={capture}
                coverArtifact={coverArtifact}
                onNotify={onNotify}
                onSaved={setSaveReceipt}
                onTimeChange={setPlaybackTime}
              />
            ) : null}

            {hasVideoPanel && !hasPlayableVideo ? (
              <VideoUnavailablePanel
                notice={capture.quality.result_notice}
                onRetry={onRetry}
                pending={capture.capture.asset_preparation_pending}
              />
            ) : null}

            {!hasVideoPanel && showImages ? (
              <section className="image-gallery">
                <div className="image-gallery-head">
                  <div className="image-gallery-title">
                    <Images size={16} />
                    <strong>图片</strong>
                  </div>

                  {imagesZip ? (
                    <ArtifactAction
                      artifact={imagesZip}
                      captureId={capture.capture.id}
                      className="copy-action"
                      onNotify={onNotify}
                      onSaved={setSaveReceipt}
                    >
                      <Download size={14} />
                      {isDesktopApp() ? "保存全部图片" : "全部下载"}
                    </ArtifactAction>
                  ) : imagesPending ? (
                    <button className="copy-action" disabled type="button">
                      <Download size={14} />
                      打包准备中
                    </button>
                  ) : null}
                </div>

                <div className="image-gallery-body">
                  {imageItems.length ? (
                    <div className="image-grid">
                      {imageItems.map((item, index) => (
                        <ImageGalleryCard
                          captureId={capture.capture.id}
                          item={item}
                          key={`image-${item.index}`}
                          onNotify={onNotify}
                          onOpen={(trigger) => {
                            const viewerIndex = viewerItems.findIndex(
                              (candidate) => candidate.index === item.index,
                            );
                            if (viewerIndex >= 0) {
                              viewerTriggerRef.current = trigger;
                              setPreviewMode(
                                viewerItems[viewerIndex]?.liveArtifact?.status === "ready"
                                  ? "live"
                                  : "image",
                              );
                              setActiveImageIndex(viewerIndex);
                            }
                          }}
                          onRetry={() => void handleRetryImage(item.index)}
                          onSaved={setSaveReceipt}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="result-empty result-empty-supported">
                      <strong>图片仍在整理中。</strong>
                      <p>结果页已经准备好，图片资源会在后台继续补齐。</p>
                    </div>
                  )}
                </div>
              </section>
            ) : null}
          </div>

          {factsSection}
        </div>

        {saveReceipt ? (
          <div className="save-toast" role="status">
            <div className="save-toast-copy">
              <Save size={15} />
              <span>已保存到下载文件夹</span>
              <strong title={saveReceipt.file_name}>{saveReceipt.file_name}</strong>
            </div>
            <div className="save-toast-actions">
              <button
                onClick={() => void openSavedArtifact(capture.capture.id, saveReceipt.artifactType).catch(() => onNotify("系统暂时无法打开这个文件。"))}
                type="button"
              >
                打开
              </button>
              <button
                onClick={() => void revealSavedArtifact(capture.capture.id, saveReceipt.artifactType).catch(() => onNotify("系统暂时无法定位这个文件。"))}
                type="button"
              >
                显示位置
              </button>
              <button
                aria-label="关闭保存提示"
                className="save-toast-close"
                onClick={() => setSaveReceipt(null)}
                type="button"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        ) : null}

        {activeImageIndex != null && viewerSlides.length ? (
          <Suspense fallback={null}>
            <ImageViewer
              activeIndex={activeImageIndex}
              items={viewerSlides}
              onClose={() => {
                setActiveImageIndex(null);
                window.requestAnimationFrame(() => viewerTriggerRef.current?.focus());
              }}
              onNavigate={setActiveImageIndex}
              onPreviewModeChange={setPreviewMode}
              previewMode={previewMode}
              renderDownloadAction={(artifact, mode) => (
                <ArtifactAction
                  artifact={artifact}
                  captureId={capture.capture.id}
                  className="image-viewer-toolbar-button is-label is-download"
                  onNotify={onNotify}
                  onSaved={setSaveReceipt}
                >
                  <Download size={15} />
                  {isDesktopApp()
                    ? mode === "live" ? "保存 Live" : "保存图片"
                    : mode === "live" ? "下载 Live" : "下载图片"}
                </ArtifactAction>
              )}
            />
          </Suspense>
        ) : null}
      </div>
    </section>
  );
}
