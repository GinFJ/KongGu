[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
if ([string]::IsNullOrWhiteSpace($desktop)) {
    $desktop = [Environment]::GetFolderPath("UserProfile")
}
$outputPath = Join-Path $desktop "Konggu_OCR_Diagnostic_Result.txt"

function Restore-EnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()][string]$Value
    )

    if ($null -eq $Value) {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    } else {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

try {
    $publicDocuments = [Environment]::GetFolderPath("CommonDocuments")
    if ([string]::IsNullOrWhiteSpace($publicDocuments)) {
        $publicDocuments = "C:\Users\Public\Documents"
    }

    $diagnosticRoot = Join-Path $publicDocuments "KongguDiag"
    $diagnosticTemp = Join-Path $publicDocuments "KongguTemp"
    $requestPath = Join-Path $diagnosticTemp "konggu-request.json"
    New-Item -ItemType Directory -Force -Path $diagnosticRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $diagnosticTemp | Out-Null
    [System.IO.File]::WriteAllText(
        $requestPath,
        '{"command":"resources.repair"}',
        [System.Text.Encoding]::ASCII
    )

    $workerProcess = Get-CimInstance Win32_Process |
        Where-Object { $_.Name -like "konggu-worker*.exe" -and $_.ExecutablePath } |
        Select-Object -First 1

    if ($null -eq $workerProcess) {
        throw "没有找到正在运行的 konggu-worker.exe。请先打开空谷，再运行本文件。"
    }

    $workerPath = [string]$workerProcess.ExecutablePath
    $oldAppDataRoot = $env:KONGGU_APP_DATA_ROOT
    $oldTemp = $env:TEMP
    $oldTmp = $env:TMP

    try {
        # Use ASCII-only paths for this diagnostic so PaddleOCR's Windows path handling
        # can be tested independently of the user's Chinese profile path.
        $env:KONGGU_APP_DATA_ROOT = $diagnosticRoot
        $env:TEMP = $diagnosticTemp
        $env:TMP = $diagnosticTemp
        $rawOutput = (& $workerPath --request $requestPath 2>&1 | Out-String)
    } finally {
        Restore-EnvironmentVariable -Name "KONGGU_APP_DATA_ROOT" -Value $oldAppDataRoot
        Restore-EnvironmentVariable -Name "TEMP" -Value $oldTemp
        Restore-EnvironmentVariable -Name "TMP" -Value $oldTmp
    }

    $report = @(
        "空谷 OCR 诊断结果"
        "时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        "worker：$workerPath"
        "诊断资源目录：$diagnosticRoot"
        "诊断临时目录：$diagnosticTemp"
        ""
        $rawOutput.TrimEnd()
    ) -join [Environment]::NewLine

    [System.IO.File]::WriteAllText($outputPath, $report, [System.Text.Encoding]::UTF8)
    Write-Host $report
    Write-Host ""
    Write-Host "诊断结果已保存到：$outputPath" -ForegroundColor Green

    $jsonLine = ($rawOutput -split "\r?\n" |
        Where-Object { $_.TrimStart().StartsWith("{") } |
        Select-Object -Last 1)
    if ($jsonLine) {
        try {
            $result = $jsonLine | ConvertFrom-Json
            Write-Host ""
            Write-Host "ASCII 路径测试 ready：$($result.status.ready)" -ForegroundColor Yellow
            Write-Host "OCR 初始化阶段：$($result.status.ocr.runtime.stage)" -ForegroundColor Yellow
            Write-Host "OCR 错误：$($result.status.ocr.runtime.error)" -ForegroundColor Yellow
        } catch {
            Write-Host "未能单独解析 JSON，但完整原始结果已保存。" -ForegroundColor Yellow
        }
    }
} catch {
    $errorReport = @(
        "空谷 OCR 诊断失败"
        "时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        "错误：$($_.Exception.Message)"
    ) -join [Environment]::NewLine
    try {
        [System.IO.File]::WriteAllText($outputPath, $errorReport, [System.Text.Encoding]::UTF8)
    } catch {
        # The console message below remains available if the desktop is not writable.
    }
    Write-Host $errorReport -ForegroundColor Red
    Write-Host "错误记录已尝试保存到：$outputPath" -ForegroundColor Yellow
    Write-Host "请确认空谷已经打开，并以 PowerShell 运行此文件。" -ForegroundColor Yellow
} finally {
    try {
        Read-Host "诊断结束。按 Enter 键关闭窗口" | Out-Null
    } catch {
        # Non-interactive PowerShell hosts do not need the pause.
    }
}
