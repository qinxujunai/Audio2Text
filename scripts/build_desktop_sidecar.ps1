param(
    [string]$OutputRoot = "web\src-tauri\sidecar",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputRootPath = Join-Path $projectRoot $OutputRoot
$distPath = Join-Path $outputRootPath "wanxiang-api"
$workPath = Join-Path $projectRoot "tests_runtime\pyinstaller"
$specPath = Join-Path $projectRoot "tests_runtime\pyinstaller-spec"

if ($Clean) {
    Remove-Item -LiteralPath $distPath -Recurse -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $outputRootPath, $workPath, $specPath | Out-Null

Push-Location $projectRoot
try {
    $pyInstallerArguments = @(
        "--from", "pyinstaller",
        "--with-requirements", "requirements.txt",
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name", "wanxiang-api",
        "--distpath", $outputRootPath,
        "--workpath", $workPath,
        "--specpath", $specPath,
        "--collect-submodules", "app",
        "--collect-submodules", "scripts",
        "--collect-all", "faster_whisper",
        "--collect-all", "av",
        "--collect-all", "sherpa_onnx",
        "--collect-all", "playwright",
        "--collect-all", "yt_dlp",
        "scripts\desktop_sidecar.py"
    )
    Write-Host "Resolving the sidecar build from the local uv cache..."
    & uvx --offline @pyInstallerArguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The local cache is incomplete; retrying with the package index..."
        & uvx @pyInstallerArguments
    }
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller sidecar build failed." }
} finally {
    Pop-Location
}

$executable = Join-Path $distPath "wanxiang-api.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Sidecar executable was not produced: $executable"
}

Write-Host "Running packaged sidecar smoke test..."
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$smokePort = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
$smokeToken = [Guid]::NewGuid().ToString("N")
$smokeWorkspace = Join-Path $projectRoot "tests_runtime\desktop-smoke-workspace"
$stdoutPath = Join-Path $projectRoot "tests_runtime\desktop-sidecar.stdout.log"
$stderrPath = Join-Path $projectRoot "tests_runtime\desktop-sidecar.stderr.log"
$savedEnvironment = @{
    AUDIO2TEXT_API_HOST = $env:AUDIO2TEXT_API_HOST
    AUDIO2TEXT_API_PORT = $env:AUDIO2TEXT_API_PORT
    AUDIO2TEXT_RUNTIME_TARGET = $env:AUDIO2TEXT_RUNTIME_TARGET
    AUDIO2TEXT_DESKTOP_TOKEN = $env:AUDIO2TEXT_DESKTOP_TOKEN
    AUDIO2TEXT_WORKSPACE_DIR = $env:AUDIO2TEXT_WORKSPACE_DIR
    AUDIO2TEXT_ALLOW_DEGRADED_START = $env:AUDIO2TEXT_ALLOW_DEGRADED_START
}
$env:AUDIO2TEXT_API_HOST = "127.0.0.1"
$env:AUDIO2TEXT_API_PORT = "$smokePort"
$env:AUDIO2TEXT_RUNTIME_TARGET = "windows_desktop"
$env:AUDIO2TEXT_DESKTOP_TOKEN = $smokeToken
$env:AUDIO2TEXT_WORKSPACE_DIR = $smokeWorkspace
$env:AUDIO2TEXT_ALLOW_DEGRADED_START = "1"
$process = $null
try {
    $process = Start-Process -FilePath $executable -WorkingDirectory $distPath -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        if ($process.HasExited) { break }
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$smokePort/health" `
                -Headers @{ "X-Wanxiang-Desktop-Token" = $smokeToken } -TimeoutSec 2
            if ($response.status -eq "ok" -and $response.runtime_target -eq "windows_desktop") {
                $ready = $true
                break
            }
        } catch {
            # The sidecar can need a few seconds for its first import.
        }
    }
    if (-not $ready) {
        $stderr = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        throw "Packaged sidecar did not become healthy. $stderr"
    }
} finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
}

Write-Host "Sidecar ready: $executable"
