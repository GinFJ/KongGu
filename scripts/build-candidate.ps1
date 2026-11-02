param(
  [string]$CandidateDirectory,
  [string]$TargetDirectory,
  [string]$BaseConfig = $env:KONGGU_TAURI_CONFIG,
  [string]$LocalToolsRoot,
  [switch]$WorkerOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Prepare the local build environment first; no dependencies will be downloaded.' }
$package = Get-Content -Raw -LiteralPath (Join-Path $repoRoot 'package.json') | ConvertFrom-Json
if (-not $CandidateDirectory) {
  $name = '{0}-{1}-{2}' -f $package.version, (Get-Date -Format 'yyyyMMdd-HHmmss'), [Guid]::NewGuid().ToString('N').Substring(0,8)
  $CandidateDirectory = Join-Path $repoRoot (Join-Path 'build\candidates' $name)
}
$CandidateDirectory = [IO.Path]::GetFullPath($CandidateDirectory)
if (Test-Path -LiteralPath $CandidateDirectory) { throw 'Choose a new candidate directory; existing artifacts are never overwritten.' }
New-Item -ItemType Directory -Path $CandidateDirectory | Out-Null

Push-Location $repoRoot
try {
  & $python (Join-Path $repoRoot 'tools\check_repo_privacy.py')
  if ($LASTEXITCODE -ne 0) { throw 'Privacy preflight failed.' }
  & $python (Join-Path $repoRoot 'tools\verify_release_bundle.py') --source-only
  if ($LASTEXITCODE -ne 0) { throw 'Source manifests failed preflight; no worker was built.' }
  $dist = Join-Path $CandidateDirectory 'worker-dist'
  $work = Join-Path $CandidateDirectory 'pyinstaller-work'
  & $python -m PyInstaller --noconfirm --distpath $dist --workpath $work (Join-Path $repoRoot 'KongguWorker.spec')
  if ($LASTEXITCODE -ne 0) { throw 'Worker build failed.' }
  $binaries = Join-Path $CandidateDirectory 'binaries'
  & node (Join-Path $PSScriptRoot 'rename-sidecar.mjs') --source (Join-Path $dist 'konggu-worker.exe') --target-dir $binaries --target-triple 'x86_64-pc-windows-msvc'
  if ($LASTEXITCODE -ne 0) { throw 'Candidate worker copy failed.' }
  $worker = Join-Path $binaries 'konggu-worker-x86_64-pc-windows-msvc.exe'
  & $python (Join-Path $repoRoot 'tools\verify_release_bundle.py') --worker $worker --skip-frontend-artifact
  if ($LASTEXITCODE -ne 0) { throw 'Worker payload audit failed.' }
  $env:KONGGU_WORKER_CANDIDATE = $worker
  if ($WorkerOnly) {
    Write-Output ("Candidate worker: {0}" -f $worker)
    exit 0
  }
  $isolation = Join-Path $CandidateDirectory 'worker-isolation.json'
  & $python (Join-Path $repoRoot 'tools\verify_worker_isolation.py') --worker $worker --output $isolation
  if ($LASTEXITCODE -ne 0) { throw 'Isolated worker acceptance failed.' }
  $env:KONGGU_WORKER_ISOLATION_REPORT = $isolation
  if (-not $LocalToolsRoot) {
    $LocalToolsRoot = Join-Path $repoRoot (Join-Path 'tmp' ('offline-bundle-tools-'+[Guid]::NewGuid().ToString('N')))
    $prepareArgs = @((Join-Path $repoRoot 'tools\prepare_local_bundle_tools.py'), '--output', $LocalToolsRoot, '--worker', $worker)
    if ($BaseConfig) { $prepareArgs += @('--base-config', $BaseConfig) }
    & $python @prepareArgs
    if ($LASTEXITCODE -ne 0) { throw 'Offline local-tool preparation failed.' }
  }
  $offlineConfig = Join-Path $CandidateDirectory 'tauri-offline.json'
  $configArgs = @((Join-Path $repoRoot 'tools\prepare_local_bundle_tools.py'), '--reuse-tools', $LocalToolsRoot,
    '--worker', $worker, '--config-output', $offlineConfig)
  if ($BaseConfig) { $configArgs += @('--base-config', $BaseConfig) }
  & $python @configArgs
  if ($LASTEXITCODE -ne 0) { throw 'Candidate NSIS configuration failed.' }
  $env:KONGGU_TAURI_CONFIG = $offlineConfig
  if (-not $TargetDirectory) { $TargetDirectory = Join-Path $CandidateDirectory 'cargo-target' }
  $env:CARGO_TARGET_DIR = [IO.Path]::GetFullPath($TargetDirectory)
  & (Join-Path $PSScriptRoot 'build-release-local-tools.ps1') -WorkerCandidate $worker -LocalToolsRoot $LocalToolsRoot -TauriConfig $offlineConfig -TargetDirectory $env:CARGO_TARGET_DIR
  if ($LASTEXITCODE -ne 0) { throw 'Offline desktop candidate build failed.' }
  $installer = Join-Path $env:CARGO_TARGET_DIR ("release\bundle\nsis\Konggu_{0}_x64-setup.exe" -f $package.version)
  Copy-Item -LiteralPath $installer -Destination (Join-Path $CandidateDirectory ([IO.Path]::GetFileName($installer)))
  Write-Output ("Candidate artifacts: {0}" -f $CandidateDirectory)
}
finally {
  Pop-Location
}
