import {
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  Expand,
  ExternalLink,
  FileText,
  Film,
  Images,
  Minus,
  Play,
  Plus,
  RotateCw,
  X,
} from "lucide-react";
import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type SyntheticEvent,
  type TouchEvent as ReactTouchEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { Artifact, CaptureEnvelope, CaptureSourceImage } from "../types";

type DeliverableStageProps = {
  capture: CaptureEnvelope;
};

type VideoOrientation = "unknown" | "portrait" | "landscape" | "square-ish";
type ImagePreviewMode = "image" | "live";
type ImageGalleryItem = {
  index: number;
  label: string;
  imageSourceUrl: string;
  liveSourceUrl: string | null;
  imageArtifact: Artifact | null;
  liveArtifact: Artifact | null;
};
type ViewerPan = { x: number; y: number };

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
  return `${window.location.origin}${artifact.download_url}`;
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
  if (capture.source.media_kind === "image_article" || capture.source.platform === "wechat_article") {
    return capture.capture.asset_preparation_pending
      ? "正文和图片已可查看，下载文件仍在后台准备。"
      : "正文和图片已整理完成，可直接查看、复制或下载。";
  }
  if (capture.source.media_kind === "video") {
    return hasVideoArtifact
      ? "文字与视频已整理完成，可直接查看、播放或下载。"
      : "文字已整理完成，视频文件暂未成功下载。";
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
  if (capture.source.media_kind === "image_article" || capture.source.platform === "wechat_article") {
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
  return capture.source.media_kind === "image_article" || capture.source.platform === "wechat_article";
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
  const sourceImagesByIndex = new Map(sourceImages.map((item) => [item.index, item]));

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
      } satisfies ImageGalleryItem;
    })
    .filter((item): item is ImageGalleryItem => Boolean(item));
}

function downloadHref(artifact: Artifact | null, fallbackUrl: string | null) {
  return artifact ? artifactUrl(artifact) : (fallbackUrl || "");
}

function preferredImageUrl(item: ImageGalleryItem) {
  return item.imageArtifact ? artifactUrl(item.imageArtifact) : item.imageSourceUrl;
}

function imageFallbackUrl(item: ImageGalleryItem) {
  return item.imageArtifact ? item.imageSourceUrl : "";
}

function preferredLiveUrl(item: ImageGalleryItem) {
  return item.liveArtifact ? artifactUrl(item.liveArtifact) : item.liveSourceUrl || "";
}

