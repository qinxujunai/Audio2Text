from __future__ import annotations

import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.providers.transcription import _build_worker_auth_headers


class TranscriptionWorkerAuthTestCase(unittest.TestCase):
    def test_auth_headers_bind_file_hash_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media_path = Path(folder) / "sample.bin"
            media_path.write_bytes(b"signed audio payload")

            with patch("app.providers.transcription.time.time", return_value=1_700_000_000), patch(
                "app.providers.transcription.uuid.uuid4"
            ) as uuid4:
                uuid4.return_value.hex = "0123456789abcdef0123456789abcdef"
                headers = _build_worker_auth_headers(
                    media_path,
                    secret="test-secret",
                    duration_seconds=42.5,
                )

        body_hash = hashlib.sha256(b"signed audio payload").hexdigest()
        expected_payload = "\n".join(
            [
                "1700000000",
                "0123456789abcdef0123456789abcdef",
                "42.500",
                body_hash,
                "wanxiang-backend",
            ]
        )
        expected_signature = hmac.new(
            b"test-secret",
            expected_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(headers["X-Praxis-Timestamp"], "1700000000")
        self.assertEqual(headers["X-Praxis-Nonce"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(headers["X-Praxis-Audio-Duration"], "42.500")
        self.assertEqual(headers["X-Praxis-Content-SHA256"], body_hash)
        self.assertEqual(headers["X-Praxis-Signature"], expected_signature)
        self.assertEqual(headers["X-Praxis-Service"], "wanxiang-backend")


if __name__ == "__main__":
    unittest.main()
