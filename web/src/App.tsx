import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  captureEventsUrl,
  clearCompletedCaptures,
  deleteCapture,
  createCaptureFromFile,
  createCaptureFromInput,
  getCapture,
  getCaptureWithRetry,
  getConfig,
  listCaptures,
} from "./api";
import { DeliverableStage } from "./components/DeliverableStage";
import { Header } from "./components/Header";
import { InputStage } from "./components/InputStage";
import { ProcessingStage } from "./components/ProcessingStage";
import type { CaptureEnvelope, CaptureListItem, ConfigResponse } from "./types";

type AppPhase = "idle" | "processing" | "done" | "failed";
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
      failed: "\u5904\u7406\u5931\u8D25",
      limit: "\u8D85\u51FA\u65F6\u957F\u9650\u5236",
    }[stage || ""] || "\u5904\u7406\u4E2D"
  );
}

function processingDescription(capture: CaptureEnvelope | null) {
  const stage = capture?.capture.current_stage || capture?.capture.status || "";
  if (stage === "resolve") return "\u6B63\u5728\u8BC6\u522B\u6765\u6E90\u548C\u5185\u5BB9\u7C7B\u578B\u3002";
  if (stage === "extract") return "\u6B63\u5728\u63D0\u53D6\u53EF\u4EA4\u4ED8\u7684\u6B63\u6587\u3001\u5B57\u5E55\u3001\u5A92\u4F53\u6216\u56FE\u7247\u3002";
  if (stage === "transcribe") return "\u6B63\u5728\u8F6C\u5199\u97F3\u9891\u5185\u5BB9\uFF0C\u8BF7\u4FDD\u6301\u5F53\u524D\u9875\u9762\u3002";
  if (stage === "compose") return "\u6B63\u5728\u6574\u7406\u6700\u7EC8\u7ED3\u679C\u3002";
  return "\u8BF7\u4FDD\u6301\u5F53\u524D\u9875\u9762\uFF0C\u6211\u4EEC\u4F1A\u81EA\u52A8\u5B8C\u6210\u6574\u7406\u3002";
}

function errorMessageForCapture(capture: CaptureEnvelope | null, workspaceError: string | null) {
  if (workspaceError) return workspaceError;

  const backendMessage = capture?.capture.error_message?.trim() || "";
  if (backendMessage) {
    return backendMessage;
  }

  const stage = capture?.capture.error_stage || "";
  if (stage === "limit") {
    return "\u5F53\u524D\u514D\u8D39\u7248\u4EC5\u652F\u6301 30 \u5206\u949F\u4EE5\u5185\u7684\u97F3\u9891\u6216\u89C6\u9891\uFF0C\u8BF7\u6362\u4E00\u6761\u66F4\u77ED\u7684\u5185\u5BB9\u518D\u8BD5\u3002";
  }
  if (stage === "input") {
    return "\u8FD9\u6761\u5185\u5BB9\u5F53\u524D\u4E0D\u53EF\u7528\u3002\u4F60\u53EF\u4EE5\u6362\u4E00\u4E2A\u516C\u5F00\u94FE\u63A5\uFF0C\u6216\u91CD\u65B0\u4E0A\u4F20\u6587\u4EF6\u518D\u8BD5\u3002";
  }
  if (stage === "extract") {
    return "\u6765\u6E90\u8BC6\u522B\u6210\u529F\uFF0C\u4F46\u6CA1\u6709\u7A33\u5B9A\u62FF\u5230\u6B63\u6587\u3001\u5B57\u5E55\u3001\u5A92\u4F53\u6216\u56FE\u7247\u7ED3\u679C\u3002\u5EFA\u8BAE\u6362\u4E00\u6761\u5185\u5BB9\u518D\u8BD5\u3002";
  }
  if (stage === "transcribe") {
    return "\u5A92\u4F53\u5DF2\u7ECF\u62FF\u5230\u4E86\uFF0C\u4F46\u8F6C\u5199\u6CA1\u6709\u6210\u529F\u3002\u5EFA\u8BAE\u7A0D\u540E\u91CD\u8BD5\uFF0C\u6216\u6362\u4E00\u4E2A\u66F4\u6E05\u6670\u7684\u97F3\u89C6\u9891\u6587\u4EF6\u3002";
  }
  if (stage === "internal") {
    return "\u5904\u7406\u8FC7\u7A0B\u4E2D\u53D1\u751F\u4E86\u5185\u90E8\u95EE\u9898\u3002\u8BF7\u8FD4\u56DE\u9996\u9875\uFF0C\u6362\u4E00\u6761\u5185\u5BB9\u91CD\u65B0\u8BD5\u8BD5\u3002";
  }
  return "\u8FD9\u6761\u5185\u5BB9\u6682\u65F6\u6CA1\u80FD\u5904\u7406\u6210\u529F\u3002\u8BF7\u8FD4\u56DE\u9996\u9875\uFF0C\u6362\u4E00\u4E2A\u94FE\u63A5\u6216\u6587\u4EF6\u518D\u8BD5\u3002";
}

