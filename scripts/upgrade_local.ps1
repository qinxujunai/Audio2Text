param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [Parameter(Mandatory=$true)][string]$VerificationReport,
    [string]$InstallDir = 'D:\Apps\万象成文',
    [switch]$NativeDebug
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$installRoot = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$appPath = Join-Path $installRoot 'wanxiang-chengwen.exe'
$backendPath = Join-Path $installRoot 'sidecar\wanxiang-api\wanxiang-api.exe'
$dataRoot = Join-Path $env:LOCALAPPDATA 'online.praxisai.wanxiang\workspace'
$setupPath = (Resolve-Path -LiteralPath $Installer).Path
$report = Get-Content -LiteralPath $VerificationReport -Raw -Encoding utf8 | ConvertFrom-Json
$candidateBackend = Join-Path $projectRoot 'web\src-tauri\sidecar\wanxiang-api\wanxiang-api.exe'
$candidateMain = Join-Path $projectRoot 'web\src-tauri\target\release\wanxiang-chengwen.exe'
$version = (Get-Content (Join-Path $projectRoot 'web\src-tauri\tauri.conf.json') -Raw -Encoding utf8 | ConvertFrom-Json).version
function Assert-DirectoryTreeSafe([string]$Root) {
    $directory = Get-Item -LiteralPath $Root -Force
    $ancestor = $directory
    while ($ancestor) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Upgrade path must not traverse a junction or symbolic link.' }
        $ancestor = $ancestor.Parent
    }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($directory.FullName)
    while ($pending.Count) {
        foreach ($entry in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Upgrade directory contains a junction or symbolic link.' }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName) }
        }
    }
}
if ($installRoot -ne 'D:\Apps\万象成文') { throw 'This machine upgrade is restricted to the existing installation directory.' }
Assert-DirectoryTreeSafe $installRoot
Assert-DirectoryTreeSafe (Join-Path $dataRoot 'captures')
Assert-DirectoryTreeSafe (Split-Path -Parent $candidateBackend)
$deliveryManifest = Get-Content -LiteralPath (Join-Path $projectRoot 'web\src-tauri\target\release\delivery-build-manifest.json') -Raw | ConvertFrom-Json
$currentSnapshot = & (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'build_fingerprint.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the frozen source snapshot.' }
if (($currentSnapshot | ConvertFrom-Json).source_fingerprint -ne $deliveryManifest.source_fingerprint) { throw 'Source changed after frozen packaging. Rebuild and validate before installation.' }
if ($deliveryManifest.version -ne $version -or $deliveryManifest.installer_sha256 -ne $ExpectedSha256 -or $deliveryManifest.backend_sha256 -ne (Get-FileHash -LiteralPath $candidateBackend).Hash.ToLowerInvariant() -or $deliveryManifest.main_sha256 -ne (Get-FileHash -LiteralPath $candidateMain).Hash.ToLowerInvariant()) { throw 'Frozen build manifest does not match the candidate installer and executable files.' }
if ($ExpectedSha256 -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $setupPath).Hash -ne $ExpectedSha256) { throw 'Installer digest mismatch.' }
if ($report.health.version -ne $version -or $report.cuda_check.device -ne 'cuda' -or -not $report.proofreading.original_unchanged) { throw 'Packaged acceptance is missing or failed.' }
if ((Get-FileHash -LiteralPath $candidateBackend).Hash -ne $report.sha256) { throw 'Backend changed after packaged acceptance.' }
if (-not (Test-Path -LiteralPath $appPath)) { throw 'Original installed app was not found; refusing to relocate or make a new installation.' }
if (Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue) { throw 'Native verification port is occupied.' }
function Assert-Idle {
foreach ($record in Get-ChildItem -LiteralPath (Join-Path $dataRoot 'captures') -Filter capture.json -Recurse -File) {
    $capture = Get-Content -LiteralPath $record.FullName -Raw -Encoding utf8 | ConvertFrom-Json
    if ($capture.status -in @('queued','processing')) { throw 'A capture is active. Wait before upgrading.' }
}
# Do not interrupt an active proofreading request. A persisted running checkpoint is
# conservatively treated as active while the current backend is alive.
$liveBackend = @(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $backendPath})
if ($liveBackend.Count) {
    foreach ($checkpoint in Get-ChildItem -LiteralPath (Join-Path $dataRoot 'captures') -Filter proofreading-job.json -Recurse -File) {
        if ((Get-Content -LiteralPath $checkpoint.FullName -Raw -Encoding utf8 | ConvertFrom-Json).status -eq 'running') { throw 'Proofreading may be active. Cancel or close the app before upgrading.' }
    }
}
}
function Stop-InstalledApp {
    Assert-Idle
    foreach ($target in Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -in @($appPath,$backendPath)}) {
        $process = Get-Process -Id $target.ProcessId -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        if ($target.ExecutablePath -eq $appPath) {
            $null = $process.CloseMainWindow()
            if ($process.WaitForExit(5000)) { continue }
        }
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Assert-Idle
Stop-InstalledApp
$backupRoot = Join-Path $projectRoot ('workspace\temp\product-rollback-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$validatedInstaller = Join-Path $backupRoot 'validated-installer.exe'
Copy-Item -LiteralPath $setupPath -Destination $validatedInstaller
if ((Get-FileHash -LiteralPath $validatedInstaller).Hash -ne $ExpectedSha256) { throw 'Could not preserve the validated installer.' }
Assert-DirectoryTreeSafe $installRoot
Copy-Item -LiteralPath $installRoot -Destination (Join-Path $backupRoot 'app') -Recurse
$metadataBackup = Join-Path $backupRoot 'metadata'
New-Item -ItemType Directory -Path $metadataBackup | Out-Null
$history = @()
foreach ($record in Get-ChildItem -LiteralPath (Join-Path $dataRoot 'captures') -Filter '*.json' -Recurse -File) {
    $relative = $record.FullName.Substring($dataRoot.Length + 1)
    $destination = Join-Path $metadataBackup $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $record.FullName -Destination $destination
    $history += @{path=$relative;sha256=(Get-FileHash -LiteralPath $record.FullName).Hash.ToLowerInvariant()}
}
$preferences = Join-Path $dataRoot 'local-preferences.json'
if (Test-Path -LiteralPath $preferences) { Copy-Item -LiteralPath $preferences -Destination $metadataBackup }
@{install_dir=$installRoot;data_root=$dataRoot;version=$version;installer_sha256=$ExpectedSha256;history=$history} | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $backupRoot 'upgrade-manifest.json') -Encoding utf8

try {
    if (Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -in @($appPath,$backendPath)}) { throw 'Application restarted during backup; stop here.' }
    # NSIS /UPDATE can leave DLLs that disappeared from a newer PyInstaller
    # runtime. Replace this owned folder after backup, rather than overlay it.
    $oldSidecarRoot = [IO.Path]::GetFullPath((Split-Path -Parent $backendPath))
    if ($oldSidecarRoot -ne 'D:\Apps\万象成文\sidecar\wanxiang-api') { throw 'Sidecar replacement target validation failed.' }
    Assert-DirectoryTreeSafe $oldSidecarRoot
    if ((Get-FileHash -LiteralPath $validatedInstaller).Hash -ne $ExpectedSha256) { throw 'Preserved installer changed; stop before replacing application files.' }
    Remove-Item -LiteralPath $oldSidecarRoot -Recurse -Force
    $setup = Start-Process -WindowStyle Hidden -FilePath $validatedInstaller -ArgumentList @('/S','/UPDATE',('/D=' + $installRoot)) -PassThru -Wait
    if ($setup.ExitCode -ne 0) { throw 'Installer returned a failure.' }
    if ((Get-FileHash -LiteralPath $backendPath).Hash -ne (Get-FileHash -LiteralPath $candidateBackend).Hash) { throw 'Installed backend mismatch.' }
    $candidateSidecarRoot = Split-Path -Parent $candidateBackend
    $installedSidecarRoot = Split-Path -Parent $backendPath
    $expectedFiles = @(Get-ChildItem -LiteralPath $candidateSidecarRoot -Recurse -File)
    $installedFiles = @(Get-ChildItem -LiteralPath $installedSidecarRoot -Recurse -File)
    if ($expectedFiles.Count -ne $installedFiles.Count) { throw 'Installed sidecar file set mismatch.' }
    foreach ($file in $expectedFiles) {
        $relative = $file.FullName.Substring($candidateSidecarRoot.Length + 1)
        $installedFile = Join-Path $installedSidecarRoot $relative
        if (-not (Test-Path -LiteralPath $installedFile) -or (Get-FileHash -LiteralPath $installedFile).Hash -ne (Get-FileHash -LiteralPath $file.FullName).Hash) { throw "Installed sidecar resource mismatch: $relative" }
    }
    # NSIS changes only the Tauri bundle marker. Compare every other byte.
    $expectedBytes = [IO.File]::ReadAllBytes($candidateMain)
    $actualBytes = [IO.File]::ReadAllBytes($appPath)
    $expectedEncoded = [Text.Encoding]::GetEncoding(28591).GetString($expectedBytes)
    $actualEncoded = [Text.Encoding]::GetEncoding(28591).GetString($actualBytes)
    if ($expectedEncoded.Replace('__TAURI_BUNDLE_TYPE_VAR_UNK','__TAURI_BUNDLE_TYPE_VAR_NSS') -cne $actualEncoded) { throw 'Installed main executable mismatch.' }
    foreach ($item in $history) {
        if ((Get-FileHash -LiteralPath (Join-Path $dataRoot $item.path)).Hash -ne $item.sha256) { throw 'History metadata changed during installation.' }
    }
    $savedArguments = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
    try {
        if ($NativeDebug) { $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1' }
        else { $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=$null }
        $newApp = Start-Process -FilePath $appPath -PassThru
    } finally { $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=$savedArguments }
    @{version=$version;installed_main_sha256=(Get-FileHash -LiteralPath $appPath).Hash.ToLowerInvariant();installed_backend_sha256=(Get-FileHash -LiteralPath $backendPath).Hash.ToLowerInvariant();installer_sha256=$ExpectedSha256;backup=$backupRoot;pid=$newApp.Id;native_acceptance_pending=$true} | ConvertTo-Json | Set-Content (Join-Path $backupRoot 'installed-artifacts.json') -Encoding utf8
    Write-Output "Installed candidate $version; backup: $backupRoot. Native runtime acceptance remains required."
} catch {
    Stop-InstalledApp
    # Exact existing app directory only, after its full backup. No user data or
    # shortcuts are removed or moved.
    if ([IO.Path]::GetFullPath($installRoot).TrimEnd('\') -ne 'D:\Apps\万象成文') { throw 'Rollback target validation failed.' }
    Assert-DirectoryTreeSafe $installRoot
    Remove-Item -LiteralPath $installRoot -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $backupRoot 'app') -Destination $installRoot -Recurse
    foreach ($item in $history) {
        Copy-Item -LiteralPath (Join-Path $metadataBackup $item.path) -Destination (Join-Path $dataRoot $item.path) -Force
        if ((Get-FileHash -LiteralPath (Join-Path $dataRoot $item.path)).Hash -ne $item.sha256) { throw "Metadata recovery failed. Stop and use backup: $backupRoot" }
    }
    $preferencesBackup = Join-Path $metadataBackup 'local-preferences.json'
    if (Test-Path -LiteralPath $preferencesBackup) { Copy-Item -LiteralPath $preferencesBackup -Destination $preferences -Force }
    throw "Upgrade failed; original program and metadata restored. Backup: $backupRoot. $($_.Exception.Message)"
}
