# 万象成文 — 本地全栈启动脚本
# 用法：在项目根目录运行  .\scripts\start_local.ps1

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  万象成文 — 本地开发环境" -ForegroundColor Cyan
Write-Host "  全平台可用：小红书/抖音/B站/YouTube" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 设置环境变量（本地模式 = 不经过 Relay，直接访问各平台）
$env:AUDIO2TEXT_DEPLOYMENT_MODE  = "local"
$env:AUDIO2TEXT_TRANSCRIPTION_PROVIDER = "local_faster_whisper"
$env:RELAY_RUN_MODE              = "local"

# 2. 启动后端 API
Write-Host "[1/2] 启动后端 API（含 Whisper 模型 + Playwright）..." -ForegroundColor Yellow
Start-Process -FilePath ".\\.venv\\Scripts\\python.exe" `
  -ArgumentList "-m", "scripts.start_api" `
  -NoNewWindow `
  -PassThru | Out-Null

Write-Host "       等待后端就绪..." -ForegroundColor Gray
do {
  Start-Sleep -Seconds 2
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
  } catch { $health = $null }
} while (-not $health)

Write-Host "       后端已就绪: $($health.run_mode)" -ForegroundColor Green
Write-Host ""

# 3. 启动前端
Write-Host "[2/2] 启动前端开发服务器..." -ForegroundColor Yellow
Push-Location web
try {
  npx vite --host 2>&1
} finally {
  Pop-Location
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  前端: http://localhost:5173" -ForegroundColor Green
Write-Host "  后端: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "  健康: http://127.0.0.1:8000/health" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
