$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvActivate = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$FrontendIndex = Join-Path $ProjectRoot "frontend\dist\index.html"
$PlaywrightBrowsers = Join-Path $ProjectRoot "workspace\runtime\playwright-browsers"
$DefaultCudaRuntime = "D:\Apps\NVIDIA\CUDA\v12-runtime"

Set-Location $ProjectRoot

function Write-Info([string] $Message) {
    Write-Host "[INFO] $Message"
}

function Write-Warn([string] $Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail([string] $Message) {
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Invoke-Checked([string] $Label, [scriptblock] $Command) {
    Write-Info $Label
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Test-CommandExists([string] $Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-VenvPython {
    if (-not (Test-Path $VenvPython)) {
        return $false
    }
    & $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-RequiredImports {
    if (-not (Test-VenvPython)) {
        return $false
    }
    $code = "import fastapi, uvicorn, faster_whisper, ctranslate2, av, httpx, requests, yt_dlp, playwright, opencc"
    & $VenvPython -c $code *> $null
    return $LASTEXITCODE -eq 0
}

function Find-SystemPython311 {
    if (Test-CommandExists "py") {
        & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return (& py -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
        }
    }

    foreach ($candidate in @("python", "python3")) {
        if (-not (Test-CommandExists $candidate)) {
            continue
        }
        $path = & $candidate -c "import sys; print(sys.executable if sys.version_info[:2] == (3, 11) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $path) {
            return ($path | Select-Object -First 1)
        }
    }

    return ""
}

function Ensure-Venv {
    if (Test-RequiredImports) {
        Write-Info "Python environment ready: $VenvPython"
        return
    }

    Write-Warn "Python environment is missing, broken, or incomplete. Rebuilding .venv."
    if (Test-CommandExists "uv") {
        Invoke-Checked "Create Python 3.11 virtual environment with uv" {
            & uv venv --clear --seed --python 3.11 .venv
        }
        Invoke-Checked "Install pinned Python dependencies with uv" {
            & uv pip install --python $VenvPython -r $Requirements --link-mode copy --compile-bytecode
        }
        return
    }

    $python311 = Find-SystemPython311
    if ($python311) {
        if (Test-Path (Join-Path $ProjectRoot ".venv")) {
            Remove-Item -LiteralPath (Join-Path $ProjectRoot ".venv") -Recurse -Force
        }
        Invoke-Checked "Create virtual environment with system Python 3.11" {
            & $python311 -m venv .venv
        }
        Invoke-Checked "Upgrade pip" {
            & $VenvPython -m pip install --upgrade pip
        }
        Invoke-Checked "Install pinned Python dependencies" {
            & $VenvPython -m pip install -r $Requirements
        }
        return
    }

    throw "uv or Python 3.11 was not found. Install uv first, then run start_api.bat again."
}

function Ensure-FrontendDist {
    if (Test-Path $FrontendIndex) {
        Write-Info "Frontend dist is ready."
        return
    }

    if (-not (Test-CommandExists "npm")) {
        throw "frontend/dist is missing and npm was not found. Install Node.js, then run start_api.bat again."
    }

    Push-Location (Join-Path $ProjectRoot "web")
    try {
        if (-not (Test-Path "node_modules")) {
            Invoke-Checked "Install frontend dependencies" {
                & npm ci
            }
        }
        Invoke-Checked "Build frontend dist" {
            & npm run build
        }
    } finally {
        Pop-Location
    }
}

function Test-PlaywrightChromium {
    if (-not (Test-Path $PlaywrightBrowsers)) {
        return $false
    }

    $code = @"
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
"@
    & $VenvPython -c $code *> $null
    return $LASTEXITCODE -eq 0
}

function Ensure-PlaywrightChromium {
    if (Test-PlaywrightChromium) {
        Write-Info "Playwright Chromium runtime ready."
        return
    }

    Write-Warn "Playwright Chromium is missing or version-mismatched. Installing project browser runtime."
    Invoke-Checked "Install project Playwright Chromium" {
        & $VenvPython -m playwright install chromium
    }
}

function Configure-RuntimeEnvironment {
    $env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightBrowsers

    if (-not $env:CUDA_RUNTIME_PATH -and (Test-Path (Join-Path $DefaultCudaRuntime "bin"))) {
        $env:CUDA_RUNTIME_PATH = $DefaultCudaRuntime
    }
    if (-not $env:CUDNN_PATH -and (Test-Path (Join-Path $DefaultCudaRuntime "bin"))) {
        $env:CUDNN_PATH = $DefaultCudaRuntime
    }
    foreach ($runtimePath in @($env:CUDA_RUNTIME_PATH, $env:CUDNN_PATH)) {
        if ($runtimePath -and (Test-Path (Join-Path $runtimePath "bin"))) {
            $binPath = Join-Path $runtimePath "bin"
            if (-not (($env:PATH -split ";") -contains $binPath)) {
                $env:PATH = "$binPath;$env:PATH"
            }
        }
    }
}

function Run-Doctor {
    Invoke-Checked "Run startup doctor" {
        & $VenvPython -m scripts.doctor
    }
}

try {
    Write-Info "Project directory: $ProjectRoot"
    Configure-RuntimeEnvironment
    Ensure-Venv
    Ensure-FrontendDist
    Ensure-PlaywrightChromium
    Run-Doctor

    if ($env:AUDIO2TEXT_API_PORT) {
        $displayPort = $env:AUDIO2TEXT_API_PORT
    } else {
        $displayPort = "8000"
    }
    Write-Info "Local URL: http://127.0.0.1:$displayPort"

    if ($env:AUDIO2TEXT_LOCAL_ONLY -eq "1") {
        Write-Info "Local-only mode. No public tunnel will be started."
        & $VenvPython -m scripts.start_api
    } else {
        Write-Info "Starting local API and public preview tunnel."
        Write-Info "Keep this window open while other people use the link."
        & $VenvPython -m scripts.public_preview
    }
    exit $LASTEXITCODE
} catch {
    Write-Fail $_.Exception.Message
    Write-Host ""
    Write-Host "Suggested next steps:"
    Write-Host "1. Make sure Python, PyPI, and npm sources are reachable."
    Write-Host "2. If port 8000 is busy, run: set AUDIO2TEXT_API_PORT=8001"
    Write-Host "3. For local-only diagnosis, run: set AUDIO2TEXT_LOCAL_ONLY=1"
    Write-Host "4. Run start_api.bat again."
    exit 1
}
