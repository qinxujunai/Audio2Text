from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


class RuntimePackError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimePack:
    pack_id: str
    version: str
    url: str
    sha256: str
    size_bytes: int
    install_subdir: str
    archive_format: str = "zip"
    required: bool = False


def _safe_subdir(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise RuntimePackError("运行组件安装目录无效。")
    return value


def _parse_pack(item: dict[str, Any]) -> RuntimePack:
    pack_id = str(item.get("id") or "").strip()
    version = str(item.get("version") or "").strip()
    url = str(item.get("url") or "").strip()
    sha256 = str(item.get("sha256") or "").strip().lower()
    archive_format = str(item.get("archive_format") or "zip").strip().lower()
    try:
        size_bytes = int(item.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimePackError("运行组件大小无效。") from exc

    if not pack_id or not version or not pack_id.replace("-", "").replace("_", "").isalnum():
        raise RuntimePackError("运行组件标识或版本无效。")
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise RuntimePackError("运行组件必须使用可信的 HTTPS 下载地址。")
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise RuntimePackError("运行组件缺少有效的 SHA-256。")
    if size_bytes <= 0:
        raise RuntimePackError("运行组件缺少有效的文件大小。")
    if archive_format not in {"zip", "raw"}:
        raise RuntimePackError("运行组件压缩格式不受支持。")
    return RuntimePack(
        pack_id=pack_id,
        version=version,
        url=url,
        sha256=sha256,
        size_bytes=size_bytes,
        install_subdir=_safe_subdir(str(item.get("install_subdir") or pack_id).strip()),
        archive_format=archive_format,
        required=bool(item.get("required", False)),
    )


class RuntimePackManager:
    def __init__(self, manifest_path: Path, runtime_dir: Path) -> None:
        self.manifest_path = manifest_path
        self.runtime_dir = runtime_dir
        self.download_dir = runtime_dir / ".downloads"
        self.staging_dir = runtime_dir / ".staging"

    def packs(self) -> list[RuntimePack]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimePackError("运行组件清单不可用，请重新安装应用。") from exc
        if payload.get("schema_version") != 1 or not isinstance(payload.get("packs"), list):
            raise RuntimePackError("运行组件清单版本不受支持。")
        packs = [_parse_pack(item) for item in payload["packs"] if isinstance(item, dict)]
        if len({pack.pack_id for pack in packs}) != len(packs):
            raise RuntimePackError("运行组件清单包含重复项目。")
        return packs

    def get(self, pack_id: str) -> RuntimePack:
        pack = next((item for item in self.packs() if item.pack_id == pack_id), None)
        if pack is None:
            raise RuntimePackError("未找到该运行组件。")
        return pack

    def status(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for pack in self.packs():
            marker = self.runtime_dir / pack.install_subdir / ".wanxiang-pack.json"
            installed = False
            if marker.is_file():
                try:
                    metadata = json.loads(marker.read_text(encoding="utf-8"))
                    installed = metadata.get("id") == pack.pack_id and metadata.get("version") == pack.version
                except (OSError, json.JSONDecodeError):
                    installed = False
            records.append(
                {
                    "id": pack.pack_id,
                    "version": pack.version,
                    "size_bytes": pack.size_bytes,
                    "required": pack.required,
                    "installed": installed,
                }
            )
        return records

    def install(self, pack_id: str) -> dict[str, Any]:
        pack = self.get(pack_id)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.download_dir / f"{pack.pack_id}-{pack.version}.{pack.archive_format}.part"
        self._download(pack, archive_path)
        self._verify(pack, archive_path)
        target = self.runtime_dir / pack.install_subdir
        stage = Path(tempfile.mkdtemp(prefix=f"{pack.pack_id}-", dir=self.staging_dir))
        backup = target.with_name(f"{target.name}.previous")
        try:
            self._unpack(pack, archive_path, stage)
            (stage / ".wanxiang-pack.json").write_text(
                json.dumps({"id": pack.pack_id, "version": pack.version}, ensure_ascii=False),
                encoding="utf-8",
            )
            if backup.exists():
                shutil.rmtree(backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(stage, target)
            except Exception:
                if backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        finally:
            archive_path.unlink(missing_ok=True)
        return {"id": pack.pack_id, "version": pack.version, "installed": True}

    def _download(self, pack: RuntimePack, destination: Path) -> None:
        attempts = 4
        for attempt in range(attempts):
            try:
                self._download_once(pack, destination)
                return
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status in {408, 429} or status >= 500
                if not retryable or attempt == attempts - 1:
                    raise RuntimePackError(
                        f"运行组件下载服务暂时不可用（HTTP {status}），请稍后重试。"
                    ) from exc
            except (httpx.RequestError, OSError) as exc:
                if attempt == attempts - 1:
                    raise RuntimePackError(
                        "运行组件下载失败，请检查网络后重试；已下载的进度会继续保留。"
                    ) from exc
            time.sleep(min(2**attempt, 8))

    def _download_once(self, pack: RuntimePack, destination: Path) -> None:
        existing = destination.stat().st_size if destination.exists() else 0
        if existing == pack.size_bytes:
            return
        if existing > pack.size_bytes:
            destination.unlink()
            existing = 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False) as client:
            current_url = pack.url
            response = None
            for _ in range(4):
                request = client.build_request("GET", current_url, headers=headers)
                response = client.send(request, stream=True)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location", "")
                response.close()
                current_url = urljoin(current_url, location)
                parsed = urlparse(current_url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise RuntimePackError("运行组件下载发生了不安全的重定向。")
            else:
                raise RuntimePackError("运行组件下载重定向次数过多。")
            if response is None:
                raise RuntimePackError("运行组件下载未返回有效响应。")
            try:
                if existing and response.status_code == 200:
                    destination.unlink(missing_ok=True)
                    existing = 0
                elif existing and response.status_code != 206:
                    raise RuntimePackError("运行组件断点续传失败。")
                response.raise_for_status()
                mode = "ab" if existing else "wb"
                with destination.open(mode) as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        handle.write(chunk)
                        if handle.tell() > pack.size_bytes:
                            raise RuntimePackError("运行组件大小与清单不一致。")
            finally:
                response.close()

    def _verify(self, pack: RuntimePack, path: Path) -> None:
        if not path.is_file() or path.stat().st_size != pack.size_bytes:
            raise RuntimePackError("运行组件下载不完整。")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), pack.sha256):
            raise RuntimePackError("运行组件校验失败，已拒绝安装。")

    def _unpack(self, pack: RuntimePack, archive_path: Path, stage: Path) -> None:
        if pack.archive_format == "raw":
            shutil.copy2(archive_path, stage / pack.pack_id)
            return
        try:
            with zipfile.ZipFile(archive_path) as archive:
                stage_root = stage.resolve()
                for member in archive.infolist():
                    destination = (stage / member.filename).resolve()
                    if destination != stage_root and stage_root not in destination.parents:
                        raise RuntimePackError("运行组件包含不安全的文件路径。")
                archive.extractall(stage)
        except zipfile.BadZipFile as exc:
            raise RuntimePackError("运行组件压缩包已损坏。") from exc
