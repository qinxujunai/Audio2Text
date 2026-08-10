// Wanxiang Chengwen edge gateway: product reverse proxy, allowlisted platform
// relay, and authenticated Workers AI transcription.

import {
  isAllowedContentType,
  isAllowedOrigin,
  isFreshTimestamp,
  readBodyWithLimit,
  sha256Hex,
  validateRelayUrl,
  verifyServiceSignature,
} from "./cf_worker_security.mjs";

const TARGET_HOST = "liamgrant-wanxiang-chengwen-preview.hf.space";
const MAX_RELAY_BYTES = 12 * 1024 * 1024;
const MAX_TRANSCRIBE_BYTES = 25 * 1024 * 1024;
const MAX_TRANSCRIBE_SECONDS = 30 * 60;
const MAX_REDIRECTS = 3;
const UPSTREAM_TIMEOUT_MS = 15_000;

function jsonResponse(payload, status, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

function corsHeaders(request) {
  const origin = request.headers.get("Origin") || "";
  if (!isAllowedOrigin(origin)) return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    Vary: "Origin",
  };
}

async function handleReverseProxy(request) {
  const url = new URL(request.url);
  url.protocol = "https:";
  url.hostname = TARGET_HOST;
  url.port = "";

  const headers = new Headers(request.headers);
  headers.set("Host", TARGET_HOST);
  headers.delete("Authorization");
  headers.delete("Cookie");
  const cfConnectingIp = request.headers.get("CF-Connecting-IP");
  if (cfConnectingIp) {
    headers.set("X-Forwarded-For", cfConnectingIp);
    headers.set("X-Real-IP", cfConnectingIp);
  }

  const upstream = await fetch(url.toString(), {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    redirect: "manual",
  });
  const responseHeaders = new Headers(upstream.headers);
  for (const [name, value] of Object.entries(corsHeaders(request))) responseHeaders.set(name, value);
  responseHeaders.set("X-Content-Type-Options", "nosniff");
  responseHeaders.set("Referrer-Policy", "strict-origin-when-cross-origin");
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

async function fetchRelayTarget(initialUrl) {
  let target = validateRelayUrl(initialUrl);
  for (let redirectCount = 0; redirectCount <= MAX_REDIRECTS; redirectCount += 1) {
    const upstream = await fetch(target.toString(), {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137.0 Safari/537.36",
        Accept: "text/html,application/json,image/*,audio/*,video/*,*/*;q=0.5",
      },
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    if (![301, 302, 303, 307, 308].includes(upstream.status)) return { upstream, target };
    const location = upstream.headers.get("Location");
    await upstream.body?.cancel();
    if (!location || redirectCount === MAX_REDIRECTS) throw new Error("Relay redirect limit exceeded");
    target = validateRelayUrl(new URL(location, target).toString());
  }
  throw new Error("Relay redirect limit exceeded");
}

async function handleForwardProxy(request) {
  if (request.method !== "GET") return jsonResponse({ error: "method_not_allowed" }, 405);
  const proxyUrl = new URL(request.url).searchParams.get("url");
  if (!proxyUrl) return jsonResponse({ error: "missing_url" }, 400);

  try {
    const { upstream, target } = await fetchRelayTarget(proxyUrl);
    const contentType = upstream.headers.get("Content-Type") || "application/octet-stream";
    if (!isAllowedContentType(contentType)) {
      await upstream.body?.cancel();
      return jsonResponse({ error: "unsupported_upstream_content" }, 415, corsHeaders(request));
    }
    const declaredLength = Number(upstream.headers.get("Content-Length") || 0);
    if (declaredLength > MAX_RELAY_BYTES) {
      await upstream.body?.cancel();
      return jsonResponse({ error: "upstream_response_too_large" }, 413, corsHeaders(request));
    }
    const body = await readBodyWithLimit(upstream.body, MAX_RELAY_BYTES);
    const responseHeaders = new Headers(corsHeaders(request));
    responseHeaders.set("Content-Type", contentType);
    responseHeaders.set("X-Proxied-Url", target.toString());
    responseHeaders.set("X-Content-Type-Options", "nosniff");
    return new Response(body, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
  } catch (error) {
    console.warn(JSON.stringify({ event: "relay_rejected", reason: String(error?.message || "relay_failed") }));
    return jsonResponse({ error: "relay_request_rejected" }, 502, corsHeaders(request));
  }
}

async function handleTranscribe(request, env) {
  if (request.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);
  if (!env.TRANSCRIBE_SHARED_SECRET) return jsonResponse({ error: "service_unavailable" }, 503);

  const declaredLength = Number(request.headers.get("Content-Length") || 0);
  if (!Number.isFinite(declaredLength) || declaredLength <= 0 || declaredLength > MAX_TRANSCRIBE_BYTES) {
    return jsonResponse({ error: "invalid_request_size" }, 413);
  }
  const contentType = (request.headers.get("Content-Type") || "").split(";", 1)[0].toLowerCase();
  if (contentType !== "application/octet-stream" && !contentType.startsWith("audio/")) {
    return jsonResponse({ error: "unsupported_audio_type" }, 415);
  }

  const timestamp = request.headers.get("X-Praxis-Timestamp") || "";
  const nonce = request.headers.get("X-Praxis-Nonce") || "";
  const duration = request.headers.get("X-Praxis-Audio-Duration") || "";
  const claimedHash = request.headers.get("X-Praxis-Content-SHA256") || "";
  const service = request.headers.get("X-Praxis-Service") || "";
  const signature = request.headers.get("X-Praxis-Signature") || "";
  const durationSeconds = Number(duration);
  if (
    service !== "wanxiang-backend" ||
    !isFreshTimestamp(timestamp) ||
    !Number.isFinite(durationSeconds) ||
    durationSeconds <= 0 ||
    durationSeconds > MAX_TRANSCRIBE_SECONDS
  ) {
    return jsonResponse({ error: "unauthorized" }, 401);
  }

  let audio;
  try {
    audio = await readBodyWithLimit(request.body, MAX_TRANSCRIBE_BYTES);
  } catch {
    return jsonResponse({ error: "invalid_request_size" }, 413);
  }
  const bodyHash = await sha256Hex(audio);
  if (bodyHash !== claimedHash) return jsonResponse({ error: "unauthorized" }, 401);
  const authenticated = await verifyServiceSignature({
    secret: env.TRANSCRIBE_SHARED_SECRET,
    timestamp,
    nonce,
    duration,
    bodyHash,
    service,
    signature,
  });
  if (!authenticated) return jsonResponse({ error: "unauthorized" }, 401);

  if (env.TRANSCRIBE_RATE_LIMITER) {
    const { success } = await env.TRANSCRIBE_RATE_LIMITER.limit({ key: service });
    if (!success) {
      console.warn(JSON.stringify({ event: "transcribe_rate_limited", service }));
      return jsonResponse({ error: "rate_limit_exceeded" }, 429);
    }
  }

  const requestId = crypto.randomUUID();
  console.log(JSON.stringify({ event: "transcribe_started", request_id: requestId, bytes: audio.byteLength, duration_seconds: durationSeconds }));
  try {
    const result = await env.AI.run("@cf/openai/whisper-large-v3-turbo", {
      audio: Array.from(audio),
      task: "transcribe",
      vad_filter: true,
      condition_on_previous_text: false,
    });
    console.log(JSON.stringify({ event: "transcribe_completed", request_id: requestId }));
    return jsonResponse({ text: result.text || "", vtt: result.vtt || "", segments: result.segments || [] }, 200);
  } catch {
    console.error(JSON.stringify({ event: "transcribe_failed", request_id: requestId }));
    return jsonResponse({ error: "transcription_failed" }, 502);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      const headers = corsHeaders(request);
      if (!headers["Access-Control-Allow-Origin"]) return new Response(null, { status: 403 });
      return new Response(null, { status: 204, headers });
    }
    if (url.pathname === "/__proxy__") return handleForwardProxy(request);
    if (url.pathname === "/__transcribe__") return handleTranscribe(request, env);
    return handleReverseProxy(request);
  },
};
