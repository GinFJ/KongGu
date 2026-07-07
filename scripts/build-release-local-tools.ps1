$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$downloads = Join-Path $repoRoot "downloads"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$nsisZip = Join-Path $downloads "nsis-3.11.zip"
$nsisUtils = Join-Path $downloads "nsis_tauri_utils.dll"

if (-not (Test-Path $python)) {
  throw "Python virtual environment not found: $python"
}

if (-not (Test-Path $nsisZip) -or -not (Test-Path $nsisUtils)) {
  Write-Warning "Local Tauri tools mirror files are missing. Falling back to Tauri's default downloads."
  & cmd.exe /c (Join-Path $PSScriptRoot "build-release.cmd")
  exit $LASTEXITCODE
}

$port = 8765
while ($port -lt 8799) {
  $busy = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -ErrorAction SilentlyContinue
  if (-not $busy) {
    break
  }
  $port += 1
}

if ($port -ge 8799) {
  throw "No free localhost port found for the local Tauri tools mirror."
}

$logDir = Join-Path $repoRoot ".runtime"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "tauri-tools-mirror.out.log"
$stderr = Join-Path $logDir "tauri-tools-mirror.err.log"

$server = Start-Process `
  -FilePath $python `
  -ArgumentList @("-m", "http.server", "$port", "--bind", "127.0.0.1") `
  -WorkingDirectory $downloads `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru `
  -WindowStyle Hidden

try {
  Start-Sleep -Seconds 2
  Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/nsis-3.11.zip" -Method Head -TimeoutSec 10 | Out-Null
  $env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR_TEMPLATE = "http://127.0.0.1:$port/<asset>"
  & cmd.exe /c (Join-Path $PSScriptRoot "build-release.cmd")
  exit $LASTEXITCODE
} finally {
  Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
}
