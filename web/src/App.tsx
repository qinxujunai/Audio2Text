import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  captureEventsUrl,
  clearCompletedCaptures,
  calmErrorMessage,
  calmPublicCopy,
  deleteCapture,
  createCaptureFromFile,
  createCaptureFromInput,
  getCaptureWithRetry,
  getConfig,
  installRuntimePack,
  listCaptures,
  listRuntimePacks,
  openBrowserSession,
  openPlatformSession,
  refreshBrowserSession,
  restartDesktopRuntime,
  revealDiagnosticsFolder,
  retryCapture,
} from "./api";
import { DeliverableStage } from "./components/DeliverableStage";
import { Header } from "./components/Header";
import { InputStage } from "./components/InputStage";
import { ProcessingStage } from "./components/ProcessingStage";
import { RuntimeSetup } from "./components/RuntimeSetup";
import { LocalSettingsButton } from "./components/LocalTools";
import { isDesktopApp } from "./desktop";
import type { CaptureEnvelope, CaptureListItem, ConfigResponse, RuntimePack } from "./types";

type AppPhase = "idle" | "processing" | "done" | "attention";
const PLATFORM_SESSION_REASON_CODES = new Set([
  "browser_challenge_required",
  "browser_session_expired",
  "browser_network_risk",
  "douyin_fresh_cookies_required",
]);
const CONTENT_UNAVAILABLE_REASON_CODES = new Set([
  "source_not_found",
  "source_access_restricted",
  "youtube_video_unavailable",
  "bilibili_video_invalid",
  "xiaohongshu_placeholder_page",
]);
const TRANSIENT_CONNECTION_REASON_CODES = new Set([
  "source_fetch_failed",
  "browser_timeout",
  "browser_request_failed",
  "retry_exhausted",
]);

function isTransientConnectionReason(reason: string) {
  return (
    TRANSIENT_CONNECTION_REASON_CODES.has(reason) ||
    reason.endsWith("_network_failed") ||
    reason.endsWith("_server_unavailable") ||
    reason.endsWith("_timeout")
  );
}

function isRateLimitedReason(reason: string) {
  return reason.endsWith("_rate_limited");
}

function isRecoverableUserActionCapture(capture: CaptureEnvelope | null) {
  if (!capture) return false;
  const status = capture.capture.status;
  if (status === "needs_user_action") return true;
  // Older captures were persisted as `failed` before recoverable states were
  // introduced. Keep retryable terminal work on the calm attention surface;
  // only known permanent content conditions remain a terminal failure.
  return status === "failed";
}
type PendingRemoval = {
  token: string;
  item: CaptureListItem;
};
type PendingHistoryClear = {
  token: string;
  captures: CaptureListItem[];
  currentCapture: CaptureEnvelope | null;
  removedCount: number;
};

type RequestError = Error & {
  status?: number;
};

function stageLabel(stage?: string | null) {
  return (
    {
      queued: "\u7B49\u5F85\u5F00\u59CB",
      resolve: "\u8BC6\u522B\u6765\u6E90",
      extract: "\u63D0\u53D6\u5185\u5BB9",
      transcribe: "\u8F6C\u5199\u97F3\u9891",
      compose: "\u6574\u7406\u7ED3\u679C",
      completed: "\u5904\u7406\u5B8C\u6210",
      processing: "\u5904\u7406\u4E2D",
      failed: "需要处理",
      limit: "\u8D85\u51FA\u65F6\u957F\u9650\u5236",
    }[stage || ""] || "\u5904\u7406\u4E2D"
  );
}

function processingDescription(capture: CaptureEnvelope | null) {
  const stage = capture?.capture.current_stage || capture?.capture.status || "";
  if (stage === "resolve")
    return "\u6B63\u5728\u8BC6\u522B\u6765\u6E90\u548C\u5185\u5BB9\u7C7B\u578B\u3002";
  if (stage === "extract")
    return "\u6B63\u5728\u63D0\u53D6\u53EF\u4EA4\u4ED8\u7684\u6B63\u6587\u3001\u5B57\u5E55\u3001\u5A92\u4F53\u6216\u56FE\u7247\u3002";
  if (stage === "transcribe")
    return "\u6B63\u5728\u8F6C\u5199\u97F3\u9891\u5185\u5BB9\uFF0C\u8BF7\u4FDD\u6301\u5F53\u524D\u9875\u9762\u3002";
  if (stage === "compose")
    return "\u6B63\u5728\u6574\u7406\u6700\u7EC8\u7ED3\u679C\u3002";
  return "\u8BF7\u4FDD\u6301\u5F53\u524D\u9875\u9762\uFF0C\u6211\u4EEC\u4F1A\u81EA\u52A8\u5B8C\u6210\u6574\u7406\u3002";
}

