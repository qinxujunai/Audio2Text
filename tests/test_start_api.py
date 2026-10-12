from __future__ import annotations

import socket
import unittest
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import scripts.start_api as start_api


class StartApiPortCheckTestCase(unittest.TestCase):
    def test_assert_port_available_passes_for_free_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = int(probe.getsockname()[1])

        start_api._assert_port_available("127.0.0.1", free_port)

    def test_assert_port_available_reports_owner_when_port_is_busy(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            busy_port = int(listener.getsockname()[1])

            with patch.object(
                start_api,
                "_find_port_owners",
                return_value=[start_api.PortOwner(pid="12345", command="python -m old.service")],
            ):
                with self.assertRaises(start_api.PortInUseError) as context:
                    start_api._assert_port_available("127.0.0.1", busy_port)

        message = str(context.exception)
        self.assertIn("端口已被占用", message)
        self.assertIn("127.0.0.1", message)
        self.assertIn(str(busy_port), message)
        self.assertIn("PID 12345", message)
        self.assertIn("python -m old.service", message)
        self.assertIn("AUDIO2TEXT_API_PORT", message)

    def test_main_stops_before_preflight_when_port_is_busy(self) -> None:
        with patch.object(start_api, "_assert_port_available", side_effect=start_api.PortInUseError("busy")), patch.object(
            start_api, "ensure_runtime_ready"
        ) as ensure_runtime_ready, patch.object(start_api.uvicorn, "run") as uvicorn_run:
            with redirect_stderr(StringIO()):
                result = start_api.main()

        self.assertEqual(result, 1)
        ensure_runtime_ready.assert_not_called()
        uvicorn_run.assert_not_called()

    def test_frontend_guard_accepts_matching_build_without_rebuilding(self) -> None:
        with patch.object(
            start_api,
            "_run_frontend_fingerprint",
            return_value={"source_fingerprint": "current"},
        ), patch.object(
            start_api,
            "_frontend_manifest_matches",
            return_value=(True, "build-id"),
        ), patch.object(
            start_api.shutil,
            "which",
            return_value="node.exe",
        ), patch.object(start_api.subprocess, "run") as run:
            start_api._ensure_frontend_build_current()

        run.assert_not_called()

    def test_frontend_guard_rebuilds_stale_build_when_npm_exists(self) -> None:
        with patch.object(
            start_api,
            "_run_frontend_fingerprint",
            return_value={"source_fingerprint": "current"},
        ), patch.object(
            start_api,
            "_frontend_manifest_matches",
            side_effect=[(False, "源码指纹与构建产物不一致"), (True, "new-build")],
        ), patch.object(
            start_api.shutil,
            "which",
            side_effect=lambda name: "node.exe" if name == "node" else "npm.cmd" if name == "npm.cmd" else None,
        ), patch.object(
            start_api.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            start_api._ensure_frontend_build_current()

        run.assert_called_once_with(
            ["npm.cmd", "run", "build"],
            cwd=start_api.FRONTEND_SOURCE_DIR,
            check=False,
            text=True,
        )

    def test_frontend_guard_rejects_stale_build_without_npm(self) -> None:
        with patch.object(
            start_api,
            "_run_frontend_fingerprint",
            return_value={"source_fingerprint": "current"},
        ), patch.object(
            start_api,
            "_frontend_manifest_matches",
            return_value=(False, "构建资源缺失"),
        ), patch.object(
            start_api.shutil,
            "which",
            side_effect=lambda name: "node.exe" if name == "node" else None,
        ):
            with self.assertRaises(start_api.FrontendBuildError) as context:
                start_api._ensure_frontend_build_current()

        self.assertIn("已拒绝启动旧 UI", str(context.exception))

    def test_cloud_preview_trusts_dist_when_runtime_image_has_no_node(self) -> None:
        manifest = MagicMock()
        manifest.is_file.return_value = True
        index = MagicMock()
        index.is_file.return_value = True
        with patch.object(start_api, "DEPLOYMENT_MODE", "cloud_preview"), patch.object(
            start_api.shutil,
            "which",
            return_value=None,
        ), patch.object(start_api, "FRONTEND_MANIFEST", manifest), patch.object(
            start_api, "FRONTEND_INDEX", index
        ):
            start_api._ensure_frontend_build_current()


if __name__ == "__main__":
    unittest.main()
