from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

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

            def fake_download(pack, destination):
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


if __name__ == "__main__":
    unittest.main()
