const ALLOWED_ORIGINS = new Set([
  "https://wanxiang.praxisai.online",
  "https://liamgrant-wanxiang-chengwen-preview.hf.space",
]);

const ALLOWED_HOST_SUFFIXES = [
  "bilibili.com",
  "b23.tv",
  "bilivideo.com",
  "hdslb.com",
  "xiaohongshu.com",
  "xhslink.com",
  "xhscdn.com",
  "douyin.com",
  "iesdouyin.com",
  "douyinvod.com",
  "byteimg.com",
  "pstatp.com",
  "amemv.com",
  "xiaoyuzhoufm.com",
  "xyzcdn.net",
  "weixin.qq.com",
  "mmbiz.qpic.cn",
  "mmbiz.qlogo.cn",
  "youtube.com",
  "youtube-nocookie.com",
  "youtu.be",
  "googlevideo.com",
  "ytimg.com",
];

const ALLOWED_CONTENT_TYPES = [
  "text/",
  "image/",
  "audio/",
  "video/",
  "application/json",
  "application/javascript",
  "application/xml",
  "application/xhtml+xml",
  "application/dash+xml",
  "application/vnd.apple.mpegurl",
  "application/octet-stream",
];

function isIpLiteral(hostname) {
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (host.includes(":")) return true;
  if (!/^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) return false;
  return host.split(".").every((part) => Number(part) >= 0 && Number(part) <= 255);
}

function hostMatches(hostname, suffix) {
  return hostname === suffix || hostname.endsWith(`.${suffix}`);
}

export function isAllowedOrigin(origin) {
  return ALLOWED_ORIGINS.has((origin || "").trim().replace(/\/$/, ""));
}

export function validateRelayUrl(rawUrl) {
  let target;
  try {
    target = new URL(rawUrl);
  } catch {
    throw new Error("Invalid relay URL");
  }
  if (target.protocol !== "https:") throw new Error("HTTPS is required");
  if (target.username || target.password) throw new Error("URL credentials are forbidden");

  const hostname = target.hostname.toLowerCase().replace(/\.$/, "");
  if (
    !hostname ||
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname.endsWith(".local") ||
    hostname === "metadata.google.internal" ||
    isIpLiteral(hostname)
  ) {
    throw new Error("Local and reserved targets are forbidden");
  }
  if (!ALLOWED_HOST_SUFFIXES.some((suffix) => hostMatches(hostname, suffix))) {
    throw new Error("Target host is not in the platform allowlist");
  }
  target.hostname = hostname;
  target.hash = "";
  return target;
}

export function isAllowedContentType(contentType) {
  const normalized = (contentType || "").split(";", 1)[0].trim().toLowerCase();
  return ALLOWED_CONTENT_TYPES.some((allowed) =>
    allowed.endsWith("/") ? normalized.startsWith(allowed) : normalized === allowed,
  );
}

export function buildSignaturePayload({ timestamp, nonce, duration, bodyHash, service }) {
  return [timestamp, nonce, duration, bodyHash, service].join("\n");
}

function hexToBytes(value) {
  if (!/^[0-9a-f]{64}$/i.test(value || "")) return null;
  const bytes = new Uint8Array(32);
  for (let index = 0; index < 32; index += 1) {
    bytes[index] = Number.parseInt(value.slice(index * 2, index * 2 + 2), 16);
  }
  return bytes;
}

export async function verifyServiceSignature({
  secret,
  timestamp,
  nonce,
  duration,
  bodyHash,
  service,
  signature,
}) {
  if (!secret || !/^[0-9]{10}$/.test(timestamp || "") || !/^[0-9a-f]{32}$/i.test(nonce || "")) {
    return false;
  }
  const signatureBytes = hexToBytes(signature);
  if (!signatureBytes || !/^[0-9a-f]{64}$/i.test(bodyHash || "")) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const payload = buildSignaturePayload({ timestamp, nonce, duration, bodyHash, service });
  return crypto.subtle.verify(
    "HMAC",
    key,
    signatureBytes,
    new TextEncoder().encode(payload),
  );
}

export function isFreshTimestamp(timestamp, nowSeconds = Math.floor(Date.now() / 1000), toleranceSeconds = 300) {
  const value = Number(timestamp);
  return Number.isInteger(value) && Math.abs(nowSeconds - value) <= toleranceSeconds;
}

export async function sha256Hex(bytes) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return Array.from(digest, (value) => value.toString(16).padStart(2, "0")).join("");
}

export async function readBodyWithLimit(body, maximumBytes) {
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximumBytes) throw new Error("Response body exceeds limit");
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const combined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return combined;
}
