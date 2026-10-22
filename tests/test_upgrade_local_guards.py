"""Exercise the shipped read-only path guard without invoking an installation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows upgrade guard")
class UpgradeDirectoryGuardTest(unittest.TestCase):
    def test_nsis_allows_missing_directory_without_losing_win32_error(self):
        compiler = Path.home() / 'AppData/Local/tauri/NSIS/Bin/makensis.exe'
        if not compiler.exists():
            self.skipTest('NSIS compiler is not prepared yet')
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project / 'tests_runtime') as temporary:
            root = Path(temporary)
            setup = root / 'guard.exe'
            source = root / 'guard.nsi'
            source.write_text(f'''Unicode true
!include LogicLib.nsh
!define MAINBINARYNAME "wanxiang-chengwen"
!define VERSION "test"
!include "{project / 'web/src-tauri/windows/hooks.nsh'}"
Name "Read-only path test"
OutFile "{setup}"
RequestExecutionLevel user
SilentInstall silent
InstallDir "{root / 'missing'}"
Section
!insertmacro WanxiangRejectLinkedParents "$INSTDIR\\sidecar\\wanxiang-api"
SectionEnd
''', encoding='utf-8-sig')
            subprocess.run([str(compiler), '/V2', str(source)], check=True, capture_output=True)
            self.assertEqual(subprocess.run([str(setup)], timeout=20).returncode, 0)
            self.assertFalse((root / 'missing').exists())

    def test_windows_powershell_preserves_chinese_install_path_and_json(self):
        project = Path(__file__).resolve().parents[1]
        for relative in ('scripts/upgrade_local.ps1', 'scripts/build_desktop.ps1', 'scripts/build_desktop_sidecar.ps1'):
            self.assertTrue((project / relative).read_bytes().startswith(b'\xef\xbb\xbf'))
        output = subprocess.run(['powershell.exe', '-NoProfile', '-Command', "[Console]::OutputEncoding=[Text.Encoding]::UTF8; (Get-Content -LiteralPath web/src-tauri/tauri.conf.json -Raw -Encoding utf8 | ConvertFrom-Json).productName"], cwd=project, check=True, capture_output=True, encoding='utf-8')
        self.assertEqual(output.stdout.strip(), '万象成文')

    def test_rejects_linked_parent_and_child_before_traversal(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project / "tests_runtime") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "sentinel.txt").write_text("preserve", encoding="utf-8")
            link = root / "linked"
            script = root / "guard.ps1"
            script.write_text("""$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath $args[0] -Raw
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Invalid upgrade script.' }
$function = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Assert-DirectoryTreeSafe'},$true)
Invoke-Expression $function.Extent.Text
Assert-DirectoryTreeSafe $args[1]
""", encoding="utf-8-sig")
            command = ["powershell.exe", "-NoProfile", "-File", str(script), str(project / "scripts/upgrade_local.ps1")]
            clean = subprocess.run(command + [str(target)], capture_output=True)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            junction_script = root / "junction.ps1"
            junction_script.write_text("$ErrorActionPreference='Stop'\n$null=New-Item -ItemType Junction -Path $args[0] -Target $args[1]\n", encoding="utf-8-sig")
            subprocess.run(["powershell.exe", "-NoProfile", "-File", str(junction_script), str(link), str(target)], check=True, capture_output=True)
            try:
                # Parent is a junction; the child itself is an ordinary directory.
                (target / "runtime").mkdir()
                linked_parent = subprocess.run(command + [str(link / "runtime")], capture_output=True)
                self.assertNotEqual(linked_parent.returncode, 0)
                # A linked child must be detected before descending into its tree.
                linked_child = subprocess.run(command + [str(root)], capture_output=True)
                self.assertNotEqual(linked_child.returncode, 0)
                self.assertEqual((target / "sentinel.txt").read_text(encoding="utf-8"), "preserve")
            finally:
                # Remove only the link, never recurse into its target.
                os.rmdir(link)


if __name__ == "__main__":
    unittest.main()
