from __future__ import annotations

import unittest

import httpx

from scripts.verify_deployment_contract import inspect_deployment


class DeploymentContractTestCase(unittest.TestCase):
    def test_deployment_contract_requires_no_store_entrypoints_and_immutable_assets(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path in {"/", "/c/deployment-contract-check"}:
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "no-store, max-age=0"},
                    text='<script type="module" src="/assets/index-abc123.js"></script>',
                )
            if request.url.path == "/health":
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "no-store, max-age=0"},
                    json={
                        "status": "ok",
                        "frontend_build_id": "build-1",
                        "frontend_git_sha": "abc1234",
                        "asset_manifest_status": "ready",
                    },
                )
            if request.url.path == "/assets/index-abc123.js":
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"},
                    text="console.log('ok')",
                )
            return httpx.Response(404)

        report = inspect_deployment(
            "https://wanxiang.example",
            client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        )

        self.assertTrue(report["passed"])
        self.assertEqual(len(report["checks"]), 5)

    def test_deployment_contract_rejects_retired_windows_entry(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path in {"/", "/c/deployment-contract-check"}:
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "no-store"},
                    text="Windows 版",
                )
            if request.url.path == "/health":
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "no-store"},
                    json={
                        "status": "ok",
                        "frontend_build_id": "build-1",
                        "frontend_git_sha": "abc1234",
                        "asset_manifest_status": "ready",
                    },
                )
            return httpx.Response(404)

        report = inspect_deployment(
            "https://wanxiang.example",
            client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        )

        self.assertFalse(report["passed"])
        public_ui = next(item for item in report["checks"] if item["name"] == "public_ui_contract")
        self.assertIn("Windows 版", public_ui["forbidden_tokens"])

    def test_deployment_contract_scans_hashed_assets_for_retired_entry(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path in {"/", "/c/deployment-contract-check"}:
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "no-store"},
                    text='<script type="module" src="/assets/index-old.js"></script>',
                )
            if request.url.path == "/health":
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "no-store"},
                    json={
                        "status": "ok",
                        "frontend_build_id": "build-1",
                        "frontend_git_sha": "abc1234",
                        "asset_manifest_status": "ready",
                    },
                )
            if request.url.path == "/assets/index-old.js":
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"},
                    text="const label = 'Windows 版';",
                )
            return httpx.Response(404)

        report = inspect_deployment(
            "https://wanxiang.example",
            client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        )

        self.assertFalse(report["passed"])
        public_ui = next(item for item in report["checks"] if item["name"] == "public_ui_contract")
        self.assertEqual(public_ui["asset_forbidden_tokens"], {"/assets/index-old.js": ["Windows 版"]})


if __name__ == "__main__":
    unittest.main()