function liveFallbackUrl(item: ImageGalleryItem) {
  return item.liveArtifact ? item.liveSourceUrl || "" : "";
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
  onLoad?: (event: SyntheticEvent<HTMLImageElement>) => void;
}) {
  const resolvedPrimary = primarySrc || fallbackSrc || "";
  const resolvedFallback = fallbackSrc || "";
  const [src, setSrc] = useState(resolvedPrimary);

  useEffect(() => {
    setSrc(resolvedPrimary);
  }, [resolvedPrimary]);

  if (!resolvedPrimary) {
    return (
      <div aria-label={alt} className={className ? `${className} is-empty` : "is-empty"} role="img">
        <span>图片准备中</span>
      </div>
    );
  }

  return (
    <img
      alt={alt}
      className={className}
      loading={eager ? "eager" : "lazy"}
      src={src}
      onLoad={onLoad}
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
  onOpen,
}: {
  item: ImageGalleryItem;
  onOpen: () => void;
}) {
  const [showLivePreview, setShowLivePreview] = useState(false);
  const canPreviewLive = Boolean(item.liveSourceUrl || item.liveArtifact);
  const imageDownload = downloadHref(item.imageArtifact, item.imageSourceUrl);

  return (
    <article
      className={showLivePreview ? "image-card is-live-hovered" : "image-card"}
      onMouseEnter={() => {
        if (canPreviewLive) setShowLivePreview(true);
      }}
      onMouseLeave={() => {
        setShowLivePreview(false);
      }}
    >
      <button
        aria-label={`查看图片 ${item.index}`}
        className="image-card-media-button"
        onClick={onOpen}
        type="button"
      >
        {canPreviewLive ? (
          <span className="image-card-badge">
            <Play size={11} />
            LIVE
          </span>
        ) : null}

        {showLivePreview && canPreviewLive ? (
          <LiveHoverPreview item={item} />
        ) : (
          <MediaImage
            alt={item.label}
            className="image-card-media-image"
            fallbackSrc={imageFallbackUrl(item)}
            primarySrc={preferredImageUrl(item)}
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

        <a
          className="image-card-action"
          download={item.imageArtifact?.label || `image_${item.index}.jpg`}
          href={imageDownload}
          onClick={(event) => event.stopPropagation()}
        >
          <Download size={13} />
          下载
        </a>
      </div>
    </article>
  );
}

function ImageViewerOverlay({
  items,
  activeIndex,
  previewMode,
  onPreviewModeChange,
  onClose,
  onNavigate,
}: {
  items: ImageGalleryItem[];
  activeIndex: number;
  previewMode: ImagePreviewMode;
  onPreviewModeChange: (mode: ImagePreviewMode) => void;
  onClose: () => void;
  onNavigate: (nextIndex: number) => void;
}) {
  const activeItem = items[activeIndex];
  const liveAvailable = Boolean(activeItem?.liveSourceUrl || activeItem?.liveArtifact);
  const effectiveMode = liveAvailable ? previewMode : "image";
  const imageDownload = activeItem ? downloadHref(activeItem.imageArtifact, activeItem.imageSourceUrl) : "";
  const liveDownload = activeItem ? downloadHref(activeItem.liveArtifact, activeItem.liveSourceUrl) : "";
  const livePrimary = activeItem ? preferredLiveUrl(activeItem) : "";
  const imagePrimary = activeItem ? preferredImageUrl(activeItem) : "";
  const [liveSrc, setLiveSrc] = useState(livePrimary);
  const [livePlaybackError, setLivePlaybackError] = useState(false);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const imageSurfaceRef = useRef<HTMLDivElement | null>(null);
  const liveVideoRef = useRef<HTMLVideoElement | null>(null);
  const pointerStateRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const touchNavigationRef = useRef<{ x: number; y: number; at: number } | null>(null);
  const wheelNavigationRef = useRef(0);
  const chromeHideTimerRef = useRef<number | null>(null);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [imageNaturalSize, setImageNaturalSize] = useState({ width: 0, height: 0 });
  const [liveNaturalSize, setLiveNaturalSize] = useState({ width: 0, height: 0 });
  const [imageScale, setImageScale] = useState(1);
  const [imageRotation, setImageRotation] = useState(0);
  const [imagePan, setImagePan] = useState<ViewerPan>({ x: 0, y: 0 });
  const [isDraggingImage, setIsDraggingImage] = useState(false);
  const [isChromeVisible, setIsChromeVisible] = useState(false);
  const [navOffsets, setNavOffsets] = useState<{ left: number; right: number } | null>(null);
  const [mediaChrome, setMediaChrome] = useState<{ top: number; left: number; right: number } | null>(null);

  useEffect(() => {
    setLiveSrc(livePrimary);
    setLivePlaybackError(false);
    setLiveNaturalSize({ width: 0, height: 0 });
  }, [livePrimary, activeItem?.index]);

  useEffect(() => {
    if (!viewportRef.current) return;
    const element = viewportRef.current;

    const updateViewportSize = () => {
      setViewportSize({
        width: element.clientWidth,
        height: element.clientHeight,
      });
    };

    updateViewportSize();
    const resizeObserver = new ResizeObserver(() => updateViewportSize());
    resizeObserver.observe(element);
    window.addEventListener("resize", updateViewportSize);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", updateViewportSize);
    };
  }, []);

  useEffect(() => {
    setImagePan({ x: 0, y: 0 });
    setImageRotation(0);
    setIsDraggingImage(false);
    pointerStateRef.current = null;
  }, [activeItem?.index, effectiveMode]);

  useEffect(() => {
    return () => {
      if (chromeHideTimerRef.current) {
        window.clearTimeout(chromeHideTimerRef.current);
      }
    };
  }, []);

  const isImageRotatedSideways = imageRotation % 180 !== 0;
  const imageDisplaySize = {
    width: isImageRotatedSideways ? imageNaturalSize.height : imageNaturalSize.width,
    height: isImageRotatedSideways ? imageNaturalSize.width : imageNaturalSize.height,
  };
  const fitScale =
    imageDisplaySize.width > 0 && imageDisplaySize.height > 0 && viewportSize.width > 0 && viewportSize.height > 0
      ? Math.min(viewportSize.width / imageDisplaySize.width, viewportSize.height / imageDisplaySize.height, 1)
      : 1;
  const isOriginalView = Math.abs(imageScale - 1) < 0.02;
  const scaledImageSize = {
    width: imageDisplaySize.width * imageScale,
    height: imageDisplaySize.height * imageScale,
  };
  const imageCanPan =
    effectiveMode === "image" &&
    viewportSize.width > 0 &&
    viewportSize.height > 0 &&
    (scaledImageSize.width > viewportSize.width + 2 || scaledImageSize.height > viewportSize.height + 2);
  const liveFrameClass =
    liveNaturalSize.width > 0 && liveNaturalSize.height > 0
      ? liveNaturalSize.width >= liveNaturalSize.height
        ? "image-viewer-media-frame is-live is-landscape"
        : "image-viewer-media-frame is-live is-portrait"
      : "image-viewer-media-frame is-live is-portrait";
  const liveFrameStyle =
    liveNaturalSize.width > 0 && liveNaturalSize.height > 0
      ? ({
          "--viewer-live-ratio": `${liveNaturalSize.width} / ${liveNaturalSize.height}`,
        } as CSSProperties)
      : undefined;

  useEffect(() => {
    if (effectiveMode !== "image") return;
    if (!imageNaturalSize.width || !viewportSize.width) return;
    setImageScale(fitScale);
    setImagePan({ x: 0, y: 0 });
  }, [activeItem?.index, effectiveMode, fitScale, imageNaturalSize.height, imageNaturalSize.width, imageRotation, viewportSize.width]);

  function clampImagePanValue(nextPan: ViewerPan, scale = imageScale) {
    if (!viewportSize.width || !viewportSize.height || !imageDisplaySize.width || !imageDisplaySize.height) {
      return { x: 0, y: 0 };
    }
    const maxX = Math.max(0, (imageDisplaySize.width * scale - viewportSize.width) / 2);
    const maxY = Math.max(0, (imageDisplaySize.height * scale - viewportSize.height) / 2);
    return {
      x: clampNumber(nextPan.x, -maxX, maxX),
      y: clampNumber(nextPan.y, -maxY, maxY),
    };
  }

  useEffect(() => {
    setImagePan((current) => clampImagePanValue(current));
  }, [
    imageDisplaySize.height,
    imageDisplaySize.width,
    imageScale,
    imageRotation,
    viewportSize.height,
    viewportSize.width,
  ]);

  function navigateBy(offset: number) {
    if (items.length <= 1) return;
    onNavigate((activeIndex + offset + items.length) % items.length);
  }

  function resetImageView(mode: "fit" | "original") {
    setImagePan({ x: 0, y: 0 });
    setImageScale(mode === "original" ? 1 : fitScale);
  }

  function toggleImageViewSize() {
    resetImageView(isOriginalView ? "fit" : "original");
  }

  function rotateImageView() {
    setImagePan({ x: 0, y: 0 });
    setImageRotation((current) => (current + 90) % 360);
  }

  function showViewerChrome() {
    if (chromeHideTimerRef.current) {
      window.clearTimeout(chromeHideTimerRef.current);
      chromeHideTimerRef.current = null;
    }
    setIsChromeVisible(true);
  }

  function hideViewerChrome(delay = 260) {
    if (chromeHideTimerRef.current) {
      window.clearTimeout(chromeHideTimerRef.current);
    }
    chromeHideTimerRef.current = window.setTimeout(() => {
      setIsChromeVisible(false);
      chromeHideTimerRef.current = null;
    }, delay);
  }

  function updateNavOffsets() {
    const overlay = overlayRef.current;
    const mediaElement = effectiveMode === "live" ? liveVideoRef.current : imageSurfaceRef.current;
    if (!overlay || !mediaElement) {
      setNavOffsets(null);
      setMediaChrome(null);
      return;
    }

    const overlayRect = overlay.getBoundingClientRect();
    const mediaRect = mediaElement.getBoundingClientRect();
    if (!mediaRect.width || !mediaRect.height) {
      setNavOffsets(null);
      setMediaChrome(null);
      return;
    }

    const inset = 14;
    const minEdge = 18;
    const maxLeft = Math.max(minEdge, overlayRect.width - 64);
    const left = clampNumber(mediaRect.left - overlayRect.left + inset, minEdge, maxLeft);
    const right = clampNumber(overlayRect.right - mediaRect.right + inset, minEdge, maxLeft);
    const top = clampNumber(mediaRect.top - overlayRect.top + inset, minEdge, Math.max(minEdge, overlayRect.height - 64));
    setNavOffsets((current) => (current?.left === left && current?.right === right ? current : { left, right }));
    setMediaChrome((current) =>
      current?.top === top && current?.left === left && current?.right === right ? current : { top, left, right },
    );
  }

  useEffect(() => {
    const frame = window.requestAnimationFrame(updateNavOffsets);
    window.addEventListener("resize", updateNavOffsets);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updateNavOffsets);
    };
  }, [
    activeIndex,
    effectiveMode,
    imagePan.x,
    imagePan.y,
    imageScale,
    liveNaturalSize.height,
    liveNaturalSize.width,
    viewportSize.height,
    viewportSize.width,
  ]);

  function handleImagePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (effectiveMode !== "image") return;
    if (!imageCanPan) return;
    pointerStateRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsDraggingImage(true);
  }

  function handleImagePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (!pointerStateRef.current || pointerStateRef.current.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - pointerStateRef.current.x;
    const deltaY = event.clientY - pointerStateRef.current.y;
    pointerStateRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    setImagePan((current) => clampImagePanValue({ x: current.x + deltaX, y: current.y + deltaY }));
  }

  function clearImagePointer(event?: ReactPointerEvent<HTMLDivElement>) {
    if (event && pointerStateRef.current && pointerStateRef.current.pointerId === event.pointerId) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    pointerStateRef.current = null;
    setIsDraggingImage(false);
  }

  function handleViewerWheel(event: React.WheelEvent<HTMLDivElement>) {
    if (items.length <= 1) return;
    const mediaElement = effectiveMode === "live" ? liveVideoRef.current : imageSurfaceRef.current;
    if (mediaElement) {
      const rect = mediaElement.getBoundingClientRect();
      const withinMedia =
        event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!withinMedia) return;
    }
    const dominantDelta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
    if (Math.abs(dominantDelta) < 2) return;
    event.preventDefault();
    const now = window.performance.now();
    if (now - wheelNavigationRef.current < 320) return;
    wheelNavigationRef.current = now;
    navigateBy(dominantDelta > 0 ? 1 : -1);
  }

  function handleViewerTouchStart(event: ReactTouchEvent<HTMLDivElement>) {
    if (event.touches.length !== 1) {
      touchNavigationRef.current = null;
      return;
    }
    const touch = event.touches[0];
    touchNavigationRef.current = { x: touch.clientX, y: touch.clientY, at: window.performance.now() };
  }

  function handleViewerTouchEnd(event: ReactTouchEvent<HTMLDivElement>) {
    const start = touchNavigationRef.current;
    touchNavigationRef.current = null;
    if (!start || items.length <= 1 || imageCanPan) return;
    const touch = event.changedTouches[0];
    if (!touch) return;
    const deltaX = touch.clientX - start.x;
    const deltaY = touch.clientY - start.y;
    if (Math.abs(deltaX) < 52 || Math.abs(deltaX) < Math.abs(deltaY) * 1.35) return;
    navigateBy(deltaX < 0 ? 1 : -1);
  }

  if (!activeItem) return null;

  return (
    <div
      aria-label="图片查看层"
      className={isChromeVisible ? "image-viewer-overlay is-chrome-visible" : "image-viewer-overlay"}
      onClick={onClose}
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      onTouchStart={handleViewerTouchStart}
      onTouchEnd={handleViewerTouchEnd}
    >
      <button
        aria-label="关闭图片预览"
        className="image-viewer-close"
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
        type="button"
      >
        <X size={20} />
      </button>

      {liveAvailable ? (
        <span
          className="image-viewer-live-badge"
          style={mediaChrome ? { top: mediaChrome.top, left: mediaChrome.left } : undefined}
        >
          LIVE
        </span>
      ) : null}

      {items.length > 1 ? (
        <>
          <button
            aria-label="查看上一张图片"
            className="image-viewer-nav is-prev"
            style={navOffsets ? { left: navOffsets.left } : undefined}
            onPointerEnter={showViewerChrome}
            onPointerLeave={() => hideViewerChrome()}
            onClick={(event) => {
              event.stopPropagation();
              navigateBy(-1);
            }}
            type="button"
          >
            <ChevronLeft size={18} />
          </button>
          <button
            aria-label="查看下一张图片"
            className="image-viewer-nav is-next"
            style={navOffsets ? { right: navOffsets.right } : undefined}
            onPointerEnter={showViewerChrome}
            onPointerLeave={() => hideViewerChrome()}
            onClick={(event) => {
              event.stopPropagation();
              navigateBy(1);
            }}
            type="button"
          >
            <ChevronRight size={18} />
          </button>
        </>
      ) : null}

      <div className="image-viewer-stage">
        <div
          className={[
            "image-viewer-viewport",
            imageCanPan ? "can-pan" : "",
            isDraggingImage ? "is-dragging" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          ref={viewportRef}
          onPointerDown={handleImagePointerDown}
          onPointerMove={handleImagePointerMove}
          onPointerUp={clearImagePointer}
          onPointerCancel={clearImagePointer}
          onPointerLeave={(event) => {
            if (pointerStateRef.current) clearImagePointer(event);
          }}
          onWheel={handleViewerWheel}
        >
          {effectiveMode === "live" && liveAvailable ? (
            <div
              className={liveFrameClass}
              key={`live-${activeItem.index}`}
              style={liveFrameStyle}
              onClick={(event) => event.stopPropagation()}
              onPointerEnter={showViewerChrome}
              onPointerLeave={() => hideViewerChrome()}
            >
              <video
                autoPlay
                loop
                muted
                playsInline
                poster={imagePrimary}
                preload="metadata"
                ref={liveVideoRef}
                src={liveSrc}
                onCanPlay={() => setLivePlaybackError(false)}
                onLoadedMetadata={(event) => {
                  setLiveNaturalSize({
                    width: event.currentTarget.videoWidth,
                    height: event.currentTarget.videoHeight,
                  });
                  window.requestAnimationFrame(updateNavOffsets);
                }}
                onError={() => {
                  const fallback = liveFallbackUrl(activeItem);
                  if (fallback && liveSrc !== fallback) {
                    setLiveSrc(fallback);
                    return;
                  }
                  setLivePlaybackError(true);
                }}
              />
          {livePlaybackError ? <p className="image-viewer-note">当前浏览器无法直接播放 Live，仍可下载原片段。</p> : null}
            </div>
          ) : (
            <div className="image-viewer-media-frame is-image">
              <div
                className="image-viewer-image-surface"
                key={`image-${activeItem.index}`}
                ref={imageSurfaceRef}
                onClick={(event) => event.stopPropagation()}
                onPointerEnter={showViewerChrome}
                onPointerLeave={() => hideViewerChrome()}
                style={
                  {
                    transform: `translate(${imagePan.x}px, ${imagePan.y}px) rotate(${imageRotation}deg) scale(${imageScale})`,
                  } satisfies CSSProperties
                }
              >
                <MediaImage
                  alt={activeItem.label}
                  className="image-viewer-image"
                  eager
                  fallbackSrc={imageFallbackUrl(activeItem)}
                  primarySrc={imagePrimary}
                  onLoad={(event: SyntheticEvent<HTMLImageElement>) => {
                    setImageNaturalSize({
                      width: event.currentTarget.naturalWidth,
                      height: event.currentTarget.naturalHeight,
                    });
                    window.requestAnimationFrame(updateNavOffsets);
                  }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      <div
        className="image-viewer-toolbar"
        onClick={(event) => event.stopPropagation()}
        onPointerEnter={showViewerChrome}
        onPointerLeave={() => hideViewerChrome(320)}
      >
        {liveAvailable ? (
          <div className="image-viewer-toolbar-group">
            <button
              className={previewMode === "image" ? "image-viewer-toolbar-button is-label is-active" : "image-viewer-toolbar-button is-label"}
              onClick={() => onPreviewModeChange("image")}
              type="button"
            >
              图片
            </button>
            <button
              className={previewMode === "live" ? "image-viewer-toolbar-button is-label is-active" : "image-viewer-toolbar-button is-label"}
              onClick={() => onPreviewModeChange("live")}
              type="button"
            >
              Live
            </button>
            <span className="image-viewer-toolbar-divider" />
          </div>
        ) : null}

        <div className="image-viewer-toolbar-group is-navigation">
          {items.length > 1 ? (
            <button aria-label="上一张" className="image-viewer-toolbar-button" onClick={() => navigateBy(-1)} type="button">
              <ChevronLeft size={16} />
            </button>
          ) : null}
          <span className="image-viewer-toolbar-count">{`${activeIndex + 1}/${items.length}`}</span>
          {items.length > 1 ? (
            <button aria-label="下一张" className="image-viewer-toolbar-button" onClick={() => navigateBy(1)} type="button">
              <ChevronRight size={16} />
            </button>
          ) : null}
          <span className="image-viewer-toolbar-divider" />
        </div>

        {effectiveMode === "image" ? (
          <div className="image-viewer-toolbar-group is-image-tools">
            <button
              aria-label="缩小图片"
              className="image-viewer-toolbar-button"
              onClick={() =>
                setImageScale((current) => clampNumber(Number((current - 0.14).toFixed(2)), Math.min(fitScale, 0.35), 3.5))
              }
              type="button"
            >
              <Minus size={16} />
            </button>
            <span className="image-viewer-toolbar-value">{`${Math.round(imageScale * 100)}%`}</span>
            <button
              aria-label="放大图片"
              className="image-viewer-toolbar-button"
              onClick={() => setImageScale((current) => clampNumber(Number((current + 0.14).toFixed(2)), Math.min(fitScale, 0.35), 3.5))}
              type="button"
            >
              <Plus size={16} />
            </button>
            <button
              aria-label={isOriginalView ? "适应屏幕显示" : "按原始尺寸显示"}
              className="image-viewer-toolbar-button is-label"
              onClick={toggleImageViewSize}
              type="button"
            >
              {isOriginalView ? "适应" : "1:1"}
            </button>
            <button aria-label="旋转图片" className="image-viewer-toolbar-button" onClick={rotateImageView} type="button">
              <RotateCw size={16} />
            </button>
            <span className="image-viewer-toolbar-divider" />
          </div>
        ) : null}

        <div className="image-viewer-toolbar-group">
          <a
            className="image-viewer-toolbar-button is-label is-download"
            download={
              effectiveMode === "live"
                ? activeItem.liveArtifact?.label || `image_${activeItem.index}_live.mp4`
                : activeItem.imageArtifact?.label || `image_${activeItem.index}.jpg`
            }
            href={effectiveMode === "live" ? liveDownload : imageDownload}
          >
            <Download size={15} />
            {effectiveMode === "live" ? "下载 Live" : "下载图片"}
          </a>
        </div>
      </div>
    </div>
  );
}

function VideoPanel({
  previewArtifact,
  sourceArtifact,
  posterUrl,
}: {
  previewArtifact: Artifact | null;
  sourceArtifact: Artifact | null;
  posterUrl?: string | null;
}) {
  const [orientation, setOrientation] = useState<VideoOrientation>("unknown");
  const [playbackError, setPlaybackError] = useState(false);
  const [aspectRatio, setAspectRatio] = useState<string>("");
  const [preferPreview, setPreferPreview] = useState(Boolean(previewArtifact));
  const activeArtifact = (preferPreview ? previewArtifact : sourceArtifact) || sourceArtifact || previewArtifact;
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
    ? ({ ["--video-aspect-ratio" as "--video-aspect-ratio"]: aspectRatio } as CSSProperties)
    : undefined;

  if (!activeArtifact || !downloadArtifact) {
    return null;
  }

  return (
    <section className="media-panel media-panel-playable" data-orientation={orientation} style={panelStyle}>
      <div className="media-panel-head">
        <div>
          <span className="result-section-kicker">视频</span>
          <strong>预览</strong>
        </div>

        <div className="media-panel-actions">
          <a className="copy-action" download href={artifactUrl(downloadArtifact)}>
            <Download size={14} />
            下载视频
          </a>
          {fileSize ? <span className="media-size-pill">{fileSize}</span> : null}
        </div>
      </div>

      <div className="media-panel-body">
        <div className="video-shell">
          <video
            controls
            preload="auto"
            playsInline
            poster={posterUrl || undefined}
            src={artifactUrl(activeArtifact)}
            onCanPlay={() => setPlaybackError(false)}
            onError={() => {
              if (preferPreview && previewArtifact && sourceArtifact && previewArtifact.download_url !== sourceArtifact.download_url) {
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

      {playbackError ? <p className="media-panel-note">当前浏览器无法直接内联预览，仍可使用上方按钮下载原视频文件。</p> : null}
    </section>
  );
}

function VideoUnavailablePanel({ notice }: { notice: string }) {
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
          <p>{notice || "文本已整理完成，但原视频暂未成功下载。"}</p>
        </div>
      </div>
    </section>
  );
}

export function DeliverableStage({ capture }: DeliverableStageProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [activeImageIndex, setActiveImageIndex] = useState<number | null>(null);
  const [previewMode, setPreviewMode] = useState<ImagePreviewMode>("image");
  const primaryText = capture.result.views.primary || capture.result.primary_text || "";
  const markdownText = capture.result.views.markdown || "";
  const copyPayload = markdownText.trim() || primaryText;
  const isLongTextResult = primaryText.trim().length >= 900 || primaryText.split("\n").filter((item) => item.trim()).length >= 8;
  const showImages = shouldShowImages(capture);
  const imageItems = useMemo(() => buildGalleryItems(capture), [capture]);
  const imagesZip = useMemo(
    () => (showImages ? capture.artifacts.find((item) => item.type === "images_zip") || null : null),
    [capture.artifacts, showImages],
  );
  const textArtifact = useMemo(() => capture.artifacts.find((item) => item.type === "txt") || null, [capture.artifacts]);
  const markdownArtifact = useMemo(() => capture.artifacts.find((item) => item.type === "md") || null, [capture.artifacts]);
  const previewVideoArtifact = useMemo(
    () => (capture.source.media_kind === "video" ? capture.artifacts.find((item) => item.type === "preview_media") || null : null),
    [capture.artifacts, capture.source.media_kind],
  );
  const sourceVideoArtifact = useMemo(
    () => (capture.source.media_kind === "video" ? capture.artifacts.find((item) => item.type === "source_media") || null : null),
    [capture.artifacts, capture.source.media_kind],
  );
  const fallbackVideoArtifact = useMemo(
    () =>
      capture.source.media_kind === "video"
        ? capture.artifacts.find((item) => item.mime_type.startsWith("video/")) || null
        : null,
    [capture.artifacts, capture.source.media_kind],
  );
  const videoArtifact = previewVideoArtifact || sourceVideoArtifact || fallbackVideoArtifact;
  const visibleFacts = useMemo(
    () =>
      capture.result.content_facts.filter((fact) =>
        ["title", "platform", "content_type", "author", "published_at", "duration"].includes(fact.key),
      ),
    [capture.result.content_facts],
  );
  const hasVideoPanel = capture.source.media_kind === "video";
  const hasPlayableVideo = Boolean(videoArtifact);
  const sourceUrl = resultSourceUrl(capture);
  const isImageOnlyResult = showImages && !primaryText.trim();
  const isVideoOnlyResult = hasPlayableVideo && !primaryText.trim();
  const heroHeading = heroTitle(capture);
  const showCopyAction = Boolean(copyPayload.trim()) && !isImageOnlyResult;
  const imagesPending = showImages && capture.capture.asset_preparation_pending;
  const mainGridClass = hasVideoPanel
    ? "deliverable-workspace has-video"
    : showImages
      ? "deliverable-workspace has-images"
      : "deliverable-workspace";

  async function handleCopy() {
    if (!copyPayload.trim()) return;
    const ok = await copyText(copyPayload);
    setCopyState(ok ? "copied" : "failed");
    window.setTimeout(() => setCopyState("idle"), 2200);
  }

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
            <a className="fact-chip fact-chip-action" href={sourceUrl} rel="noreferrer" target="_blank">
              <span>来源</span>
              <strong>查看来源</strong>
              <ExternalLink size={13} />
            </a>
          ) : null}
        </div>
      ) : null}
    </section>
  ) : null;

  useEffect(() => {
    if (activeImageIndex == null) return;
    if (!imageItems.length || activeImageIndex >= imageItems.length) {
      setActiveImageIndex(null);
    }
  }, [activeImageIndex, imageItems.length]);

  useEffect(() => {
    if (activeImageIndex == null) return;
    const activeItem = imageItems[activeImageIndex];
    const hasLive = Boolean(activeItem?.liveSourceUrl || activeItem?.liveArtifact);
    setPreviewMode(hasLive ? "live" : "image");
  }, [activeImageIndex, imageItems]);

  useEffect(() => {
    if (activeImageIndex == null) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [activeImageIndex]);

  useEffect(() => {
    if (activeImageIndex == null) return;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setActiveImageIndex(null);
        return;
      }
      if (imageItems.length <= 1) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setActiveImageIndex((current) => (current == null ? current : (current - 1 + imageItems.length) % imageItems.length));
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setActiveImageIndex((current) => (current == null ? current : (current + 1) % imageItems.length));
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeImageIndex, imageItems.length]);

  return (
    <section className="deliverable-stage">
      <div className="deliverable-stage-inner">
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
            {capture.source.duration_seconds ? <span>{durationLabel(capture.source.duration_seconds)}</span> : null}
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
                    {textArtifact ? (
                      <a className="copy-action" download href={artifactUrl(textArtifact)}>
                        <Download size={14} />
                        下载 .txt
                      </a>
                    ) : null}

                    {markdownArtifact ? (
                      <a className="copy-action" download href={artifactUrl(markdownArtifact)}>
                        <Download size={14} />
                        下载 .md
                      </a>
                    ) : null}

                    {showCopyAction ? (
                      <button className="copy-action is-primary" onClick={() => void handleCopy()} type="button" aria-label="复制全文">
                        <Copy size={14} />
                        {copyState === "copied" ? "已复制" : copyState === "failed" ? "请手动复制" : "复制全文"}
                      </button>
                    ) : null}
                  </div>
                </div>

                <div className={isLongTextResult ? "result-content-shell is-scrollable" : "result-content-shell"}>
                  {primaryText.trim() ? (
                    <div className={isLongTextResult ? "result-prose result-prose-long" : "result-prose"}>
                      {primaryText.split("\n").map((paragraph, index) =>
                        paragraph.trim() ? <p key={`paragraph-${index}`}>{paragraph}</p> : <div className="result-gap" key={`gap-${index}`} />,
                      )}
                    </div>
                  ) : (
                    <div className={isImageOnlyResult || isVideoOnlyResult ? "result-empty result-empty-supported" : "result-empty"}>
                      {isImageOnlyResult ? (
                        <>
                          <strong>这条图文已整理完成。</strong>
                          <p>右侧可直接查看正文图片，打包下载会在准备完成后自动可用。</p>
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
                previewArtifact={previewVideoArtifact}
                sourceArtifact={sourceVideoArtifact || fallbackVideoArtifact}
              />
            ) : null}

            {hasVideoPanel && !hasPlayableVideo ? <VideoUnavailablePanel notice={capture.quality.result_notice} /> : null}

            {!hasVideoPanel && showImages ? (
              <section className="image-gallery">
                <div className="image-gallery-head">
                  <div className="image-gallery-title">
                    <Images size={16} />
                    <strong>图片</strong>
                  </div>

                  {imagesZip ? (
                    <a className="copy-action" download href={artifactUrl(imagesZip)}>
                      <Download size={14} />
                      全部下载
                    </a>
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
                        <ImageGalleryCard item={item} key={`image-${item.index}`} onOpen={() => setActiveImageIndex(index)} />
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

        {activeImageIndex != null && imageItems.length ? (
          <ImageViewerOverlay
            activeIndex={activeImageIndex}
            items={imageItems}
            onClose={() => setActiveImageIndex(null)}
            onNavigate={setActiveImageIndex}
            onPreviewModeChange={setPreviewMode}
            previewMode={previewMode}
          />
        ) : null}
      </div>
    </section>
  );
}
