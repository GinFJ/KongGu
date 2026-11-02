param(
  [string]$WorkerCandidate = $env:KONGGU_WORKER_CANDIDATE,
  [string]$TauriConfig = $env:KONGGU_TAURI_CONFIG,
  [string]$TargetDirectory = $env:CARGO_TARGET_DIR,
  [string]$LocalToolsRoot
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$downloads = Join-Path $repoRoot "downloads"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$nsisZip = Join-Path $downloads "nsis-3.11.zip"
$nsisUtils = Join-Path $downloads "nsis_tauri_utils.dll"

if (-not (Test-Path $python)) {
  throw "Python virtual environment not found: $python"
}

$env:CARGO_NET_OFFLINE = 'true'
if ($LocalToolsRoot) {
  $LocalToolsRoot = (Resolve-Path -LiteralPath $LocalToolsRoot).Path
  $env:LOCALAPPDATA = Join-Path $LocalToolsRoot 'local-appdata'
  if (-not $TauriConfig) { $TauriConfig = Join-Path $LocalToolsRoot 'tauri-offline.json' }
}
if ($WorkerCandidate) { $env:KONGGU_WORKER_CANDIDATE = (Resolve-Path -LiteralPath $WorkerCandidate).Path }
if ($TauriConfig) { $env:KONGGU_TAURI_CONFIG = (Resolve-Path -LiteralPath $TauriConfig).Path }
if ($TargetDirectory) { $env:CARGO_TARGET_DIR = [IO.Path]::GetFullPath($TargetDirectory) }
if ($LocalToolsRoot) {
  & $python (Join-Path $repoRoot 'tools\prepare_local_bundle_tools.py') --validate-tools $LocalToolsRoot --worker $env:KONGGU_WORKER_CANDIDATE
  if ($LASTEXITCODE -ne 0) { throw 'Prepared local build tools failed integrity validation.' }
  $selectedConfig = Join-Path $LocalToolsRoot ('tauri-selected-'+[Guid]::NewGuid().ToString('N')+'.json')
  $selectArgs = @((Join-Path $repoRoot 'tools\prepare_local_bundle_tools.py'), '--reuse-tools', $LocalToolsRoot,
    '--worker', $env:KONGGU_WORKER_CANDIDATE, '--config-output', $selectedConfig)
  if ($TauriConfig) { $selectArgs += @('--base-config', $TauriConfig) }
  & $python @selectArgs
  if ($LASTEXITCODE -ne 0) { throw 'Unable to bind offline configuration to the selected worker.' }
  $env:KONGGU_TAURI_CONFIG = $selectedConfig
  # Windows Known Folders do not follow the LOCALAPPDATA environment override.
  # The effective overlay uses bundle.useLocalToolsDir so seed the actual target.
  if (-not $env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR = Join-Path $repoRoot 'src-tauri\target' }
  $nsisSource = Join-Path $LocalToolsRoot 'local-appdata\tauri\NSIS'
  $nsisTarget = Join-Path $env:CARGO_TARGET_DIR '.tauri\NSIS'
  Get-ChildItem -LiteralPath $nsisSource -File -Recurse | ForEach-Object {
    $relative = $_.FullName.Substring($nsisSource.Length).TrimStart('\','/')
    $destination = Join-Path $nsisTarget $relative
    if (Test-Path -LiteralPath $destination) {
      if ((Get-FileHash -LiteralPath $destination).Hash -ne (Get-FileHash -LiteralPath $_.FullName).Hash) {
        throw ('Project-local NSIS cache differs; choose a fresh target directory: '+$relative)
      }
    } else {
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
      Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
  }
  Write-Output 'Verified NSIS tools prepared in the actual Cargo target cache.'
}

# Use Tauri's existing per-user cache without starting a download mirror.
# A cold cache must fail locally rather than silently fall back to the Internet.
$tauriCache = Join-Path $env:LOCALAPPDATA 'tauri'
$cachedNsis = Join-Path $tauriCache 'NSIS\Bin\makensis.exe'
$cachedPlugin = Join-Path $tauriCache 'NSIS\Plugins\x86-unicode\additional\nsis_tauri_utils.dll'
$cachedRuntime = @(Get-ChildItem -LiteralPath (Join-Path $tauriCache 'x64') -Filter 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe' -File -Recurse -ErrorAction SilentlyContinue |
  Where-Object { $_.Length -ge 10MB })
if ($LocalToolsRoot) {
  $cachedRuntime = @(Get-Item -LiteralPath (Join-Path $LocalToolsRoot 'runtime\MicrosoftEdgeWebView2RuntimeInstallerX64.exe') -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -ge 10MB })
}
if ((Test-Path -LiteralPath $cachedNsis) -and (Test-Path -LiteralPath $cachedPlugin) -and $cachedRuntime.Count -gt 0) {
  $env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR_TEMPLATE = 'http://127.0.0.1:9/<asset>'
  Write-Output 'Using existing local NSIS tools and the cached WebView2 Standalone Runtime.'
  & (Join-Path $PSScriptRoot 'build-release.cmd')
  exit $LASTEXITCODE
}

if (-not (Test-Path $nsisZip) -or -not (Test-Path $nsisUtils)) {
  throw 'Local NSIS tools or WebView2 Standalone Runtime are missing. Restore the verified local cache before this offline build; no download was attempted.'
}
if ($cachedRuntime.Count -eq 0) { throw 'The WebView2 Standalone Runtime is missing from the local cache; no download was attempted.' }

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
