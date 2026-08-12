"""Render the public product site from a verified stable GitHub Release."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_INSTALLER = "Wanxiang-Windows-x64-Setup.exe"
EXPECTED_CHECKSUMS = ("SHA256SUMS.txt", "Wanxiang-Windows-x64-Setup.exe.sha256")
TRIAL_URL = "https://wanxiang.praxisai.online"


def _request(url: str, *, token: str = "", accept: str = "application/json") -> bytes:
    headers = {
        "Accept": accept,
        "User-Agent": "Wanxiang-public-site-renderer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def _load_release(path: str | None, *, repository: str, token: str) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    payload = _request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        token=token,
        accept="application/vnd.github+json",
    )
    return json.loads(payload.decode("utf-8"))


def _asset_by_name(release: dict, names: tuple[str, ...]) -> dict:
    matches = [asset for asset in release.get("assets", []) if asset.get("name") in names]
    if len(matches) != 1:
        raise ValueError(f"release must contain exactly one of: {', '.join(names)}")
    asset = matches[0]
    if asset.get("state") != "uploaded" or int(asset.get("size") or 0) <= 0:
        raise ValueError(f"release asset is unavailable: {asset.get('name')}")
    if not str(asset.get("browser_download_url") or "").startswith("https://"):
        raise ValueError(f"release asset URL is not HTTPS: {asset.get('name')}")
    return asset


def _asset_digest(asset: dict) -> str:
    value = str(asset.get("digest") or "").casefold()
    digest = value.removeprefix("sha256:")
    if not value.startswith("sha256:") or not SHA256_PATTERN.fullmatch(digest):
        raise ValueError(f"release asset has no valid SHA-256 digest: {asset.get('name')}")
    return digest


def _format_size(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def _checksum_for(text: str, installer_name: str) -> str:
    matches: list[str] = []
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        if filename.lstrip("*").strip() == installer_name:
            matches.append(digest.casefold())
    if len(matches) != 1 or not SHA256_PATTERN.fullmatch(matches[0]):
        raise ValueError("checksum file must contain exactly one installer digest")
    return matches[0]


def _trial_is_current(version: str, *, health_path: str | None, config_path: str | None) -> bool:
    try:
        health = (
            json.loads(Path(health_path).read_text(encoding="utf-8"))
            if health_path
            else json.loads(_request(f"{TRIAL_URL}/health").decode("utf-8"))
        )
        config = (
            json.loads(Path(config_path).read_text(encoding="utf-8"))
            if config_path
            else json.loads(_request(f"{TRIAL_URL}/config").decode("utf-8"))
        )
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return False
    return (
        health.get("status") == "ok"
        and health.get("version") == version
        and health.get("deployment_mode") == "cloud_preview"
        and health.get("runtime_target") == "cloud_demo"
        and config.get("deployment_mode") == "cloud_preview"
        and config.get("runtime_target") == "cloud_demo"
    )


def _validated_release(release: dict, *, token: str = "", checksums_file: str | None = None) -> dict:
    match = VERSION_PATTERN.fullmatch(str(release.get("tag_name") or ""))
    if match is None or release.get("draft") or release.get("prerelease"):
        raise ValueError("site requires a stable vMAJOR.MINOR.PATCH release")
    installer = _asset_by_name(release, (EXPECTED_INSTALLER,))
    checksums = _asset_by_name(release, EXPECTED_CHECKSUMS)
    digest = _asset_digest(installer)
    checksum_text = (
        Path(checksums_file).read_text(encoding="utf-8")
        if checksums_file
        else _request(str(checksums["browser_download_url"]), token=token, accept="application/octet-stream").decode("utf-8")
    )
    if _checksum_for(checksum_text, EXPECTED_INSTALLER) != digest:
        raise ValueError("installer digest does not match the published checksum file")
    return {
        "version": match.group("version"),
        "tag": str(release["tag_name"]),
        "installer_url": str(installer["browser_download_url"]),
        "installer_size": int(installer["size"]),
        "installer_size_label": _format_size(int(installer["size"])),
        "sha256": digest,
        "checksums_url": str(checksums["browser_download_url"]),
    }


def _render_site(source: Path, output: Path, metadata: dict, *, trial_available: bool) -> None:
    if not source.is_dir():
        raise ValueError(f"site directory does not exist: {source}")
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    shutil.copytree(source, output)
    trial_link = (
        f'<a class="button secondary trial-link" href="{TRIAL_URL}" data-i18n="trial">在线试用</a>'
        if trial_available
        else ""
    )
    replacements = {
        "__WANXIANG_VERSION__": metadata["version"],
        "__WANXIANG_INSTALLER_URL__": metadata["installer_url"],
        "__WANXIANG_INSTALLER_SIZE__": metadata["installer_size_label"],
        "__WANXIANG_SHA256__": metadata["sha256"],
        "__WANXIANG_TRIAL_LINK__": trial_link,
    }
    index_path = output / "index.html"
    content = index_path.read_text(encoding="utf-8")
    for marker, value in replacements.items():
        content = content.replace(marker, value)
    if "__WANXIANG_" in content:
        raise ValueError("unresolved public-site marker remains")
    index_path.write_text(content, encoding="utf-8")
    metadata["trial_available"] = trial_available
    (output / "release-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", default=str(PROJECT_ROOT / "site"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--release-json")
    parser.add_argument("--checksums-file")
    parser.add_argument("--health-json")
    parser.add_argument("--config-json")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "qinxujunai/Audio2Text"))
    args = parser.parse_args()
    token = os.environ.get("WANXIANG_GITHUB_TOKEN", "")
    try:
        metadata = _validated_release(
            _load_release(args.release_json, repository=args.repository, token=token),
            token=token,
            checksums_file=args.checksums_file,
        )
        available = _trial_is_current(metadata["version"], health_path=args.health_json, config_path=args.config_json)
        _render_site(Path(args.site_dir), Path(args.output_dir), metadata, trial_available=available)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(f"public site render failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