export default function App() {
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [captures, setCaptures] = useState<CaptureListItem[]>([]);
  const [currentCapture, setCurrentCapture] = useState<CaptureEnvelope | null>(null);
  const [input, setInput] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(false);
  const [loadingDeepLink, setLoadingDeepLink] = useState(false);
  const [toast, setToast] = useState("");
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [pendingRemovals, setPendingRemovals] = useState<PendingRemoval[]>([]);
  const [pendingHistoryClear, setPendingHistoryClear] = useState<PendingHistoryClear | null>(null);
  const initialPathRef = useRef(window.location.pathname);
  const pendingRemovalTimersRef = useRef<Record<string, number>>({});
  const pendingHistoryClearTimerRef = useRef<number | null>(null);

  const phase: AppPhase = useMemo(() => {
    if (workspaceError) return "failed";
    if (submitting && !currentCapture) return "processing";
    if (!currentCapture) return "idle";
    if (["queued", "processing"].includes(currentCapture.capture.status)) return "processing";
    if (currentCapture.capture.status === "failed") return "failed";
    if (currentCapture.capture.status === "done") return "done";
    return "idle";
  }, [currentCapture, submitting, workspaceError]);

  const inputSubtitle = useMemo(() => {
    if (selectedFile) {
      return "\u9009\u597D\u6587\u4EF6\u540E\u5373\u53EF\u76F4\u63A5\u5F00\u59CB\u5904\u7406\u3002";
    }
    return "\u652F\u6301 YouTube\u3001\u54D4\u54E9\u54D4\u54E9\u3001\u5C0F\u5B87\u5B99\u3001\u6296\u97F3\u3001\u5C0F\u7EA2\u4E66\u3001\u5FAE\u4FE1\u516C\u4F17\u53F7\u56FE\u6587\uFF0C\u4E5F\u652F\u6301\u672C\u5730\u97F3\u89C6\u9891\u6587\u4EF6\u3002";
  }, [selectedFile]);

  useEffect(() => {
    const match = initialPathRef.current.match(/^\/c\/([^/]+)$/);
    const bootstrap = async () => {
      // Load config and history in background \u2014 don't block the UI
      try {
        const [runtimeConfig, history] = await Promise.all([getConfig(), listCaptures()]);
        setConfig(runtimeConfig);
        setCaptures(history.items || []);
      } catch {
        // Config/history load failed \u2014 UI still works with defaults
      }

      if (match?.[1]) {
        setLoadingDeepLink(true);
        try {
          await openCapture(match[1]);
        } finally {
          setLoadingDeepLink(false);
        }
      }
    };

    void bootstrap();
  }, []);

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
      Object.values(pendingRemovalTimersRef.current).forEach((timerId) => window.clearTimeout(timerId));
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
    const shouldTrackCapture =
      !!currentCapture &&
      (["queued", "processing"].includes(currentCapture.capture.status) ||
        (currentCapture.capture.status === "done" && currentCapture.capture.asset_preparation_pending));
    if (!currentCapture || !shouldTrackCapture) return undefined;

    let active = true;
    let pollInFlight = false;
    const source = new EventSource(captureEventsUrl(currentCapture.capture.id));

    const syncCapture = async () => {
      if (!active || pollInFlight) return;
      pollInFlight = true;
      try {
        const nextCapture = await getCapture(currentCapture.capture.id);
        if (!active) return;
        setCurrentCapture(nextCapture);
        if (
          nextCapture.capture.status === "failed" ||
          (nextCapture.capture.status === "done" && !nextCapture.capture.asset_preparation_pending)
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
      const payload = JSON.parse((event as MessageEvent).data) as { payload: CaptureEnvelope };
      const nextCapture = payload.payload;
      setCurrentCapture(nextCapture);
      if (
        nextCapture.capture.status === "failed" ||
        (nextCapture.capture.status === "done" && !nextCapture.capture.asset_preparation_pending)
      ) {
        source.close();
        void refreshHistory();
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
  }, [currentCapture?.capture.id, currentCapture?.capture.status, currentCapture?.capture.asset_preparation_pending]);

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
    setPendingRemovals((items) => items.filter((item) => item.token !== removal.token));
    try {
      await deleteCapture(removal.item.id);
      setCaptures((items) => items.filter((item) => item.id !== removal.item.id));
      await refreshHistory();
    } catch (error) {
      setToast(error instanceof Error ? error.message : "删除记录失败。");
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
      if (pending.currentCapture && ["done", "failed"].includes(pending.currentCapture.capture.status)) {
        resetWorkspace();
      }
      setToast(`\u5DF2\u6E05\u7A7A ${result.cleared} \u6761\u8BB0\u5F55\u3002`);
    } catch (error) {
      setCaptures(pending.captures);
      setCurrentCapture(pending.currentCapture);
      setToast(error instanceof Error ? error.message : "\u6E05\u7A7A\u8BB0\u5F55\u5931\u8D25\u3002");
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
    // Method 1: designMode + contentEditable + execCommand
    // designMode='on' signals the browser to enable paste from system clipboard
    let text = "";
    try {
      const prevMode = document.designMode;
      document.designMode = "on";
      const div = document.createElement("div");
      div.contentEditable = "true";
      div.style.cssText = "position:fixed;top:0;left:-9999px;opacity:0;width:1px;height:1px;";
      document.body.appendChild(div);
      div.focus();
      document.execCommand("selectAll", false);
      document.execCommand("delete", false);
      const ok = document.execCommand("paste");
      if (ok) text = (div.innerText || "").replace(/ /g, " ").trim();
      document.body.removeChild(div);
      document.designMode = prevMode;
    } catch {
      document.designMode = "off";
    }

    if (text) {
      setInput(text);
      setToast("已从剪贴板粘贴。");
      return;
    }

    // Method 2: navigator.clipboard.readText() — standard API
    if (navigator.clipboard?.readText) {
      try {
        text = (await navigator.clipboard.readText()).trim();
        if (text) {
          setInput(text);
          setToast("已从剪贴板粘贴。");
          return;
        }
      } catch {
        // Fall through
      }
    }

    // Method 3: navigator.clipboard.read() — newer API
    if (navigator.clipboard?.read) {
      try {
        const items = await navigator.clipboard.read();
        for (const item of items) {
          if (item.types.includes("text/plain")) {
            const blob = await item.getType("text/plain");
            text = (await blob.text()).trim();
            if (text) {
              setInput(text);
              setToast("已从剪贴板粘贴。");
              return;
            }
          }
        }
      } catch {
        // Fall through
      }
    }

    // Method 4: Nothing worked — guide user
    const ta = document.querySelector("textarea");
    if (ta) (ta as HTMLTextAreaElement).focus();
    setToast("请在输入框中粘贴链接。");
  }

  async function submitText() {
    if (!input.trim()) {
      setToast("\u5148\u8D34\u5165\u4E00\u4E2A\u516C\u5F00\u94FE\u63A5\u3002");
      return;
    }

    const submittedInput = input.trim();
    setSubmitting(true);
    setWorkspaceError(null);
    setInput("");
    try {
      const created = await createCaptureFromInput(submittedInput);
      await openCapture(created.capture_id);
      await refreshHistory();
      if (created.reused) {
        setToast("\u8FD9\u6761\u5185\u5BB9\u4E4B\u524D\u5904\u7406\u8FC7\uFF0C\u5DF2\u76F4\u63A5\u590D\u7528\u7ED3\u679C\u3002");
      } else if (created.input_warning) {
        setToast(created.input_warning);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "\u521B\u5EFA\u4EFB\u52A1\u5931\u8D25\u3002";
      setWorkspaceError(message);
      setInput(submittedInput);
      setToast(message);
    } finally {
      setSubmitting(false);
    }
  }

  async function submitFile() {
    if (!selectedFile) {
      setToast("\u8BF7\u5148\u9009\u62E9\u4E00\u4E2A\u672C\u5730\u6587\u4EF6\u3002");
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
      setToast(error instanceof Error ? error.message : "\u4E0A\u4F20\u6587\u4EF6\u5931\u8D25\u3002");
    } finally {
      setSubmitting(false);
    }
  }

  async function clearHistory() {
    if (pendingHistoryClear) return;
    const removableItems = captures.filter((item) => ["done", "failed"].includes(item.status));
    if (!removableItems.length) {
      setToast("\u6682\u65F6\u6CA1\u6709\u53EF\u6E05\u7A7A\u7684\u8BB0\u5F55\u3002");
      return;
    }

    Object.values(pendingRemovalTimersRef.current).forEach((timerId) => window.clearTimeout(timerId));
    pendingRemovalTimersRef.current = {};
    setPendingRemovals([]);

    const pending: PendingHistoryClear = {
      token: `history-clear-${Date.now()}`,
      captures,
      currentCapture,
      removedCount: removableItems.length,
    };
    setCaptures((items) => items.filter((item) => !["done", "failed"].includes(item.status)));
    setPendingHistoryClear(pending);
    pendingHistoryClearTimerRef.current = window.setTimeout(() => {
      void finalizePendingHistoryClear(pending);
    }, 4000);
  }

  function shouldShowInRecent(item: CaptureListItem) {
    if (item.status === "failed") return false;
    const title = (item.title || "").trim().toLowerCase();
    const preview = (item.preview_text || "").trim().toLowerCase();
    if (item.source_platform === "xiaohongshu" && (/xhslink\.com/.test(title) || /xhslink\.com/.test(preview))) {
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

  const headerStatus = loadingDeepLink ? "processing" : phase;
  const pendingRemovalIds = useMemo(() => new Set(pendingRemovals.map((item) => item.item.id)), [pendingRemovals]);
  const recentCaptures = useMemo(
    () =>
      captures
        .filter((item) => shouldShowInRecent(item) && !pendingRemovalIds.has(item.id))
        .slice(0, config?.capture_history_limit || 12),
    [captures, config?.capture_history_limit, pendingRemovalIds],
  );
  const supportedExtensions = useMemo(
    () => (config?.supported_extensions || []).map((item) => item.replace(/^\./, "").toUpperCase()),
    [config?.supported_extensions],
  );
  const processingCopy = {
    eyebrow: phase === "failed" ? "\u5904\u7406\u5931\u8D25" : "\u6B63\u5728\u63D0\u53D6\u5185\u5BB9",
    title:
      phase === "failed"
        ? "\u8FD9\u6761\u5185\u5BB9\u6682\u65F6\u6CA1\u80FD\u5904\u7406\u6210\u529F"
        : stageLabel(currentCapture?.capture.current_stage || currentCapture?.capture.status),
    description:
      phase === "failed"
        ? errorMessageForCapture(currentCapture, workspaceError)
        : processingDescription(currentCapture),
  };

  return (
    <div className="app-shell">
      <Header status={headerStatus} featureName={config?.product_feature_name || "\u4E07\u8C61\u6210\u6587"} onReset={resetWorkspace} />

      <main className="main-stage">
        <AnimatePresence mode="wait">
          {phase === "idle" ? (
            <motion.div
              key="idle"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.4, ease: "easeInOut" }}
              className="stage-shell stage-shell-idle"
            >
              <InputStage
                input={input}
                subtitle={inputSubtitle}
                selectedFile={selectedFile}
                submitting={submitting || clearBusy}
                supportedExtensions={supportedExtensions}
                maxUploadSizeMb={config?.max_upload_size_mb || 0}
                freeDurationMinutes={config?.free_duration_minutes || 30}
                captures={recentCaptures}
                currentCaptureId={currentCapture?.capture.id}
                onInputChange={setInput}
                onSubmitText={() => void submitText()}
                onPaste={() => void pasteFromClipboard()}
                onSelectFile={setSelectedFile}
                onSubmitFile={() => void submitFile()}
                onOpenCapture={(captureId) => void openCapture(captureId)}
                onClearHistory={() => {
                  if (!clearBusy) void clearHistory();
                }}
                onDeleteCapture={queueCaptureRemoval}
              />
            </motion.div>
          ) : null}

          {(loadingDeepLink || phase === "processing" || phase === "failed") && (
            <motion.div
              key={phase}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.35, ease: "easeInOut" }}
              className="stage-shell stage-shell-processing"
            >
              <ProcessingStage
                phase={loadingDeepLink ? "bootstrapping" : phase === "failed" ? "failed" : "processing"}
                eyebrow={loadingDeepLink ? "\u6062\u590D\u5185\u5BB9" : processingCopy.eyebrow}
                title={loadingDeepLink ? "\u6B63\u5728\u6062\u590D\u4E0A\u4E00\u6761\u5185\u5BB9" : processingCopy.title}
                description={
                  loadingDeepLink
                    ? "\u5982\u679C\u4F60\u662F\u4ECE\u6DF1\u94FE\u63A5\u8FDB\u5165\uFF0C\u8FD9\u91CC\u4F1A\u76F4\u63A5\u6062\u590D\u5230\u5BF9\u5E94\u5185\u5BB9\u7684\u5F53\u524D\u72B6\u6001\u3002"
                    : processingCopy.description
                }
                stageLabel={phase === "processing" ? stageLabel(currentCapture?.capture.current_stage || currentCapture?.capture.status) : undefined}
                progressPercent={phase === "processing" ? currentCapture?.capture.progress_percent : undefined}
                progressDetail={phase === "processing" ? currentCapture?.capture.progress_detail : undefined}
                onReset={phase === "failed" || loadingDeepLink ? resetWorkspace : undefined}
              />
            </motion.div>
          )}

          {phase === "done" && currentCapture ? (
            <motion.div
              key="done"
              initial={{ opacity: 0, y: 18, scale: 0.985 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.6, ease: "easeOut" }}
              className="stage-shell stage-shell-ready"
            >
              <DeliverableStage capture={currentCapture} />
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
              <button className="toast-action" onClick={undoPendingHistoryClear} type="button">
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
              <button className="toast-action" onClick={() => undoPendingRemoval(removal.token)} type="button">
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
