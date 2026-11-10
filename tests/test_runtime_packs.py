from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from app.runtime_packs import RuntimePackError, RuntimePackManager


class RuntimePackManagerTestCase(unittest.TestCase):
    def _manager(self, root: Path, pack: dict) -> RuntimePackManager:
        manifest = root / "runtime-packs.json"
        manifest.write_text(json.dumps({"schema_version": 1, "packs": [pack]}), encoding="utf-8")
        return RuntimePackManager(manifest, root / "runtime")

    def test_manifest_rejects_insecure_url_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            manager = self._manager(
                Path(raw_root),
                {
                    "id": "model",
                    "version": "1",
                    "url": "http://example.com/model.zip",
                    "sha256": "0" * 64,
                    "size_bytes": 1,
                    "install_subdir": "../outside",
                },
            )
            with self.assertRaises(RuntimePackError):
                manager.packs()

    def test_install_is_verified_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            archive = root / "pack.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("model.bin", b"verified")
            payload = archive.read_bytes()
            manager = self._manager(
                root,
                {
                    "id": "sensevoice",
                    "version": "1.0.0",
                    "url": "https://downloads.example.com/model.zip",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                    "install_subdir": "models/sensevoice",
                },
            )

            def fake_download(pack, destination, *_url):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)

            with patch.object(manager, "_download", fake_download):
                result = manager.install("sensevoice")

            self.assertTrue(result["installed"])
            self.assertEqual((root / "runtime/models/sensevoice/model.bin").read_bytes(), b"verified")
            self.assertTrue(manager.status()[0]["installed"])

    def test_zip_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            archive = root / "pack.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.txt", b"no")
            payload = archive.read_bytes()
            manager = self._manager(
                root,
                {
                    "id": "bad-pack",
                    "version": "1",
                    "url": "https://downloads.example.com/model.zip",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                    "install_subdir": "models/bad",
                },
            )
            with self.assertRaises(RuntimePackError):
                manager._unpack(manager.get("bad-pack"), archive, root / "stage")
            self.assertFalse((root / "escape.txt").exists())

    def test_download_retries_transient_network_failure_and_keeps_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = self._manager(
                root,
                {
                    "id": "model",
                    "version": "1",
                    "url": "https://downloads.example.com/model.zip",
                    "sha256": "0" * 64,
                    "size_bytes": 8,
                    "install_subdir": "models/model",
                },
            )
            destination = root / "runtime/.downloads/model.part"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"part")
            pack = manager.get("model")
            attempts = 0

            def flaky_download(_pack, path, *_url):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise httpx.ConnectTimeout("temporary")
                self.assertEqual(path.read_bytes(), b"part")
                path.write_bytes(b"complete")

            with (
                patch.object(manager, "_download_once", side_effect=flaky_download),
                patch("app.runtime_packs.time.sleep"),
            ):
                manager._download(pack, destination)

            self.assertEqual(attempts, 2)
            self.assertEqual(destination.read_bytes(), b"complete")

    def test_download_normalizes_final_network_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = self._manager(
                root,
                {
                    "id": "model",
                    "version": "1",
                    "url": "https://downloads.example.com/model.zip",
                    "sha256": "0" * 64,
                    "size_bytes": 8,
                    "install_subdir": "models/model",
                },
            )
            with (
                patch.object(
                    manager,
                    "_download_once",
                    side_effect=httpx.ConnectTimeout("temporary"),
                ),
                patch("app.runtime_packs.time.sleep"),
                self.assertRaisesRegex(RuntimePackError, "已下载的进度会继续保留"),
            ):
                manager._download(manager.get("model"), root / "model.part")

    def test_mirror_is_tried_first_and_failure_falls_back_to_the_canonical_url(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = self._manager(
                root,
                {
                    "id": "model",
                    "version": "1",
                    "url": "https://github.example.com/model.zip",
                    "mirrors": ["https://mirror.example.cn/model.zip"],
                    "sha256": "0" * 64,
                    "size_bytes": 8,
                    "install_subdir": "models/model",
                },
            )
            tried = []

            def download(_pack, path, url):
                tried.append(url)
                if "mirror" in url:
                    request = httpx.Request("GET", url)
                    raise httpx.HTTPStatusError("missing", request=request, response=httpx.Response(404, request=request))
                path.write_bytes(b"complete")

            destination = root / "model.part"
            with patch.object(manager, "_download_once", side_effect=download), patch("app.runtime_packs.time.sleep"):
                manager._download(manager.get("model"), destination)
            self.assertEqual(tried, ["https://mirror.example.cn/model.zip", "https://github.example.com/model.zip"])
            self.assertEqual(destination.read_bytes(), b"complete")

    def test_manifest_rejects_insecure_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            manager = self._manager(
                Path(raw_root),
                {"id": "model", "version": "1", "url": "https://example.com/m.zip",
                 "mirrors": ["http://mirror.example.cn/m.zip"], "sha256": "0" * 64, "size_bytes": 1},
            )
            with self.assertRaises(RuntimePackError):
                manager.packs()

    def test_install_removes_the_pack_it_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            archive = root / "pack.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("model.int8.onnx", b"general")
            payload = archive.read_bytes()
            manager = self._manager(
                root,
                {"id": "general", "version": "2", "url": "https://example.com/g.zip",
                 "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload),
                 "install_subdir": "models/sensevoice/general-int8", "replaces": ["models/sensevoice/small-int8"]},
            )
            old = root / "runtime/models/sensevoice/small-int8"
            old.mkdir(parents=True)
            (old / "model.int8.onnx").write_bytes(b"cantonese")
            with patch.object(manager, "_download", side_effect=lambda pack, dest: dest.write_bytes(payload)):
                manager.install("general")
            self.assertFalse(old.exists())
            self.assertEqual((root / "runtime/models/sensevoice/general-int8/model.int8.onnx").read_bytes(), b"general")

    def test_project_manifest_points_at_the_general_model(self) -> None:
        manager = RuntimePackManager(Path(__file__).resolve().parents[1] / "runtime-packs.json", Path("/nonexistent"))
        packs = {pack.pack_id: pack for pack in manager.packs()}
        model = packs["sensevoice-small-int8-general"]
        self.assertEqual(model.install_subdir, "models/sensevoice/general-int8")
        self.assertIn("models/sensevoice/small-int8", model.replaces)
        self.assertTrue(all(pack.urls[-1].startswith("https://github.com/qinxujunai/Audio2Text/releases/") for pack in packs.values()))

    def test_download_once_accepts_real_httpx_response(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            payload = b"download"
            manager = self._manager(
                root,
                {
                    "id": "model",
                    "version": "1",
                    "url": "https://downloads.example.com/model.zip",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                    "install_subdir": "models/model",
                },
            )
            destination = root / "model.part"
            request = httpx.Request("GET", "https://downloads.example.com/model.zip")
            response = httpx.Response(200, content=payload, request=request)

            class FakeClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

                def build_request(self, *_args, **_kwargs):
                    return request

                def send(self, *_args, **_kwargs):
                    return response

            with patch("app.runtime_packs.httpx.Client", return_value=FakeClient()):
                manager._download_once(manager.get("model"), destination)

            self.assertEqual(destination.read_bytes(), payload)
            self.assertTrue(response.is_closed)


if __name__ == "__main__":
    unittest.main()
