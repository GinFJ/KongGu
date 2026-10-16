param(
  [Parameter(Mandatory = $true)]
  [string]$Path
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-SignTool {
  if ($env:KONGGU_SIGNTOOL_PATH) {
    $configured = [Environment]::ExpandEnvironmentVariables($env:KONGGU_SIGNTOOL_PATH)
    if (Test-Path -LiteralPath $configured -PathType Leaf) {
      return (Resolve-Path -LiteralPath $configured).Path
    }
    throw "KONGGU_SIGNTOOL_PATH does not point to a file: $configured"
  }

  $candidates = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.22000.0\x64\signtool.exe"
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return $candidate
    }
  }
  throw "signtool.exe not found. Set KONGGU_SIGNTOOL_PATH to the Windows SDK signtool.exe."
}

function Resolve-SigningCertificate {
  $thumbprint = ($env:KONGGU_SIGN_CERT_THUMBPRINT -replace '\s', '').ToUpperInvariant()
  if (-not $thumbprint) {
    throw "KONGGU_SIGN_CERT_THUMBPRINT is required; no release signing certificate was selected."
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
  return $certificate
}

$resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
$signTool = Resolve-SignTool
$certificate = Resolve-SigningCertificate
$timestampUrl = $env:KONGGU_SIGN_TIMESTAMP_URL
if (-not $timestampUrl) {
  $timestampUrl = "http://timestamp.digicert.com"
}

& $signTool sign /fd SHA256 /sha1 $certificate.Thumbprint /tr $timestampUrl /td SHA256 $resolvedPath
if ($LASTEXITCODE -ne 0) {
  throw "signtool failed for $resolvedPath with exit code $LASTEXITCODE."
}

$signature = Get-AuthenticodeSignature -LiteralPath $resolvedPath
if ($signature.Status -eq "NotSigned") {
  throw "signtool returned success but the artifact is still unsigned: $resolvedPath"
}
if (-not $signature.SignerCertificate) {
  throw "The artifact has no signer certificate after signing: $resolvedPath"
}
if ($signature.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
  throw "The artifact was signed by an unexpected certificate: $resolvedPath"
}

& $signTool verify /pa /all $resolvedPath
if ($LASTEXITCODE -ne 0) {
  throw "signtool verification failed for $resolvedPath with exit code $LASTEXITCODE."
}

Write-Output ("Signed {0} with {1}; signature status: {2}" -f $resolvedPath, $certificate.Thumbprint, $signature.Status)
