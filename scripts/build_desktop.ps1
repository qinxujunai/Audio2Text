param(
    [switch]$Debug,
    [switch]$SkipSidecar
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot "web"
$distRoot = Join-Path $projectRoot "frontend\dist"
$manifestPath = Join-Path $distRoot "build-manifest.json"
$configuration = if ($Debug) { "debug" } else { "release" }
$buildStartedAt = Get-Date
$snapshotPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
function Get-SourceSnapshot {
    $snapshot = & $snapshotPython (Join-Path $PSScriptRoot 'build_fingerprint.py')
    if ($LASTEXITCODE -ne 0) { throw 'Could not fingerprint the repository snapshot.' }
    return $snapshot | ConvertFrom-Json
}
function Get-BackendSourceFingerprint {
    $sourceFiles = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'app'), (Join-Path $projectRoot 'scripts') -Filter '*.py' -Recurse -File)
    $sourceFiles += Get-Item -LiteralPath (Join-Path $projectRoot 'requirements.txt')
    [string[]]$sourcePaths = @($sourceFiles | ForEach-Object { $_.FullName })
    [Array]::Sort($sourcePaths, [StringComparer]::Ordinal)
    $lines = foreach ($sourcePath in $sourcePaths) {
        $relative = $sourcePath.Substring($projectRoot.Length).Replace('\', '/')
        $relative + ':' + (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))))).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}
$sourceSnapshot = Get-SourceSnapshot

function Resolve-RequiredTool([string]$name) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Required tool '$name' is not available. Install the supported toolchain first; no desktop artifact was built."
    }
    return $command.Source
}

function Invoke-Checked([string]$label, [string]$filePath, [string[]]$arguments, [string]$workingDirectory) {
    Write-Host "`n==> $label"
    Write-Host "$filePath $($arguments -join ' ')"
    Push-Location $workingDirectory
    try {
        & $filePath @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$label failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}

$node = Resolve-RequiredTool "node"
$npm = Resolve-RequiredTool "npm"
$rustup = Resolve-RequiredTool "rustup"
Resolve-RequiredTool "cargo" | Out-Null
Resolve-RequiredTool "rustc" | Out-Null
$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$activeToolchain = (& $rustup "show" "active-toolchain" 2>&1 | Out-String).Trim()
$rustupExitCode = $LASTEXITCODE
$ErrorActionPreference = $savedErrorActionPreference
if ($rustupExitCode -ne 0 -or $activeToolchain -match "no active toolchain") {
    throw "Rust is installed only as a rustup shim, but no active toolchain is configured. Install/select a stable Rust toolchain, then rerun this script; no desktop artifact was built."
}

$fingerprintJson = & $node (Join-Path $webRoot "scripts\frontend_fingerprint.mjs") --json
if ($LASTEXITCODE -ne 0) {
    throw "Could not calculate the frontend source fingerprint."
}
$expectedFingerprint = ($fingerprintJson | ConvertFrom-Json).source_fingerprint
$manifest = $null
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
}

if (-not $manifest -or $manifest.source_fingerprint -ne $expectedFingerprint) {
    Write-Host "Frontend artifact is stale or missing; rebuilding before the desktop package."
    Invoke-Checked "Frontend build" $npm @("run", "build") $webRoot
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
}

if ($manifest.source_fingerprint -ne $expectedFingerprint) {
    throw "Frontend build-manifest.json does not match the current source fingerprint; refusing to package stale UI."
}

$forbiddenUiTokens = @(
    "processing-mascot",
    [string]::Concat([char]0x5361, [char]0x5431),
    ("Windows " + [char]0x7248)
)
$frontendAssets = Get-ChildItem -LiteralPath $distRoot -Recurse -File | Where-Object { $_.Extension -in @(".html", ".css", ".js", ".svg") }
foreach ($token in $forbiddenUiTokens) {
    $matches = $frontendAssets | Select-String -Pattern $token -SimpleMatch -ErrorAction SilentlyContinue
    if ($matches) {
        $locations = ($matches | Select-Object -ExpandProperty Path -Unique) -join ", "
        throw "Frontend artifact contains forbidden legacy UI token '$token' ($locations); refusing to package stale UI."
    }
}