function errorMessageForCapture(
  capture: CaptureEnvelope | null,
  workspaceError: string | null,
) {
  if (workspaceError) return calmPublicCopy(workspaceError);

  const backendMessage = capture?.capture.error_message?.trim() || "";
  if (
    backendMessage &&
    !/Traceback|Exception|FileNotFound|https?:\/\/|HTTP\s+Error|KeyError|yt[-_ ]?dlp|ERROR\s*:/i.test(
      backendMessage,
    )
  ) {
    return calmPublicCopy(backendMessage);
  }

  const stage = capture?.capture.error_stage || "";
  if (stage === "limit") {
    return "\u8FD9\u6761\u5185\u5BB9\u8D85\u51FA\u4E86\u5F53\u524D\u5728\u7EBF\u4F53\u9A8C\u7684\u5904\u7406\u8303\u56F4\u3002\u684C\u9762\u7248\u4E0D\u9650\u5236\u672C\u5730\u5904\u7406\u65F6\u957F\u3002";
  }
  if (stage === "input") {
    return "\u8FD9\u6761\u5185\u5BB9\u5F53\u524D\u4E0D\u53EF\u7528\u3002\u4F60\u53EF\u4EE5\u6362\u4E00\u4E2A\u516C\u5F00\u94FE\u63A5\uFF0C\u6216\u91CD\u65B0\u4E0A\u4F20\u6587\u4EF6\u518D\u8BD5\u3002";
  }
  if (stage === "extract") {
    return "\u6765\u6E90\u8BC6\u522B\u6210\u529F\uFF0C\u4F46\u6CA1\u6709\u7A33\u5B9A\u62FF\u5230\u6B63\u6587\u3001\u5B57\u5E55\u3001\u5A92\u4F53\u6216\u56FE\u7247\u7ED3\u679C\u3002\u5EFA\u8BAE\u6362\u4E00\u6761\u5185\u5BB9\u518D\u8BD5\u3002";
  }
  if (stage === "transcribe") {
    return "\u5A92\u4F53\u5DF2\u7ECF\u62FF\u5230\u4E86\uFF0C\u6587\u5B57\u8FD8\u5728\u51C6\u5907\u4E2D\u3002\u5EFA\u8BAE\u7A0D\u540E\u91CD\u8BD5\uFF0C\u6216\u6362\u4E00\u4E2A\u66F4\u6E05\u6670\u7684\u97F3\u89C6\u9891\u6587\u4EF6\u3002";
  }
  if (stage === "internal") {
    return "\u5904\u7406\u8FC7\u7A0B\u4E2D\u53D1\u751F\u4E86\u5185\u90E8\u95EE\u9898\u3002\u8BF7\u8FD4\u56DE\u9996\u9875\uFF0C\u6362\u4E00\u6761\u5185\u5BB9\u91CD\u65B0\u8BD5\u8BD5\u3002";
  }
  return "\u8FD9\u6761\u5185\u5BB9\u6682\u65F6\u8FD8\u6CA1\u6709\u5B8C\u6210\u3002\u8BF7\u8FD4\u56DE\u9996\u9875\uFF0C\u6362\u4E00\u4E2A\u94FE\u63A5\u6216\u6587\u4EF6\u518D\u8BD5\u3002";
}

function processingFailureEyebrow(reason: string) {
  if (CONTENT_UNAVAILABLE_REASON_CODES.has(reason)) return "内容暂不可用";
  if (isTransientConnectionReason(reason)) return "连接暂时不稳定";
  if (isRateLimitedReason(reason)) return "来源请求较多";
  if (reason === "transcribe_runtime_unavailable") return "转写组件未准备好";
  if (reason === "transcribe_failed") return "转写未完成";
  if (reason === "download_failed") return "资源未准备好";
  return "暂时未完成";
}

function processingFailureTitle(reason: string) {
  if (CONTENT_UNAVAILABLE_REASON_CODES.has(reason)) return "这条内容暂时不可用";
  if (isTransientConnectionReason(reason)) return "暂时连不上来源";
  if (isRateLimitedReason(reason)) return "来源正在稍后响应";
  if (reason === "transcribe_runtime_unavailable") return "转写组件还没有准备好";
  if (reason === "transcribe_failed") return "文字还没有准备好";
  if (reason === "download_failed") return "资源还没有准备好";
  if (reason === "internal_error") return "处理暂时中断";
  return "这条内容暂时没有完成";
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number) {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => {
      window.setTimeout(() => reject(new Error("startup_request_timeout")), timeoutMs);
    }),
  ]);
}

function safeWorkspaceError(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message.trim() : "";
  if (!message) return fallback;
  if (
    /Capture not found|HTTP\s+\d+|NetworkError|Failed to fetch|fetch failed|TypeError|SyntaxError/i.test(
      message,
    )
  ) {
    return fallback;
  }
  return calmPublicCopy(message, fallback);
}

