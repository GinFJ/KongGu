$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$package = Get-Content -Raw (Join-Path $repoRoot "package.json") | ConvertFrom-Json
$thumbprint = ($env:KONGGU_SIGN_CERT_THUMBPRINT -replace '\s', '').ToUpperInvariant()
if (-not $thumbprint) {
  throw "Set KONGGU_SIGN_CERT_THUMBPRINT to the thumbprint of the release certificate before building."
}

$certificate = Get-ChildItem -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue
if (-not $certificate) {
  $certificate = Get-ChildItem -LiteralPath "Cert:\LocalMachine\My\$thumbprint" -ErrorAction SilentlyContinue
}
if (-not $certificate) {
  throw "Signing certificate $thumbprint was not found in CurrentUser or LocalMachine Personal store."
}
if (-not $certificate.HasPrivateKey) {
  throw "Signing certificate $thumbprint does not have an accessible private key."
}
if ($certificate.Subject -eq $certificate.Issuer) {
  throw "Signing certificate $thumbprint is self-signed; use a publicly trusted release certificate."
}
if ($certificate.NotAfter -le (Get-Date)) {
  throw "Signing certificate $thumbprint has expired."
}

$signTool = $env:KONGGU_SIGNTOOL_PATH
if (-not $signTool) {
  $signTool = "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
}
if (-not (Test-Path -LiteralPath $signTool -PathType Leaf)) {
  throw "signtool.exe not found. Set KONGGU_SIGNTOOL_PATH to the Windows SDK signtool.exe."
}

$runtimeDir = Join-Path $repoRoot ".runtime"
$configPath = Join-Path $runtimeDir "tauri-signed-config.json"
$timestampUrl = if ($env:KONGGU_SIGN_TIMESTAMP_URL) { $env:KONGGU_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
$config = @{
  bundle = @{
    windows = @{
      certificateThumbprint = $thumbprint
      digestAlgorithm = "sha256"
      timestampUrl = $timestampUrl
      tsp = $false
    }
  }
} | ConvertTo-Json -Depth 8

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
Set-Content -LiteralPath $configPath -Value $config -Encoding UTF8

$env:KONGGU_SIGN_CERT_THUMBPRINT = $thumbprint
$env:KONGGU_SIGNTOOL_PATH = (Resolve-Path -LiteralPath $signTool).Path
$env:KONGGU_SIGN_TIMESTAMP_URL = $timestampUrl
$env:KONGGU_TAURI_CONFIG = $configPath

try {
  Push-Location $repoRoot
  & (Join-Path $PSScriptRoot "build-release.cmd")
  if ($LASTEXITCODE -ne 0) {
    throw "Signed release build failed with exit code $LASTEXITCODE."
  }

  $signScript = Join-Path $PSScriptRoot "sign-windows-artifact.ps1"
  $mainBinary = Join-Path $repoRoot "src-tauri\target\release\konggu.exe"
  $installer = Join-Path $repoRoot ("src-tauri\target\release\bundle\nsis\Konggu_{0}_x64-setup.exe" -f $package.version)
  $installerScript = Join-Path $repoRoot "src-tauri\target\release\nsis\x64\installer.nsi"
  $installerScriptText = Get-Content -Raw -LiteralPath $installerScript
  if ($installerScriptText -notmatch '!define INSTALLWEBVIEW2MODE "offlineInstaller"') {
    throw "The generated NSIS script is not configured for offline WebView2 installation."
  }
  if ($installerScriptText -notmatch '!define WEBVIEW2INSTALLERPATH "[^"]+"') {
    throw "The generated NSIS script does not embed a WebView2 Standalone Runtime installer."
  }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $signScript -Path $mainBinary
  if ($LASTEXITCODE -ne 0) { throw "Signing the main executable failed." }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $signScript -Path $installer
  if ($LASTEXITCODE -ne 0) { throw "Signing the NSIS installer failed." }

  foreach ($artifact in @($mainBinary, (Join-Path $repoRoot "src-tauri\binaries\konggu-worker-x86_64-pc-windows-msvc.exe"), $installer)) {
    $signature = Get-AuthenticodeSignature -LiteralPath $artifact
    if ($signature.Status -eq "NotSigned") {
      throw "Release artifact is not signed: $artifact"
    }
    Write-Output ("Release signature: {0} -> {1}" -f $artifact, $signature.Status)
  }
}
finally {
  Pop-Location
  if (Test-Path -LiteralPath $configPath) {
    Remove-Item -LiteralPath $configPath -Force -ErrorAction SilentlyContinue
  }
}
