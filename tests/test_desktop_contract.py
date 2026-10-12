from __future__ import annotations

import re
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopContractTests(unittest.TestCase):
    def test_frontend_invokes_registered_tauri_commands(self) -> None:
        frontend = "\n".join(
            (ROOT / "web" / "src" / name).read_text(encoding="utf-8")
            for name in ["api.ts", "desktop.ts"]
        )
        rust = (ROOT / "web" / "src-tauri" / "src" / "lib.rs").read_text(
            encoding="utf-8"
        )

        invoked = set(re.findall(r'invoke(?:<[^>]+>)?\("([a-z_]+)"', frontend))
        handler_match = re.search(
            r"generate_handler!\[(.*?)\]", rust, flags=re.DOTALL
        )
        self.assertIsNotNone(handler_match)
        registered = set(re.findall(r"\b([a-z][a-z0-9_]+)\b", handler_match.group(1)))

        self.assertTrue(invoked)
        self.assertEqual(set(), invoked - registered)

    def test_runtime_manifest_has_a_stable_bundle_path(self) -> None:
        config = json.loads(
            (ROOT / "web" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        resources = config["bundle"]["resources"]
        self.assertEqual(resources["../../runtime-packs.json"], "runtime-packs.json")
        self.assertEqual(resources["sidecar/"], "sidecar/")

    def test_desktop_csp_allows_loopback_artifact_previews(self) -> None:
        config = json.loads(
            (ROOT / "web" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        directives = {
            tokens[0]: tokens[1:]
            for item in config["app"]["security"]["csp"].split(";")
            if (tokens := item.strip().split())
        }

        self.assertIn("http://127.0.0.1:*", directives["connect-src"])
        self.assertIn("http://127.0.0.1:*", directives["img-src"])
        self.assertIn("http://127.0.0.1:*", directives["media-src"])

    def test_desktop_and_header_use_the_compact_praxis_symbol(self) -> None:
        header_mark = (ROOT / "web" / "public" / "brand-mark.svg").read_text(
            encoding="utf-8"
        )
        app_mark = (ROOT / "design" / "brand" / "app-icon.svg").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-symbol="praxis-dot"', header_mark)
        self.assertIn('data-symbol="praxis-dot"', app_mark)
        all_marks = header_mark + app_mark
        self.assertNotIn("#0A84FF", all_marks)
        self.assertNotIn("#30B38C", all_marks)
        self.assertNotIn("#F2B84B", all_marks)

    def test_background_mode_tracks_real_capture_work_not_bootstrap_phase(self) -> None:
        source = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn('["queued", "processing"].includes(currentCapture.capture.status)', source)
        self.assertNotIn('active: phase === "processing"', source)

        rust = (ROOT / "web" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        self.assertIn("window.app_handle().exit(0);", rust)

    def test_desktop_keeps_native_chrome_without_a_second_brand_bar(self) -> None:
        app = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
        header = (ROOT / "web" / "src" / "components" / "Header.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn("const desktopApp = isDesktopApp();", app)
        self.assertIn("const showAppHeader = !desktopApp;", app)
        self.assertNotIn("showBrandCopy", app)
        self.assertNotIn("showBrandCopy", header)
        self.assertIn('className="desktop-result-back"', (ROOT / "web" / "src" / "components" / "DeliverableStage.tsx").read_text(encoding="utf-8"))

    def test_desktop_window_can_reach_the_compact_layout(self) -> None:
        config = json.loads(
            (ROOT / "web" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        styles = (ROOT / "web" / "src" / "styles.css").read_text(encoding="utf-8")
        window = config["app"]["windows"][0]

        self.assertLessEqual(window["minWidth"], 800)
        self.assertIn("@media (max-width: 960px)", styles)
        self.assertIn("@media (max-width: 640px)", styles)

    def test_packaged_sidecar_exposes_the_bounded_ytdlp_worker(self) -> None:
        sidecar = (ROOT / "scripts" / "desktop_sidecar.py").read_text(encoding="utf-8")
        extractors = (ROOT / "app" / "extractors.py").read_text(encoding="utf-8")
        self.assertIn('"--ytdlp-worker"', sidecar)
        self.assertIn('"--ytdlp-worker"', extractors)

    @unittest.skipUnless(os.name == "nt", "Requires Windows PowerShell")
    def test_desktop_build_matches_complete_legacy_phrases(self) -> None:
        build_script = (ROOT / "scripts" / "build_desktop.ps1").read_text(encoding="utf-8")
        expression = re.search(r"\$forbiddenUiTokens = @\(.*?\n\)", build_script, re.S).group(0)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + expression
             + "\nConvertTo-Json -InputObject @($forbiddenUiTokens) -Compress"],
            capture_output=True, check=True, encoding="utf-8",
        )
        self.assertEqual(json.loads(result.stdout), ["processing-mascot", "卡吱", "Windows 版"])

    def test_desktop_build_rejects_legacy_visual_artifacts(self) -> None:
        build_script = (ROOT / "scripts" / "build_desktop.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("processing-mascot", build_script)
        self.assertIn("0x5361", build_script)
        self.assertIn("0x5431", build_script)
        self.assertIn("0x7248", build_script)
        self.assertIn("refusing to package stale UI", build_script)

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Requires both PowerShell versions")
    def test_backend_fingerprint_is_stable_across_powershell_versions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wanxiang-fingerprint-") as directory:
            root = Path(directory)
            (root / "app").mkdir()
            (root / "scripts").mkdir()
            for name in ("verify.py", "verify_native_ui.py", "verify_clean_windows.py"):
                (root / "scripts" / name).write_text("fixture = 1\n", encoding="utf-8")
            (root / "app" / "main.py").write_text("fixture = 2\n", encoding="utf-8")
            (root / "requirements.txt").write_text("fixture==1\n", encoding="utf-8")
            commands = []
            for relative in ("scripts/build_desktop.ps1", "scripts/build_desktop_sidecar.ps1"):
                source = (ROOT / relative).read_text(encoding="utf-8")
                function = re.search(r"function Get-BackendSourceFingerprint \{.*?\n\}", source, re.S).group(0)
                command = "$ErrorActionPreference='Stop'; $env:PSModulePath=Join-Path $PSHOME 'Modules'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; $projectRoot='" + str(root).replace("'", "''") + "';\n" + function + "\nGet-BackendSourceFingerprint"
                commands.extend((shell, command) for shell in ("powershell.exe", "pwsh"))
            environment = dict(os.environ)
            # A Python child must not pass pwsh-only module discovery paths into Windows PowerShell.
            environment.pop("PSModulePath", None)
            def fingerprints():
                return [subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                                       capture_output=True, check=True, encoding="utf-8", timeout=30, env=environment).stdout.strip()
                        for shell, command in commands]
            before = fingerprints()
            self.assertEqual(len(set(before)), 1, "Parent and child shells must agree on source identity")
            (root / "scripts" / "verify.py").write_text("fixture = 3\n", encoding="utf-8")
            after = fingerprints()
            self.assertEqual(len(set(after)), 1)
            self.assertNotEqual(before[0], after[0], "Changing one source file must invalidate the manifest")


if __name__ == "__main__":
    unittest.main()
