import {
  ChevronLeft,
  ChevronRight,
  Minus,
  Plus,
  RotateCw,
  X,
} from "lucide-react";
import {
  type ZoomRef,
  default as Lightbox,
} from "yet-another-react-lightbox";
import Zoom from "yet-another-react-lightbox/plugins/zoom";
import "yet-another-react-lightbox/styles.css";
import {
  type MouseEvent,
  type ReactNode,
  type SyntheticEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { Artifact } from "../types";

export type ImageViewerMode = "image" | "live";

export type ImageViewerItem = {
  index: number;
  label: string;
  imageSrc: string;
  imageFallbackSrc?: string;
  liveSrc?: string;
  liveFallbackSrc?: string;
  imageArtifact: Artifact | null;
  liveArtifact: Artifact | null;
  width?: number | null;
  height?: number | null;
};

declare module "yet-another-react-lightbox" {
  interface SlideTypes {
    wanxiang: {
      type: "wanxiang";
      src: string;
      item: ImageViewerItem;
    };
  }
}

type WanxiangSlide = {
  type: "wanxiang";
  src: string;
  item: ImageViewerItem;
};

type ImageViewerProps = {
  items: ImageViewerItem[];
  activeIndex: number;
  previewMode: ImageViewerMode;
  onPreviewModeChange: (mode: ImageViewerMode) => void;
  onClose: () => void;
  onNavigate: (nextIndex: number) => void;
  renderDownloadAction: (
    artifact: Artifact,
    mode: ImageViewerMode,
  ) => ReactNode;
};

function stopLightboxGesture(event: SyntheticEvent) {
  // YARL listens for pointer events on the same container to drive swipe
  // navigation.  Custom controls must keep their pointer sequence local;
  // otherwise a real mouse/touch click can be consumed as an empty gesture
  // before React dispatches the button action.
  event.stopPropagation();
}

function handleViewerClick(event: MouseEvent, action: () => void) {
  event.stopPropagation();
  action();
}

function isReducedMotionPreferred() {
  return typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function ViewerImage({
  item,
  rotation,
}: {
  item: ImageViewerItem;
  rotation: number;
}) {
  return (
    <div className="image-viewer-stage">
      <div className="image-viewer-media-frame is-image">
        <div
          className="image-viewer-image-surface"
          style={{ transform: `rotate(${rotation}deg)` }}
        >
          <img
            alt={item.label}
            className="image-viewer-image"
            decoding="async"
            draggable={false}
            height={item.height || undefined}
            src={item.imageSrc}
            width={item.width || undefined}
            onError={(event) => {
              if (
                item.imageFallbackSrc &&
                event.currentTarget.src !== item.imageFallbackSrc
              ) {
                event.currentTarget.src = item.imageFallbackSrc;
              }
            }}
          />
        </div>
      </div>
    </div>
  );
}

function ViewerLive({ item }: { item: ImageViewerItem }) {
  const [source, setSource] = useState(item.liveSrc || "");
  const [playbackFailed, setPlaybackFailed] = useState(false);
  const [videoRatio, setVideoRatio] = useState<number | null>(null);
  const fallbackRatio =
    item.width && item.height ? item.width / item.height : 3 / 4;

  useEffect(() => {
    setSource(item.liveSrc || "");
    setPlaybackFailed(false);
    setVideoRatio(null);
  }, [item.index, item.liveSrc]);

  const mediaRatio = videoRatio || fallbackRatio;

  return (
    <div className="image-viewer-stage">
      <div
        className="image-viewer-media-frame is-live"
        style={{ aspectRatio: `${mediaRatio}` }}
      >
        <video
          autoPlay
          loop
          muted
          playsInline
          poster={item.imageSrc}
          preload="metadata"
          src={source}
          onLoadedMetadata={(event) => {
            const { videoWidth, videoHeight } = event.currentTarget;
            if (videoWidth > 0 && videoHeight > 0) {
              setVideoRatio(videoWidth / videoHeight);
            }
          }}
          onCanPlay={() => setPlaybackFailed(false)}
          onError={() => {
            if (item.liveFallbackSrc && source !== item.liveFallbackSrc) {
              setSource(item.liveFallbackSrc);
              return;
            }
            setPlaybackFailed(true);
          }}
        />
        {playbackFailed ? (
          <p className="image-viewer-note">
            当前浏览器无法直接播放 Live，仍可下载原片段。
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default function ImageViewer({
  items,
  activeIndex,
  previewMode,
  onPreviewModeChange,
  onClose,
  onNavigate,
  renderDownloadAction,
}: ImageViewerProps) {
  const zoomRef = useRef<ZoomRef | null>(null);
  const [zoom, setZoom] = useState(1);
  const [rotationByIndex, setRotationByIndex] = useState<Record<number, number>>(
    {},
  );
  const [reducedMotion, setReducedMotion] = useState(
    isReducedMotionPreferred,
  );

  const slides = useMemo<WanxiangSlide[]>(
    () =>
      items.map((item) => ({
        type: "wanxiang",
        src: item.imageSrc,
        item,
      })),
    [items],
  );
  const activeItem = items[activeIndex];
  const preloadSources = useMemo(
    () =>
      [activeIndex - 1, activeIndex, activeIndex + 1]
        .filter((index) => index >= 0 && index < slides.length)
        .map((index) => slides[index]?.src)
        .filter((src): src is string => Boolean(src)),
    [activeIndex, slides],
  );
  const liveAvailable = Boolean(
    activeItem?.liveSrc && activeItem.liveArtifact?.status === "ready",
  );
  const effectiveMode: ImageViewerMode =
    previewMode === "live" && liveAvailable ? "live" : "image";
  const activeArtifact =
    effectiveMode === "live"
      ? activeItem?.liveArtifact
      : activeItem?.imageArtifact;
  const rotation = activeItem
    ? rotationByIndex[activeItem.index] || 0
    : 0;

  const navigate = (direction: -1 | 1) => {
    if (items.length < 2) return;
    // Keep every explicit control on the same controlled index as the
    // Lightbox view callback. This avoids a ref/controller race in embedded
    // browsers where a nested Lightbox controller can be present but its
    // publish cycle is not reflected by the parent's controlled `index` prop.
    const nextIndex = (activeIndex + direction + items.length) % items.length;
    onNavigate(nextIndex);
  };

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    // The lightbox is a true modal surface: the document behind it must not
    // keep a second scrollbar or move while a pointer gesture targets media.
    const documentElement = document.documentElement;
    const body = document.body;
    const previousHtmlOverflow = documentElement.style.overflow;
    const previousBodyOverflow = body.style.overflow;
    documentElement.style.overflow = "hidden";
    body.style.overflow = "hidden";
    return () => {
      documentElement.style.overflow = previousHtmlOverflow;
      body.style.overflow = previousBodyOverflow;
    };
  }, []);

  useEffect(() => {
    zoomRef.current?.changeZoom(1, true);
    setZoom(1);
  }, [activeIndex, effectiveMode, rotation]);

  useEffect(() => {
    const preloadedImages = preloadSources.map((src, index) => {
      const image = new Image();
      image.decoding = "async";
      image.fetchPriority = index === Math.floor(preloadSources.length / 2) ? "high" : "low";
      image.src = src;
      return image;
    });

    void Promise.all(
      preloadedImages.map((image) =>
        typeof image.decode === "function"
          ? image.decode().catch(() => undefined)
          : Promise.resolve(),
      ),
    );

    return () => {
      preloadedImages.forEach((image) => {
        image.onload = null;
        image.onerror = null;
      });
    };
  }, [preloadSources]);

  if (!activeItem || !slides.length) return null;

  return (
    <Lightbox
      animation={{
        fade: 0,
        swipe: reducedMotion ? 0 : 320,
        // Arrow / keyboard navigation should replace the content in place.
        // Pointer swipes keep their direct-manipulation physics below.
        navigation: reducedMotion ? 0 : 1,
      }}
      carousel={{
        finite: false,
        imageFit: "contain",
        padding: 0,
        preload: 2,
        spacing: "8%",
      }}
      controller={{
        closeOnBackdropClick: true,
        closeOnPullDown: false,
        closeOnPullUp: false,
      }}
      close={onClose}
      index={activeIndex}
      labels={{ Close: "关闭图片预览", Next: "查看下一张图片", Previous: "查看上一张图片" }}
      open
      plugins={[Zoom]}
      portal={{
        container: {
          "aria-label": "图片查看层",
          className: "image-viewer-overlay wanxiang-image-viewer",
        },
      }}
      render={{
        buttonClose: () => null,
        buttonNext: () => null,
        buttonPrev: () => null,
        controls: () => (
          <>
            <button
              aria-label="关闭图片预览"
              className="image-viewer-close"
              onClick={(event) => handleViewerClick(event, onClose)}
              onPointerDown={stopLightboxGesture}
              type="button"
            >
              <X size={20} />
            </button>

            {liveAvailable ? (
              <span className="image-viewer-live-badge">LIVE</span>
            ) : null}

            {items.length > 1 ? (
              <>
                <button
                  aria-label="查看上一张图片"
                  className="image-viewer-nav is-prev"
                  onClick={(event) => handleViewerClick(event, () => navigate(-1))}
                  onPointerDown={stopLightboxGesture}
                  type="button"
                >
                  <ChevronLeft size={18} />
                </button>
                <button
                  aria-label="查看下一张图片"
                  className="image-viewer-nav is-next"
                  onClick={(event) => handleViewerClick(event, () => navigate(1))}
                  onPointerDown={stopLightboxGesture}
                  type="button"
                >
                  <ChevronRight size={18} />
                </button>
              </>
            ) : null}

            <div className="image-viewer-toolbar">
              {liveAvailable ? (
                <div className="image-viewer-toolbar-group">
                  <button
                    className={
                      effectiveMode === "image"
                        ? "image-viewer-toolbar-button is-label is-active"
                        : "image-viewer-toolbar-button is-label"
                    }
                    onClick={(event) =>
                      handleViewerClick(event, () => onPreviewModeChange("image"))
                    }
                    onPointerDown={stopLightboxGesture}
                    type="button"
                  >
                    图片
                  </button>
                  <button
                    className={
                      effectiveMode === "live"
                        ? "image-viewer-toolbar-button is-label is-active"
                        : "image-viewer-toolbar-button is-label"
                    }
                    onClick={(event) =>
                      handleViewerClick(event, () => onPreviewModeChange("live"))
                    }
                    onPointerDown={stopLightboxGesture}
                    type="button"
                  >
                    Live
                  </button>
                  <span className="image-viewer-toolbar-divider" />
                </div>
              ) : null}

              <div className="image-viewer-toolbar-group is-navigation">
                {items.length > 1 ? (
                  <button
                    aria-label="上一张"
                    className="image-viewer-toolbar-button"
                    onClick={(event) => handleViewerClick(event, () => navigate(-1))}
                    onPointerDown={stopLightboxGesture}
                    type="button"
                  >
                    <ChevronLeft size={16} />
                  </button>
                ) : null}
                <span className="image-viewer-toolbar-count">{`${activeIndex + 1}/${items.length}`}</span>
                {items.length > 1 ? (
                  <button
                    aria-label="下一张"
                    className="image-viewer-toolbar-button"
                    onClick={(event) => handleViewerClick(event, () => navigate(1))}
                    onPointerDown={stopLightboxGesture}
                    type="button"
                  >
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
                    disabled={zoom <= 1.001}
                    onClick={(event) =>
                      handleViewerClick(event, () => zoomRef.current?.zoomOut())
                    }
                    onPointerDown={stopLightboxGesture}
                    type="button"
                  >
                    <Minus size={16} />
                  </button>
                  <span className="image-viewer-toolbar-value">{`${Math.round(zoom * 100)}%`}</span>
                  <button
                    aria-label="放大图片"
                    className="image-viewer-toolbar-button"
                    onClick={(event) =>
                      handleViewerClick(event, () => zoomRef.current?.zoomIn())
                    }
                    onPointerDown={stopLightboxGesture}
                    type="button"
                  >
                    <Plus size={16} />
                  </button>
                  <button
                    aria-label="旋转图片"
                    className="image-viewer-toolbar-button"
                    onClick={(event) =>
                      handleViewerClick(event, () =>
                        setRotationByIndex((current) => ({
                          ...current,
                          [activeItem.index]:
                            ((current[activeItem.index] || 0) + 90) % 360,
                        })),
                      )
                    }
                    onPointerDown={stopLightboxGesture}
                    type="button"
                  >
                    <RotateCw size={16} />
                  </button>
                  <span className="image-viewer-toolbar-divider" />
                </div>
              ) : null}

              {activeArtifact?.status === "ready" ? (
                <div className="image-viewer-toolbar-group">
                  {renderDownloadAction(activeArtifact, effectiveMode)}
                </div>
              ) : null}
            </div>
          </>
        ),
        slide: ({ slide, offset }) => {
          const viewerSlide = slide as WanxiangSlide;
          if (
            offset === 0 &&
            effectiveMode === "live" &&
            viewerSlide.item.index === activeItem.index
          ) {
            return <ViewerLive item={viewerSlide.item} />;
          }
          return (
            <ViewerImage
              item={viewerSlide.item}
              rotation={rotationByIndex[viewerSlide.item.index] || 0}
            />
          );
        },
      }}
      slides={slides}
      toolbar={{ buttons: [] }}
      zoom={{
        doubleClickMaxStops: 2,
        maxZoom: effectiveMode === "live" ? 1 : 3.5,
        pinchZoomV4: true,
        ref: zoomRef,
        scrollToZoom: false,
        supports: ["wanxiang"],
        zoomInMultiplier: 1.5,
      }}
      on={{
        view: ({ index }) => {
          if (index !== activeIndex) onNavigate(index);
        },
        zoom: ({ zoom: nextZoom }) => setZoom(nextZoom),
      }}
    />
  );
}