async function reloadIfFrontendAssetIsStale(): Promise<boolean> {
  const currentScript = [...document.scripts]
    .map((script) => script.src)
    .find((src) => /\/assets\/index-[^/]+\.js$/.test(src));
  if (!currentScript) return false;

  try {
    const response = await fetch(`/?ui_build_probe=${Date.now()}`, {
      cache: "no-store",
      headers: { "X-Wanxiang-Build-Probe": "1" },
    });
    if (!response.ok) return false;
    const html = await response.text();
    const latestMatch = html.match(/\/assets\/index-[^"']+\.js/);
    if (!latestMatch) return false;

    const currentPath = new URL(currentScript, window.location.origin).pathname;
    if (currentPath === latestMatch[0]) return false;

    window.location.reload();
    return true;
  } catch {
    // A build probe is a freshness hint only. Never block a usable app when
    // an offline desktop runtime or a proxy temporarily rejects the probe.
    return false;
  }
}

export default function App() {
  const desktopApp = isDesktopApp();
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [captures, setCaptures] = useState<CaptureListItem[]>([]);
  const [currentCapture, setCurrentCapture] = useState<CaptureEnvelope | null>(
    null,
  );
  const [input, setInput] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [loadingDeepLink, setLoadingDeepLink] = useState(false);
  const [toast, setToast] = useState("");
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [runtimePacks, setRuntimePacks] = useState<RuntimePack[]>([]);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runtimeError, setRuntimeError] = useState("");
  const [pendingRemovals, setPendingRemovals] = useState<PendingRemoval[]>([]);
  const [pendingHistoryClear, setPendingHistoryClear] =
    useState<PendingHistoryClear | null>(null);
  const initialPathRef = useRef(window.location.pathname);
  const pendingRemovalTimersRef = useRef<Record<string, number>>({});
  const pendingHistoryClearTimerRef = useRef<number | null>(null);

  const phase: AppPhase = useMemo(() => {
    if (workspaceError) return "attention";
    if (bootstrapping) return "processing";
    if (submitting && !currentCapture) return "processing";
    if (!currentCapture) return "idle";
    if (["queued", "processing"].includes(currentCapture.capture.status))
      return "processing";
    if (isRecoverableUserActionCapture(currentCapture)) return "attention";
    // A terminal source/runtime problem is always an actionable attention
    // state in the product surface. The backend keeps the reason code for
    // diagnostics; the user should never land on a dead-end failure page.
    if (currentCapture.capture.status === "failed") return "attention";
    if (currentCapture.capture.status === "done") return "done";
    return "idle";
  }, [bootstrapping, currentCapture, submitting, workspaceError]);
  const showAppHeader = !desktopApp;

  const inputSubtitle = useMemo(() => {
    if (selectedFile) {
      return "\u9009\u597D\u6587\u4EF6\u540E\u5373\u53EF\u76F4\u63A5\u5F00\u59CB\u5904\u7406\u3002";
    }
    return "\u652F\u6301 \u54D4\u54E9\u54D4\u54E9\u3001\u5C0F\u5B87\u5B99\u3001\u6296\u97F3\u3001\u5C0F\u7EA2\u4E66\u3001\u5FAE\u4FE1\u516C\u4F17\u53F7\u56FE\u6587\uFF0C\u4E5F\u652F\u6301\u672C\u5730\u97F3\u89C6\u9891\u6587\u4EF6\u3002";
  }, [selectedFile]);

  useEffect(() => {
    const match = initialPathRef.current.match(/^\/c\/([^/]+)$/);
    const bootstrap = async () => {
      if (await reloadIfFrontendAssetIsStale()) return;
      const configRequest = getConfig();
      const historyRequest = listCaptures();

      try {
        const runtimeConfig = await withTimeout(configRequest, 8000);
        setConfig(runtimeConfig);
        if (runtimeConfig.runtime_target === "windows_desktop") {
          try {
            const runtime = await listRuntimePacks();
            setRuntimePacks(
              runtime.packs.filter((item) => item.required && !item.installed),
            );
          } catch {
            setRuntimeError("本地识别组件暂时无法读取，请重新打开应用后再试。");
          }
        }
      } catch {
        setWorkspaceError("本地处理服务没有准备好，请重新打开应用后再试。");
      }

      try {
        const history = await withTimeout(historyRequest, 1800);
        setCaptures(history.items || []);
      } catch {
        // History is optional during startup; capture submission remains available.
      }

      if (match?.[1]) {
        setLoadingDeepLink(true);
        try {
          await openCapture(match[1]);
        } catch (error) {
          setWorkspaceError(
            safeWorkspaceError(
              error,
              "这条内容暂时无法恢复，请返回首页重新处理。",
            ),
          );
        } finally {
          setLoadingDeepLink(false);
        }
      }
      setBootstrapping(false);
    };

    void bootstrap();
  }, []);

  useEffect(() => {
    // A tab can remain open across a local restart or a deployment.  Hashed
    // assets protect new navigations, but an already-running SPA otherwise
    // keeps the old component tree forever.  Probe the no-store HTML shell at
    // a short visible cadence so stale processing UI cannot survive into a new
    // build, while hidden tabs do not keep polling.
    const probeWhenVisible = () => {
      if (document.visibilityState === "visible") {
        void reloadIfFrontendAssetIsStale();
      }
    };
    const freshnessTimer = window.setInterval(probeWhenVisible, 15_000);
    document.addEventListener("visibilitychange", probeWhenVisible);
    return () => {
      window.clearInterval(freshnessTimer);
      document.removeEventListener("visibilitychange", probeWhenVisible);
    };
  }, []);

  async function prepareDesktopRuntime() {
    if (!runtimePacks.length || runtimeBusy) return;
    setRuntimeBusy(true);
    setRuntimeError("");
    try {
      for (const pack of runtimePacks) {
        await installRuntimePack(pack.id);
      }
      await restartDesktopRuntime();
    } catch (error) {
      setRuntimeError(calmErrorMessage(error, "安装没有完成，请检查网络后重试。"));
      setRuntimeBusy(false);
    }
  }

  async function retryCurrentCapture() {
    if (!currentCapture || submitting) return;
    setSubmitting(true);
    setWorkspaceError(null);
    try {
      await retryCapture(currentCapture.capture.id);
      const nextCapture = await getCaptureWithRetry(currentCapture.capture.id);
      setCurrentCapture(nextCapture);
    } catch (error) {
      setToast(calmErrorMessage(error, "暂时无法重新处理，请稍后再试。"));
    } finally {
      setSubmitting(false);
    }
  }

  async function restartWorkspaceRuntime() {
    setWorkspaceError(null);
    setBootstrapping(true);
    try {
      await restartDesktopRuntime();
    } catch (error) {
      setBootstrapping(false);
      setWorkspaceError(calmErrorMessage(error, "本地处理服务没有启动成功。"));
    }
  }

  async function refreshPlatformSession() {
    const platform = currentCapture?.source.platform;
    if (platform !== "xiaohongshu" && platform !== "douyin") return;
    if (!currentCapture || submitting) return;
    setSubmitting(true);
    setWorkspaceError(null);
    try {
      await refreshBrowserSession();
      await retryCapture(currentCapture.capture.id);
      setCurrentCapture(await getCaptureWithRetry(currentCapture.capture.id));
      setToast("浏览器会话已刷新，正在重新处理。");
    } catch (error) {
      setToast(calmErrorMessage(error, "会话刷新没有完成，请稍后再试。"));
    } finally {
      setSubmitting(false);
    }
  }

  async function openPlatformVerification() {
    const platform = currentCapture?.source.platform;
    if (platform !== "xiaohongshu" && platform !== "douyin") return;
    try {
      if (isDesktopApp()) {
        await openPlatformSession(platform);
      } else {
        await openBrowserSession(currentCapture?.source.canonical_url);
      }
      setToast("平台页面已打开。完成验证后，返回这里重新处理。");
    } catch {
      setToast("暂时无法打开平台页面，请重新启动应用后再试。");
    }
  }

  useEffect(() => {
    if (bootstrapping) return;
    if (!currentCapture) {
      window.history.replaceState({}, "", "/");
      return;
    }
    window.history.replaceState({}, "", `/c/${currentCapture.capture.id}`);
  }, [bootstrapping, currentCapture]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(
    () => () => {
      Object.values(pendingRemovalTimersRef.current).forEach((timerId) =>
        window.clearTimeout(timerId),
      );
      pendingRemovalTimersRef.current = {};
      if (pendingHistoryClearTimerRef.current) {
        window.clearTimeout(pendingHistoryClearTimerRef.current);
        pendingHistoryClearTimerRef.current = null;
      }
    },
    [],
  );

  useEffect(() => {
    if (!config) return;
    document.title = `${config.product_name} \u00B7 ${config.product_feature_name}`;
  }, [config]);

  useEffect(() => {
    const className = "has-result-stage";
    if (phase === "done") {
      document.body.classList.add(className);
    } else {
      document.body.classList.remove(className);
    }
    return () => document.body.classList.remove(className);
  }, [phase]);

  useEffect(() => {
    const hasBackgroundTask =
      currentCapture !== null &&
      ["queued", "processing"].includes(currentCapture.capture.status);
    void import("@tauri-apps/api/core")
      .then(({ invoke }) =>
        invoke("set_background_task_active", { active: hasBackgroundTask }),
      )
      .catch(() => undefined);
  }, [currentCapture]);

  useEffect(() => {
    const shouldTrackCapture =
      !!currentCapture &&
      (["queued", "processing"].includes(currentCapture.capture.status) ||
        (currentCapture.capture.status === "done" &&
          currentCapture.capture.asset_preparation_pending));
    if (!currentCapture || !shouldTrackCapture) return undefined;

    let active = true;
    let pollInFlight = false;
    const source = new EventSource(captureEventsUrl(currentCapture.capture.id));

    const syncCapture = async () => {
      if (!active || pollInFlight) return;
      pollInFlight = true;
      try {
        const nextCapture = await getCaptureWithRetry(currentCapture.capture.id, 2);
        if (!active) return;
        setCurrentCapture(nextCapture);
        if (
          ["failed", "needs_user_action"].includes(nextCapture.capture.status) ||
          (nextCapture.capture.status === "done" &&
            !nextCapture.capture.asset_preparation_pending)
        ) {
          source.close();
          void refreshHistory();
        }
      } catch {
        // Keep the last known state and let the next poll retry.
      } finally {
        pollInFlight = false;
      }
    };

    source.addEventListener("capture.updated", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as {
          payload: CaptureEnvelope;
        };
        const nextCapture = payload.payload;
        if (!nextCapture?.capture) return;
        setCurrentCapture(nextCapture);
        if (
          ["failed", "needs_user_action"].includes(nextCapture.capture.status) ||
          (nextCapture.capture.status === "done" &&
            !nextCapture.capture.asset_preparation_pending)
        ) {
          source.close();
          void refreshHistory().catch(() => undefined);
        }
      } catch {
        // A malformed or truncated SSE frame must not tear down the workspace;
        // the normal poll will fetch the last complete capture envelope.
        void syncCapture();
      }
    });
    source.onerror = () => {
      source.close();
      void syncCapture();
    };

    const pollTimer = window.setInterval(() => {
      void syncCapture();
    }, 3000);

    return () => {
      active = false;
      source.close();
      window.clearInterval(pollTimer);
    };
  }, [
    currentCapture?.capture.id,
    currentCapture?.capture.status,
    currentCapture?.capture.asset_preparation_pending,
  ]);

  function requestStatus(error: unknown) {
    if (!(error instanceof Error)) return undefined;
    return (error as RequestError).status;
  }

  async function refreshHistory() {
    const history = await listCaptures();
    const nextItems = history.items || [];
    setCaptures(nextItems);
    return nextItems;
  }

  async function finalizePendingRemoval(removal: PendingRemoval) {
    delete pendingRemovalTimersRef.current[removal.token];
    setPendingRemovals((items) =>
      items.filter((item) => item.token !== removal.token),
    );
    try {
      await deleteCapture(removal.item.id);
      setCaptures((items) =>
        items.filter((item) => item.id !== removal.item.id),
      );
      await refreshHistory();
    } catch (error) {
      setToast(calmErrorMessage(error, "暂时无法删除这条记录，请稍后再试。"));
    }
  }

  function undoPendingRemoval(token: string) {
    const timerId = pendingRemovalTimersRef.current[token];
    if (timerId) {
      window.clearTimeout(timerId);
      delete pendingRemovalTimersRef.current[token];
    }
    setPendingRemovals((items) => items.filter((item) => item.token !== token));
  }

  async function finalizePendingHistoryClear(pending: PendingHistoryClear) {
    if (pendingHistoryClearTimerRef.current) {
      window.clearTimeout(pendingHistoryClearTimerRef.current);
      pendingHistoryClearTimerRef.current = null;
    }
    setPendingHistoryClear(null);
    setClearBusy(true);
    try {
      const result = await clearCompletedCaptures();
      await refreshHistory();
      if (
        pending.currentCapture &&
        ["done", "failed", "needs_user_action"].includes(pending.currentCapture.capture.status)
      ) {
        resetWorkspace();
      }
      setToast(`\u5DF2\u6E05\u7A7A ${result.cleared} \u6761\u8BB0\u5F55\u3002`);
    } catch (error) {
      setCaptures(pending.captures);
      setCurrentCapture(pending.currentCapture);
      setToast(
        calmErrorMessage(error, "暂时无法清空记录，请稍后再试。"),
      );
    } finally {
      setClearBusy(false);
    }
  }

  function undoPendingHistoryClear() {
    if (!pendingHistoryClear) return;
    if (pendingHistoryClearTimerRef.current) {
      window.clearTimeout(pendingHistoryClearTimerRef.current);
      pendingHistoryClearTimerRef.current = null;
    }
    setCaptures(pendingHistoryClear.captures);
    setCurrentCapture(pendingHistoryClear.currentCapture);
    setPendingHistoryClear(null);
  }

  function resetWorkspace() {
    setWorkspaceError(null);
    setCurrentCapture(null);
    setSelectedFile(null);
    setInput("");
    window.history.replaceState({}, "", "/");
  }

  async function openCapture(captureId: string) {
    setWorkspaceError(null);
    const capture = await getCaptureWithRetry(captureId);
    setCurrentCapture(capture);
  }

  async function pasteFromClipboard() {
    let text = "";

    // Method 1: navigator.clipboard.readText()
    if (navigator.clipboard?.readText) {
      try {
        text = (await navigator.clipboard.readText()).trim();
      } catch {}
    }

    if (text) {
      setInput(text);
      setToast("已从剪贴板粘贴。");
      return;
    }

    // Method 2: navigator.clipboard.read()
    if (navigator.clipboard?.read) {
      try {
        const items = await navigator.clipboard.read();
        for (const item of items) {
          if (item.types.includes("text/plain")) {
            const blob = await item.getType("text/plain");
            text = (await blob.text()).trim();
            if (text) break;
          }
        }
      } catch {}
    }

    if (text) {
      setInput(text);
      setToast("已从剪贴板粘贴。");
      return;
    }

    setToast("请长按输入框粘贴链接。");
  }

  async function submitText() {
    if (!input.trim()) {
      setToast("\u5148\u8D34\u5165\u4E00\u4E2A\u516C\u5F00\u94FE\u63A5\u3002");
      return;
    }

    const fullText = input.trim();
    const urlMatch = fullText.match(/https?:\/\/[^\s]+/i);
    const submittedInput = urlMatch ? urlMatch[0] : fullText;
    setSubmitting(true);
    setWorkspaceError(null);
    try {
      const created = await createCaptureFromInput(submittedInput);
      await openCapture(created.capture_id);
      await refreshHistory();
      if (created.reused) {
        setToast("这条内容之前处理过，已直接复用结果。");
      } else if (created.input_warning) {
        setToast(created.input_warning);
      }
    } catch (error) {
      const message = calmErrorMessage(error, "暂时无法创建任务，请稍后再试。");
      const status = requestStatus(error);
      // Input validation and other 4xx responses belong to the form, not the
      // application workspace. Keep the user on the landing surface instead
      // of rendering a misleading processing-failed state.
      if (!status || status >= 500) {
        setWorkspaceError(message);
      }
      setToast(message);
    } finally {
      setSubmitting(false);
    }
  }

  async function submitFile() {
    if (!selectedFile) {
      setToast(
        "\u8BF7\u5148\u9009\u62E9\u4E00\u4E2A\u672C\u5730\u6587\u4EF6\u3002",
      );
      return;
    }

    setSubmitting(true);
    setWorkspaceError(null);
    try {
      const created = await createCaptureFromFile(selectedFile);
      await openCapture(created.capture_id);
      await refreshHistory();
      setSelectedFile(null);
    } catch (error) {
      setToast(
        error instanceof Error
          ? calmPublicCopy(error.message, "文件暂时无法上传，请稍后再试。")
          : "文件暂时无法上传，请稍后再试。",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function clearHistory() {
    if (pendingHistoryClear) return;
    const removableItems = captures.filter((item) =>
      ["done", "failed", "needs_user_action"].includes(item.status),
    );
    if (!removableItems.length) {
      setToast(
        "\u6682\u65F6\u6CA1\u6709\u53EF\u6E05\u7A7A\u7684\u8BB0\u5F55\u3002",
      );
      return;
    }

    Object.values(pendingRemovalTimersRef.current).forEach((timerId) =>
      window.clearTimeout(timerId),
    );
    pendingRemovalTimersRef.current = {};
    setPendingRemovals([]);

    const pending: PendingHistoryClear = {
      token: `history-clear-${Date.now()}`,
      captures,
      currentCapture,
      removedCount: removableItems.length,
    };
    setCaptures((items) =>
      items.filter((item) => !["done", "failed", "needs_user_action"].includes(item.status)),
    );
    setPendingHistoryClear(pending);
    pendingHistoryClearTimerRef.current = window.setTimeout(() => {
      void finalizePendingHistoryClear(pending);
    }, 4000);
  }

  function shouldShowInRecent(item: CaptureListItem) {
    if (["failed", "needs_user_action"].includes(item.status)) return false;
    const title = (item.title || "").trim().toLowerCase();
    const preview = (item.preview_text || "").trim().toLowerCase();
    if (
      item.source_platform === "xiaohongshu" &&
      (/xhslink\.com/.test(title) || /xhslink\.com/.test(preview))
    ) {
      return false;
    }
    return true;
  }

  async function queueCaptureRemoval(item: CaptureListItem) {
    if (pendingRemovals.some((entry) => entry.item.id === item.id)) {
      return;
    }

    const latestHistory = await refreshHistory().catch(() => null);
    const latest = latestHistory?.find((entry) => entry.id === item.id) || null;
    if (!latest) {
      setCaptures((items) => items.filter((entry) => entry.id !== item.id));
      setToast("这条记录已经删除。");
      return;
    }
    if (latest.status === "processing") {
      setToast("这条记录还在处理中，暂时不能删除。");
      return;
    }

    const token = `${latest.id}-${Date.now()}`;
    const removal = { token, item: latest };
    const timerId = window.setTimeout(() => {
      void finalizePendingRemoval(removal);
    }, 4000);

    pendingRemovalTimersRef.current[token] = timerId;
    setPendingRemovals((items) => [...items, removal]);
  }

  const pendingRemovalIds = useMemo(
    () => new Set(pendingRemovals.map((item) => item.item.id)),
    [pendingRemovals],
  );
  const recentCaptures = useMemo(
    () =>
      captures
        .filter(
          (item) => shouldShowInRecent(item) && !pendingRemovalIds.has(item.id),
        )
        .slice(0, config?.capture_history_limit || 12),
    [captures, config?.capture_history_limit, pendingRemovalIds],
  );
  const supportedExtensions = useMemo(
    () =>
      (config?.supported_extensions || []).map((item) =>
        item.replace(/^\./, "").toUpperCase(),
      ),
    [config?.supported_extensions],
  );
  const platformRecoveryReason = currentCapture?.capture.failure_reason_code || "";
  const platformSessionRequired =
    phase === "attention" && PLATFORM_SESSION_REASON_CODES.has(platformRecoveryReason);
  const xiaohongshuFullUrlRequired =
    phase === "attention" && platformRecoveryReason === "xiaohongshu_full_url_required";
  const platformActionLabel =
    platformRecoveryReason === "browser_network_risk"
      ? "打开平台检查访问"
      : platformRecoveryReason === "douyin_fresh_cookies_required"
        ? "打开抖音刷新会话"
        : "打开平台完成验证";
  const platformSessionDescription =
    platformRecoveryReason === "browser_network_risk"
      ? "当前网络被平台暂时判定为高风险。打开平台确认访问状态，必要时切换网络后再回来重新检查。"
      : platformRecoveryReason === "douyin_fresh_cookies_required"
        ? "平台要求更新一次浏览器会话。打开抖音完成页面确认后，回到这里重新检查。"
      : "平台暂时要求确认项目级浏览器会话。打开平台页面完成验证后，回到这里重新检查。";
  const xiaohongshuFullUrlDescription =
    "短链接没有稳定展开。请在小红书打开这条内容，从浏览器地址栏复制完整网页链接，再回到这里粘贴。";
  const processingCopy = {
    eyebrow:
      platformSessionRequired
        ? "需要完成一次验证"
        : xiaohongshuFullUrlRequired
        ? "需要完整链接"
        : phase === "attention"
        ? processingFailureEyebrow(platformRecoveryReason)
        : "\u6B63\u5728\u63D0\u53D6\u5185\u5BB9",
    title:
      platformSessionRequired
        ? "完成平台验证后继续"
        : xiaohongshuFullUrlRequired
        ? "请粘贴小红书完整链接"
        : phase === "attention"
        ? processingFailureTitle(platformRecoveryReason)
        : stageLabel(
            currentCapture?.capture.current_stage ||
              currentCapture?.capture.status,
          ),
    description:
      platformSessionRequired
        ? platformSessionDescription
        : xiaohongshuFullUrlRequired
        ? xiaohongshuFullUrlDescription
        : phase === "attention"
        ? errorMessageForCapture(currentCapture, workspaceError)
        : processingDescription(currentCapture),
  };
  const runtimeSetupRequired =
    !loadingDeepLink &&
    phase === "idle" &&
    config?.runtime_target === "windows_desktop" &&
    runtimePacks.length > 0;

  return (
    <div
      className={
        showAppHeader ? "app-shell" : "app-shell is-native-chrome-only"
      }
    >
      {showAppHeader ? (
        <Header
          featureName={config?.product_feature_name || "\u4E07\u8C61\u6210\u6587"}
          onReset={resetWorkspace}
        />
      ) : null}

      <main className="main-stage">
        <AnimatePresence mode="wait">
          {runtimeSetupRequired ? (
            <motion.div
              key="runtime-setup"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="stage-shell stage-shell-runtime"
            >
              <RuntimeSetup
                packs={runtimePacks}
                busy={runtimeBusy}
                error={runtimeError}
                onInstall={() => void prepareDesktopRuntime()}
              />
            </motion.div>
          ) : phase === "idle" && !loadingDeepLink ? (
            <motion.div
              key="idle"
              initial={false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, ease: "easeInOut" }}
              className="stage-shell stage-shell-idle"
            >
              <InputStage
                settingsControl={config && config.deployment_mode !== "cloud_preview" ? <LocalSettingsButton /> : null}
                input={input}
                subtitle={inputSubtitle}
                selectedFile={selectedFile}
                submitting={submitting || clearBusy}
                supportedExtensions={supportedExtensions}
                maxUploadSizeMb={config?.max_upload_size_mb || 0}
                captures={recentCaptures}
                currentCaptureId={currentCapture?.capture.id}
                onInputChange={setInput}
                onSubmitText={() => void submitText()}
                onPaste={() => void pasteFromClipboard()}
                onSelectFile={setSelectedFile}
                onSubmitFile={() => void submitFile()}
                onOpenCapture={(captureId) => {
                  void openCapture(captureId).catch((error) => {
                    setToast(
                      calmErrorMessage(
                        error,
                        "这条记录暂时无法打开，请稍后重试。",
                      ),
                    );
                  });
                }}
                onClearHistory={() => {
                  if (!clearBusy) void clearHistory();
                }}
                onDeleteCapture={queueCaptureRemoval}
              />
            </motion.div>
          ) : null}

          {(bootstrapping || loadingDeepLink ||
            phase === "processing" ||
            phase === "attention") && (
            <motion.div
              // Keep one stable processing surface for loading, attention, and
              // failure states. Remounting here used to make the indicator
              // restart while the copy changed, which read as a visual jump.
              key="processing-surface"
              initial={false}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35, ease: "easeInOut" }}
              className="stage-shell stage-shell-processing"
            >
              <ProcessingStage
                phase={
                  bootstrapping || loadingDeepLink
                    ? "bootstrapping"
                    : phase === "attention"
                      ? "attention"
                      : "processing"
                }
                eyebrow={
                  bootstrapping
                    ? "正在启动"
                    : loadingDeepLink
                      ? "\u6062\u590D\u5185\u5BB9"
                    : processingCopy.eyebrow
                }
                title={
                  bootstrapping
                    ? "正在准备万象成文"
                    : loadingDeepLink
                      ? "\u6B63\u5728\u6062\u590D\u4E0A\u4E00\u6761\u5185\u5BB9"
                    : processingCopy.title
                }
                description={
                  bootstrapping
                    ? "正在检查本地处理组件和运行环境，准备好后会自动进入首页。"
                    : loadingDeepLink
                      ? "\u5982\u679C\u4F60\u662F\u4ECE\u6DF1\u94FE\u63A5\u8FDB\u5165\uFF0C\u8FD9\u91CC\u4F1A\u76F4\u63A5\u6062\u590D\u5230\u5BF9\u5E94\u5185\u5BB9\u7684\u5F53\u524D\u72B6\u6001\u3002"
                      : processingCopy.description
                }
                stageLabel={
                  phase === "processing"
                    ? stageLabel(
                        currentCapture?.capture.current_stage ||
                          currentCapture?.capture.status,
                      )
                    : undefined
                }
                progressPercent={
                  phase === "processing"
                    ? currentCapture?.capture.progress_percent
                    : undefined
                }
                progressDetail={
                  phase === "processing"
                    ? currentCapture?.capture.progress_detail
                    : undefined
                }
                onReset={
                  phase === "attention" || loadingDeepLink
                    ? resetWorkspace
                    : undefined
                }
                onRetry={
                  phase === "attention" && !workspaceError
                    ? currentCapture?.capture.retryable
                      ? () => void retryCurrentCapture()
                      : undefined
                    : phase === "attention" && workspaceError && isDesktopApp()
                      ? () => void restartWorkspaceRuntime()
                      : undefined
                }
                onRefreshSession={
                  platformSessionRequired
                    ? () => void refreshPlatformSession()
                    : undefined
                }
                onOpenPlatformSession={
                  platformSessionRequired
                    ? () => void openPlatformVerification()
                    : undefined
                }
                platformActionLabel={
                  platformSessionRequired ? platformActionLabel : undefined
                }
                onDiagnostics={
                  phase === "attention" && isDesktopApp()
                    ? () => void revealDiagnosticsFolder().catch(() => setToast("暂时无法打开诊断目录。"))
                    : undefined
                }
              />
            </motion.div>
          )}

          {!loadingDeepLink && phase === "done" && currentCapture ? (
            <motion.div
              key="done"
              // The result surface follows the processing surface in the same
              // layout slot. Keep the handoff opacity-only so content never
              // appears to jump or pop over the spinner.
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.32, ease: "easeOut" }}
              className="stage-shell stage-shell-ready"
            >
              <DeliverableStage
                capture={currentCapture}
                localToolsAvailable={Boolean(config && config.deployment_mode !== "cloud_preview" && (config.runtime_target === "local_web" || config.runtime_target === "windows_desktop"))}
                onCaptureUpdated={setCurrentCapture}
                onNotify={setToast}
                onReset={resetWorkspace}
                onRetry={() => void retryCurrentCapture()}
              />
            </motion.div>
          ) : null}
        </AnimatePresence>
      </main>

      <div className="toast-stack">
        <AnimatePresence>
          {pendingHistoryClear ? (
            <motion.div
              key={pendingHistoryClear.token}
              className="toast is-actionable"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={{ duration: 0.2 }}
            >
              <span>{`\u5DF2\u6E05\u7A7A ${pendingHistoryClear.removedCount} \u6761\u8BB0\u5F55`}</span>
              <button
                className="toast-action"
                onClick={undoPendingHistoryClear}
                type="button"
              >
                撤销
              </button>
            </motion.div>
          ) : null}

          {pendingRemovals.map((removal) => (
            <motion.div
              key={removal.token}
              className="toast is-actionable"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={{ duration: 0.2 }}
            >
              <span>已删除这条记录</span>
              <button
                className="toast-action"
                onClick={() => undoPendingRemoval(removal.token)}
                type="button"
              >
                撤销
              </button>
            </motion.div>
          ))}

          {toast ? (
            <motion.div
              key={`toast-${toast}`}
              className="toast"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={{ duration: 0.2 }}
            >
              {toast}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  );
}
