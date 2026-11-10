from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

from app import update_check


def _client(responses: dict[str, httpx.Response | Exception]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        item = responses.get(str(request.url))
        if isinstance(item, Exception):
            raise item
        return item or httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


MIRROR, GITHUB = update_check.MANIFEST_SOURCES


class UpdateCheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        update_check.reset_cache()
        self.env = patch.dict(os.environ, {"AUDIO2TEXT_UPDATE_CHECK": "1"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        update_check.reset_cache()

    def test_newer_release_on_the_mirror_is_reported(self) -> None:
        client = _client({MIRROR: httpx.Response(200, json={"version": "2.4.1"})})
        status = update_check.update_status("2.4.0", client=client)
        self.assertTrue(status["update_available"])
        self.assertEqual(status["latest"], "2.4.1")
        self.assertEqual(status["url"], update_check.UPDATE_PAGE_URL)

    def test_github_is_used_when_the_mirror_fails(self) -> None:
        client = _client({MIRROR: httpx.ConnectTimeout("slow"), GITHUB: httpx.Response(200, json={"tag_name": "v2.5.0"})})
        self.assertEqual(update_check.update_status("2.4.0", client=client)["latest"], "2.5.0")

    def test_same_or_older_version_is_not_an_update(self) -> None:
        for latest in ("2.4.0", "2.3.9"):
            update_check.reset_cache()
            client = _client({MIRROR: httpx.Response(200, json={"version": latest})})
            self.assertFalse(update_check.update_status("2.4.0", client=client)["update_available"])

    def test_bad_answers_and_network_failure_mean_no_notice(self) -> None:
        client = _client({MIRROR: httpx.Response(200, text="<html>"), GITHUB: httpx.ConnectError("offline")})
        status = update_check.update_status("2.4.0", client=client)
        self.assertFalse(status["update_available"])
        self.assertIsNone(status["latest"])

    def test_prerelease_is_ignored(self) -> None:
        client = _client({MIRROR: httpx.Response(404), GITHUB: httpx.Response(200, json={"tag_name": "v9.0.0", "prerelease": True})})
        self.assertFalse(update_check.update_status("2.4.0", client=client)["update_available"])

    def test_result_is_cached_between_launches_of_the_home_page(self) -> None:
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={"version": "2.4.1"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        update_check.update_status("2.4.0", client=client, now=1000)
        update_check.update_status("2.4.0", client=client, now=2000)
        self.assertEqual(len(calls), 1)

    def test_check_can_be_switched_off(self) -> None:
        with patch.dict(os.environ, {"AUDIO2TEXT_UPDATE_CHECK": "0"}):
            client = _client({MIRROR: httpx.Response(200, json={"version": "9.9.9"})})
            self.assertFalse(update_check.update_status("2.4.0", client=client)["update_available"])


if __name__ == "__main__":
    unittest.main()
