import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSignaturePayload,
  isAllowedContentType,
  isAllowedOrigin,
  validateRelayUrl,
  verifyServiceSignature,
} from "./cf_worker_security.mjs";

test("relay accepts only HTTPS platform hosts", () => {
  assert.equal(validateRelayUrl("https://www.bilibili.com/video/BV1xx").hostname, "www.bilibili.com");
  assert.equal(validateRelayUrl("https://api.xiaohongshu.com/api/sns/web/v1/feed").hostname, "api.xiaohongshu.com");
  assert.throws(() => validateRelayUrl("http://www.bilibili.com/video/BV1xx"), /HTTPS/);
  assert.throws(() => validateRelayUrl("https://example.com/steal"), /allowlist/);
});

test("relay rejects local, metadata, credentialed and deceptive hosts", () => {
  for (const candidate of [
    "https://127.0.0.1/",
    "https://[::1]/",
    "https://169.254.169.254/latest/meta-data/",
    "https://localhost/",
    "https://bilibili.com.example.org/",
    "https://user:pass@www.bilibili.com/",
  ]) {
    assert.throws(() => validateRelayUrl(candidate));
  }
});

test("CORS and response content types use explicit allowlists", () => {
  assert.equal(isAllowedOrigin("https://wanxiang.praxisai.online"), true);
  assert.equal(isAllowedOrigin("https://liamgrant-wanxiang-chengwen-preview.hf.space"), true);
  assert.equal(isAllowedOrigin("https://evil.example"), false);
  assert.equal(isAllowedContentType("application/json; charset=utf-8"), true);
  assert.equal(isAllowedContentType("text/html"), true);
  assert.equal(isAllowedContentType("application/x-msdownload"), false);
});

test("service HMAC binds timestamp, nonce, duration and body digest", async () => {
  const secret = "test-only-secret";
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = "0123456789abcdef0123456789abcdef";
  const duration = "42.5";
  const bodyHash = "a".repeat(64);
  const service = "wanxiang-backend";
  const payload = buildSignaturePayload({ timestamp, nonce, duration, bodyHash, service });
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = Buffer.from(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload))).toString("hex");

  assert.equal(
    await verifyServiceSignature({ secret, timestamp, nonce, duration, bodyHash, service, signature }),
    true,
  );
  assert.equal(
    await verifyServiceSignature({ secret, timestamp, nonce, duration: "43", bodyHash, service, signature }),
    false,
  );
});
