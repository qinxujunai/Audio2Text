from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


REQUIRED_SUCCESS_PLATFORMS = {
    "youtube",
    "bilibili",
    "xiaoyuzhou",
    "douyin",
    "xiaohongshu",
    "wechat_article",
}
REQUIRED_XIAOHONGSHU_VARIANTS = {"image", "video", "live"}
FORBIDDEN_COMMITTED_QUERY_KEYS = {
    "share_source",
    "vd_source",
    "xsec_token",
    "xsec_source",
    "xhsshare",
}


def load_live_smoke_samples(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_live_smoke_samples(payload)
    if errors:
        raise ValueError("Invalid live smoke sample file:\n- " + "\n- ".join(errors))
    return payload


def validate_live_smoke_samples(payload: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root payload must be a JSON object"]

    success_items = payload.get("success")
    failure_items = payload.get("failure")
    if not isinstance(success_items, list):
        errors.append("success must be a list")
        success_items = []
    if not isinstance(failure_items, list):
        errors.append("failure must be a list")
        failure_items = []

    def validate_sample(bucket: str, index: int, sample: object) -> None:
        if not isinstance(sample, dict):
            errors.append(f"{bucket}[{index}] must be an object")
            return

        platform = str(sample.get("platform", "")).strip()
        url = str(sample.get("url", "")).strip()
        if not platform:
            errors.append(f"{bucket}[{index}] is missing platform")
        if not url:
            errors.append(f"{bucket}[{index}] is missing url")

        variant = sample.get("variant")
        if variant is not None and not str(variant).strip():
            errors.append(f"{bucket}[{index}] has an empty variant")

        for optional_key in (
            "expected_media_kind",
            "expected_error_stage",
            "expected_text_source",
        ):
            if optional_key in sample and not str(sample.get(optional_key, "")).strip():
                errors.append(f"{bucket}[{index}] has an empty {optional_key}")

    for index, sample in enumerate(success_items):
        validate_sample("success", index, sample)
    for index, sample in enumerate(failure_items):
        validate_sample("failure", index, sample)

    success_platforms = {
        str(sample.get("platform", "")).strip()
        for sample in success_items
        if isinstance(sample, dict)
    }
    missing_platforms = sorted(item for item in REQUIRED_SUCCESS_PLATFORMS if item not in success_platforms)
    if missing_platforms:
        errors.append("success samples are missing required platforms: " + ", ".join(missing_platforms))

    xiaohongshu_variants = {
        str(sample.get("variant", "")).strip()
        for sample in success_items
        if isinstance(sample, dict) and str(sample.get("platform", "")).strip() == "xiaohongshu"
    }
    missing_variants = sorted(item for item in REQUIRED_XIAOHONGSHU_VARIANTS if item not in xiaohongshu_variants)
    if missing_variants:
        errors.append("xiaohongshu success samples are missing variants: " + ", ".join(missing_variants))

    return errors


def find_committed_sample_url_hygiene_issues(payload: dict) -> list[str]:
    issues: list[str] = []
    for bucket in ("success", "failure"):
        samples = payload.get(bucket, [])
        if not isinstance(samples, list):
            continue
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                continue
            url = str(sample.get("url", "")).strip()
            query_keys = {key.lower() for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
            forbidden_keys = sorted(query_keys & FORBIDDEN_COMMITTED_QUERY_KEYS)
            if forbidden_keys:
                issues.append(f"{bucket}[{index}] has committed share/tracking query keys: {', '.join(forbidden_keys)}")
    return issues