if (-not $SkipSidecar) {
    Invoke-Checked "Desktop sidecar build and smoke" "powershell.exe" @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $projectRoot "scripts\build_desktop_sidecar.ps1")
    ) $projectRoot
}
$sidecarRoot = Join-Path $webRoot 'src-tauri\sidecar\wanxiang-api'
$sidecarManifest = Get-Content -LiteralPath (Join-Path $sidecarRoot 'sidecar-build-manifest.json') -Raw -Encoding utf8 | ConvertFrom-Json
$resourceJson = & $snapshotPython (Join-Path $PSScriptRoot 'build_fingerprint.py') --resources $sidecarRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not fingerprint the complete sidecar resource tree.' }
$resourceSnapshot = $resourceJson | ConvertFrom-Json
if (-not $sidecarManifest.resource_tree_sha256 -or $resourceSnapshot.resource_tree_sha256 -ne $sidecarManifest.resource_tree_sha256) {
    throw 'Sidecar DLL/resource tree changed or lacks a complete manifest. Rebuild and validate it before packaging.'
}
if ($sidecarManifest.python_version -ne '3.11' -or $sidecarManifest.source_fingerprint -ne (Get-BackendSourceFingerprint) -or $sidecarManifest.executable_sha256 -ne (Get-FileHash -LiteralPath (Join-Path $sidecarRoot 'wanxiang-api.exe')).Hash.ToLowerInvariant()) {
    throw 'Sidecar manifest does not match the current backend sources and executable. Rebuild the sidecar before packaging.'
}

$tauriArguments = @((Join-Path $webRoot 'node_modules\@tauri-apps\cli\tauri.js'), 'build', '--bundles', 'nsis')
if ($Debug) {
    $tauriArguments += "--debug"
}
Invoke-Checked "Tauri $configuration package" $node $tauriArguments $webRoot
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($manifest.source_fingerprint -ne $expectedFingerprint) { throw 'Tauri frontend rebuild changed source identity; refusing the package.' }

$targetRoot = Join-Path $webRoot "src-tauri\target\$configuration"
$executable = Join-Path $targetRoot "wanxiang-chengwen.exe"
$installerRoot = Join-Path $targetRoot "bundle\nsis"
$freshExecutable = (Test-Path -LiteralPath $executable) -and ((Get-Item -LiteralPath $executable).LastWriteTime -ge $buildStartedAt)
$freshInstaller = @(
    Get-ChildItem -LiteralPath $installerRoot -Filter "*.exe" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $buildStartedAt }
)

if (-not $freshExecutable) {
    throw "Tauri did not produce a fresh $configuration executable at $executable; refusing to report an old package as current."
}
if ($freshInstaller.Count -ne 1) {
    throw "Tauri must produce exactly one fresh NSIS installer under $installerRoot; refusing an ambiguous package."
}

if ((Get-SourceSnapshot).source_fingerprint -ne $sourceSnapshot.source_fingerprint) {
    throw 'Repository files changed during packaging; this installer is not a frozen delivery. Rebuild after integration stops.'
}
$finalResources = & $snapshotPython (Join-Path $PSScriptRoot 'build_fingerprint.py') --resources $sidecarRoot
if ($LASTEXITCODE -ne 0 -or ($finalResources | ConvertFrom-Json).resource_tree_sha256 -ne $resourceSnapshot.resource_tree_sha256) { throw 'Sidecar resources changed during packaging; refusing a mixed installer.' }
$sourceSnapshot | Add-Member -NotePropertyName frontend_build_id -NotePropertyValue $manifest.build_id
$sourceSnapshot | Add-Member -NotePropertyName installer_sha256 -NotePropertyValue (Get-FileHash -LiteralPath $freshInstaller[0].FullName).Hash.ToLowerInvariant()
$sourceSnapshot | Add-Member -NotePropertyName version -NotePropertyValue ((Get-Content (Join-Path $webRoot 'src-tauri\tauri.conf.json') -Raw -Encoding utf8 | ConvertFrom-Json).version)
$sourceSnapshot | Add-Member -NotePropertyName main_sha256 -NotePropertyValue (Get-FileHash -LiteralPath $executable).Hash.ToLowerInvariant()
$sourceSnapshot | Add-Member -NotePropertyName backend_sha256 -NotePropertyValue (Get-FileHash -LiteralPath (Join-Path $webRoot 'src-tauri\sidecar\wanxiang-api\wanxiang-api.exe')).Hash.ToLowerInvariant()
$sourceSnapshot | Add-Member -NotePropertyName resource_tree_sha256 -NotePropertyValue $resourceSnapshot.resource_tree_sha256
$sourceSnapshot | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $targetRoot 'delivery-build-manifest.json') -Encoding utf8

Write-Host "`nDesktop package verified."
Write-Host "Frontend build id: $($manifest.build_id)"
Write-Host "Executable: $executable"
Write-Host "Installer: $($freshInstaller[0].FullName)"
