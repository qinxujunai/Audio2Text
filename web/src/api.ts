import type {
  CaptureEnvelope,
  CaptureListResponse,
  ConfigResponse,
  CreateCaptureResponse,
  RuntimePack,
} from "./types";

type DesktopRuntime = { api_base: string; token: string };

const WEB_API_BASE = window.location.origin.replace(/\/$/, "");
let desktopRuntime: DesktopRuntime | null = null;
let desktopRuntimePromise: Promise<DesktopRuntime | null> | null = null;
const TEMPORARY_BUSY_MESSAGE = "服务暂时繁忙，请稍后重试。";

type RequestError = Error & {
  status?: number;
};

async function getRuntime(): Promise<DesktopRuntime | null> {
  if (desktopRuntime) return desktopRuntime;
  if (!("__TAURI_INTERNALS__" in window)) return null;
  if (!desktopRuntimePromise) {
    desktopRuntimePromise = import("@tauri-apps/api/core")
      .then(async ({ invoke }) => {
        let lastError: unknown = null;
        for (let attempt = 0; attempt < 30; attempt++) {
          try {
            return await invoke<DesktopRuntime>("desktop_runtime_info");
          } catch (error) {
            lastError = error;
            await wait(100);
          }
        }
        throw lastError;
      })
      .then((runtime) => {
        desktopRuntime = runtime;
        return runtime;
      })
      .catch((error) => {
        desktopRuntimePromise = null;
        throw error;
      });
  }
  return desktopRuntimePromise;
}

export function resolveApiUrl(path: string, includeDesktopToken = false) {
  const runtime = desktopRuntime;
  const base = runtime?.api_base.replace(/\/$/, "") || WEB_API_BASE;
  const url = new URL(path, `${base}/`);
  if (includeDesktopToken && runtime?.token) {
    url.searchParams.set("desktop_token", runtime.token);
  }
  return url.toString();
}

function _sanitizeErrorMessage(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return TEMPORARY_BUSY_MESSAGE;
  // HTML response means the backend crashed — show generic message
  if (/^<!doctype html/i.test(trimmed) || /^<html/i.test(trimmed)) {
    return TEMPORARY_BUSY_MESSAGE;
  }
  // Backend always produces Chinese error messages; pass through directly
  return trimmed;
}

async function request<T>(
  path: string,
  options?: RequestInit,
  retries = 3,
): Promise<T> {
  let lastError: Error | null = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const runtime = await getRuntime();
      const headers = new Headers(options?.headers);
      if (runtime?.token) {
        headers.set("X-Wanxiang-Desktop-Token", runtime.token);
      }
      const response = await fetch(resolveApiUrl(path), { ...options, headers });
      const contentType = response.headers.get("content-type") || "";
      const payload = contentType.includes("application/json")
        ? await response.json()
        : await response.text();

      if (!response.ok) {
        // Retry on server errors (502/503/504) and rate limits (429)
        if (
          attempt < retries &&
          (response.status >= 502 || response.status === 429)
        ) {
          await wait(500 * (attempt + 1));
          continue;
        }
        const rawDetail =
          typeof payload === "string"
            ? payload
            : typeof payload?.detail === "string"
              ? payload.detail
              : JSON.stringify(payload);
        const safeMessage = _sanitizeErrorMessage(rawDetail || "");
        const error = new Error(
          safeMessage || `HTTP ${response.status}`,
        ) as RequestError;
        error.status = response.status;
        throw error;
      }

      return payload as T;
    } catch (error: any) {
      lastError = error;
      // Retry on network errors
      if (
        attempt < retries &&
        (error.name === "TypeError" ||
          error.message?.includes("fetch") ||
          error.message?.includes("NetworkError"))
      ) {
        await wait(500 * (attempt + 1));
        continue;
      }
      // Don't retry client errors or already-structured errors
      if (error.status && error.status < 500) throw error;
      if (attempt < retries) {
        await wait(500 * (attempt + 1));
        continue;
      }
      if (
        error.name === "TypeError" ||
        error.message?.includes("fetch") ||
        error.message?.includes("NetworkError")
      ) {
        throw new Error(TEMPORARY_BUSY_MESSAGE);
      }
      throw error;
    }
  }
  throw lastError || new Error(TEMPORARY_BUSY_MESSAGE);
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function shouldRetryCaptureRead(error: unknown) {
  if (!(error instanceof Error)) return false;
  const status = (error as RequestError).status;
  if (status === 502 || status === 503 || status === 504) {
    return true;
  }
  return (error.message || "").includes(TEMPORARY_BUSY_MESSAGE);
}

export function getConfig() {
  return request<ConfigResponse>("/config");
}

export function listCaptures() {
  return request<CaptureListResponse>("/v1/captures");
}

export function getCapture(captureId: string) {
  return request<CaptureEnvelope>(`/v1/captures/${captureId}`);
}

export async function getCaptureWithRetry(captureId: string, retries = 4) {
  let lastError: unknown = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await getCapture(captureId);
    } catch (error) {
      lastError = error;
      if (attempt === retries || !shouldRetryCaptureRead(error)) {
        throw error;
      }
      await wait(250 * (attempt + 1));
    }
  }
  throw (lastError as Error) || new Error(TEMPORARY_BUSY_MESSAGE);
}

export function createCaptureFromInput(text: string) {
  return request<CreateCaptureResponse>("/v1/captures", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export function createCaptureFromFile(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<CreateCaptureResponse>("/v1/captures", {
    method: "POST",
    body: form,
  });
}

export function clearCompletedCaptures() {
  return request<{ cleared: number; history_limit: number }>("/v1/captures", {
    method: "DELETE",
  });
}

export function deleteCapture(captureId: string) {
  return request<{ deleted: boolean; capture_id: string }>(
    `/v1/captures/${captureId}`,
    {
      method: "DELETE",
    },
  );
}

export function retryCapture(captureId: string) {
  return request<{ capture_id: string; status: string }>(
    `/v1/captures/${captureId}/retry`,
    {
      method: "POST",
    },
  );
}

export function captureEventsUrl(captureId: string) {
  return resolveApiUrl(`/v1/captures/${captureId}/events`, true);
}

export function listRuntimePacks() {
  return request<{ packs: RuntimePack[] }>("/v1/runtime/packs");
}

export function installRuntimePack(packId: string) {
  return request<{ id: string; version: string; installed: boolean }>(
    `/v1/runtime/packs/${encodeURIComponent(packId)}/install`,
    { method: "POST" },
    0,
  );
}

export async function restartDesktopRuntime() {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("restart_backend");
  window.location.reload();
}

export async function openPlatformSession(platform: "xiaohongshu" | "douyin") {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("open_platform_session", { platform });
}
