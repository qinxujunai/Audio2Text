from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx


FORBIDDEN_PUBLIC_UI_TOKENS = ("Windows 版", "Windows版", "processing-mascot", "卡吱")
ASSET_PATTERN = re.compile(r"(?:src|href)=[\"'](/assets/[^\"']+)[\"']")


def _cache_contains(response: httpx.Response, token: str) -> bool:
    return token.lower() in (response.headers.get("cache-control") or "").lower()


def inspect_deployment(base_url: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    normalized = base_url.rstrip("/") + "/"
    owned_client = client is None
    session = client or httpx.Client(timeout=20.0, follow_redirects=True, trust_env=False)
    checks: list[dict[str, Any]] = []

    def get(path: str) -> httpx.Response:
        return session.get(urljoin(normalized, path.lstrip("/")))

    try:
        root = get("/")
        html = root.text
        checks.append({
            "name": "spa_entrypoint",
            "ok": root.status_code == 200 and _cache_contains(root, "no-store"),
            "status_code": root.status_code,
            "cache_control": root.headers.get("cache-control", ""),
        })
        forbidden = [token for token in FORBIDDEN_PUBLIC_UI_TOKENS if token in html]

        asset_paths = sorted(set(ASSET_PATTERN.findall(html)))
        asset_forbidden: dict[str, list[str]] = {}
        for asset_path in asset_paths:
            asset_response = get(asset_path)
            if asset_response.status_code != 200:
                continue
            asset_text = asset_response.text
            leaked = [token for token in FORBIDDEN_PUBLIC_UI_TOKENS if token in asset_text]
            if leaked:
                asset_forbidden[asset_path] = leaked

        checks.append({
            "name": "public_ui_contract",
            "ok": not forbidden and not asset_forbidden,
            "forbidden_tokens": forbidden,
            "asset_forbidden_tokens": asset_forbidden,
            "scanned_assets": len(asset_paths),
        })

        health = get("/health")
        try:
            health_payload = health.json()
        except ValueError:
            health_payload = {}
        checks.append({
            "name": "health_identity",
            "ok": health_payload.get("status") == "ok"
            and bool(health_payload.get("frontend_build_id"))
            and health_payload.get("asset_manifest_status") == "ready"
            and _cache_contains(health, "no-store"),
            "status_code": health.status_code,
            "frontend_build_id": health_payload.get("frontend_build_id"),
            "frontend_git_sha": health_payload.get("frontend_git_sha"),
            "asset_manifest_status": health_payload.get("asset_manifest_status"),
            "cache_control": health.headers.get("cache-control", ""),
        })

        deep_link = get("/c/deployment-contract-check")
        checks.append({
            "name": "deep_link_entrypoint",
            "ok": deep_link.status_code == 200 and _cache_contains(deep_link, "no-store"),
            "status_code": deep_link.status_code,
            "cache_control": deep_link.headers.get("cache-control", ""),
        })

        asset_match = ASSET_PATTERN.search(html)
        if asset_match:
            asset_path = asset_match.group(1)
            asset = get(asset_path)
            checks.append({
                "name": "hashed_asset_cache",
                "ok": asset.status_code == 200 and _cache_contains(asset, "immutable"),
                "status_code": asset.status_code,
                "asset": asset_path,
                "cache_control": asset.headers.get("cache-control", ""),
            })
        else:
            checks.append({"name": "hashed_asset_cache", "ok": False, "reason": "no hashed asset in index"})
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        checks.append({"name": "request", "ok": False, "error": type(exc).__name__})
    finally:
        if owned_client:
            session.close()

    return {
        "schema_version": 1,
        "base_url": normalized.rstrip("/"),
        "checks": checks,
        "passed": all(bool(item.get("ok")) for item in checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the deployed SPA cache and build identity contract.")
    parser.add_argument("base_url", help="Deployed site URL, for example https://wanxiang.praxisai.online")
    parser.add_argument("--json-out", help="Optional JSON report path")
    args = parser.parse_args()

    report = inspect_deployment(args.base_url)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
