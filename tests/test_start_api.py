from __future__ import annotations

import socket
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
