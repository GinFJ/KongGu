param([string]$WorkerPath, [string]$OutputRoot)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility
Add-Type -AssemblyName System.IO.Compression.FileSystem
$expectedHash = 'c0f68906660be9856587c209ee49f4a4ca77ea955be3543f86bbf6c9fd6bca50'
$steps = New-Object 'Collections.Generic.List[object]'
$report = [ordered]@{tool_version='1.0';application_version='0.9.0';ok=$false;steps=$steps;
  real_timetable_used=$false;api_request_sent=$false;physical_network_disabled=$false;
  fresh_os_or_new_account=$false;installation_tested=$false}
$process = $null; $errorTask = $null; $restoreAcls = @{}; $started = Get-Date
function Check($condition, [string]$message) { if (-not $condition) { throw $message } }
function Passed([string]$name) {
  $steps.Add([ordered]@{step=$name;ok=$true})
  Write-Host ('通过：' + $name)
}
function Send([string]$command, [hashtable]$payload=@{}, [switch]$AllowFailure) {
  $script:counter += 1; $id = [string]$script:counter
  $json = @{id=$id;command=$command;payload=$payload} | ConvertTo-Json -Depth 30 -Compress
  $script:inputWriter.WriteLine($json); $script:inputWriter.Flush()
  $deadline = [DateTime]::UtcNow.AddSeconds(210)
  while ($true) {
    $remaining = [int][Math]::Max(0, ($deadline-[DateTime]::UtcNow).TotalMilliseconds)
    if ($remaining -eq 0) { throw ('识别程序等待超时：'+$command) }
    $lineTask = $process.StandardOutput.ReadLineAsync()
    if (-not $lineTask.Wait($remaining)) { throw ('识别程序等待超时：'+$command) }
    $line = $lineTask.Result
    if ($null -eq $line) { throw ('识别程序提前退出：'+$command) }
    $message = $line | ConvertFrom-Json
    if ($message.event -eq 'sidecar.fatal') { throw $message.message }
    if ($message.type -eq 'response' -and [string]$message.id -eq $id) {
      $result = if ($message.ok) { $message.data } else { $message }
      if (-not $AllowFailure -and -not $result.ok) { throw ($command+'：'+$result.error) }
      return $result
    }
  }
}
function Start-Worker {
  $info = New-Object Diagnostics.ProcessStartInfo
  $info.FileName = $script:copiedWorker; $info.Arguments = '--serve'; $info.WorkingDirectory = $script:install
  $info.UseShellExecute = $false; $info.CreateNoWindow = $true
  $info.RedirectStandardInput = $true; $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
  $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
  $info.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
  $info.EnvironmentVariables.Clear()
  foreach ($entry in $script:environment.GetEnumerator()) { $info.EnvironmentVariables[$entry.Key] = [string]$entry.Value }
  $script:process = New-Object Diagnostics.Process
  $script:process.StartInfo = $info
  Check ($script:process.Start()) '无法启动包内识别程序。'
  $script:inputWriter = New-Object IO.StreamWriter($script:process.StandardInput.BaseStream, (New-Object Text.UTF8Encoding($false)))
  $script:errorTask = $script:process.StandardError.ReadToEndAsync()
  $script:counter = 0
}
function Stop-Worker {
  if ($null -eq $script:process) { return }
  if (-not $script:process.HasExited) {
    $script:inputWriter.Close()
    if (-not $script:process.WaitForExit(15000)) {
      & taskkill.exe /PID $script:process.Id /T /F | Out-Null
      throw '识别程序退出超时，已停止本工具创建的进程。'
    }
  }
  $code = $script:process.ExitCode
  $script:process.Dispose(); $script:process = $null
  Check ($code -eq 0) ('识别程序退出码异常：'+$code)
}
function ReadOnly([string]$path) {
  $acl = Get-Acl -LiteralPath $path
  $script:restoreAcls[$path] = $acl
  $copy = Get-Acl -LiteralPath $path
  $rule = New-Object Security.AccessControl.FileSystemAccessRule(
    $script:sid, [Security.AccessControl.FileSystemRights]::Write,
    [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
    [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Deny)
  $copy.AddAccessRule($rule); Set-Acl -LiteralPath $path -AclObject $copy
  $denied = $false
  try { [IO.File]::WriteAllText((Join-Path $path 'must-not-write.tmp'),'probe') } catch [UnauthorizedAccessException] { $denied = $true }
  Check $denied '测试目录的只读权限没有生效，未继续宣称权限测试通过。'
}
function Parse([string[]]$paths) {
  return Send 'schedules.parse' @{paths=$paths}
}
try {
  if (-not $OutputRoot) { $OutputRoot = Join-Path $env:PUBLIC ('KongguChecks\'+[guid]::NewGuid().ToString('N')) }
  $OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
  Check (-not (Test-Path -LiteralPath $OutputRoot)) '请选择新的输出目录，避免覆盖之前的自检结果。'
  Check ($OutputRoot -cmatch '^[\x00-\x7F]+$') '自检基础目录需要英文路径；程序会在其下另外创建中文测试目录。'
  New-Item -ItemType Directory -Path $OutputRoot | Out-Null
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent(); $script:sid = $identity.User
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  $report.elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  Check (-not $report.elevated) '请以普通方式运行自检，不要选择“以管理员身份运行”。安装器的管理员确认与日常运行权限不同。'
  $os = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
  $report.os_build = [string]$os.CurrentBuild; $report.os_release = [string]$os.DisplayVersion
  $nativeArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
  $report.processor_architecture = [string]$nativeArch
  Check ([Environment]::Is64BitProcess -and $nativeArch -eq 'AMD64') '当前候选仅支持 x64 Windows；32 位或 ARM64 不在本轮已验证范围。'
  Check ([int]$os.CurrentBuild -ge 10240) '需要 Windows 10 或 Windows 11。'
  if (-not $WorkerPath) { $WorkerPath = Join-Path $env:ProgramFiles 'Konggu\konggu-worker.exe' }
  Check (Test-Path -LiteralPath $WorkerPath -PathType Leaf) '没有找到空谷 0.9.0，请先安装同目录中的安装包后再运行自检。'
  $report.worker_sha256 = (Get-FileHash -LiteralPath $WorkerPath -Algorithm SHA256).Hash.ToLowerInvariant()
  Check ($report.worker_sha256 -eq $expectedHash) '识别程序不是本工具对应的 0.9.0 候选，请先安装配套版本。'
  $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($OutputRoot))
  Check ($drive.AvailableFreeSpace -ge 2GB) '自检目录所在磁盘需至少 2 GiB 可用空间，请释放空间后重试。'
  Passed '普通权限、Windows x64、可用空间和候选版本'
  $script:install = Join-Path $OutputRoot '安装目录 Program Files'
  $testProfile = Join-Path $OutputRoot '用户 测试甲'
  $temp = Join-Path $testProfile '临时目录 Temp'
  $local = Join-Path $testProfile 'AppData\Local'
  $program = Join-Path $OutputRoot 'ProgramData'
  $public = Join-Path $OutputRoot 'Public'
  foreach ($dir in @($install,$temp,$local,$program,$public)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $script:copiedWorker = Join-Path $install 'konggu-worker.exe'
  Copy-Item -LiteralPath $WorkerPath -Destination $copiedWorker
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'samples') -Destination (Join-Path $OutputRoot '合成材料') -Recurse
  ReadOnly $install; ReadOnly $program
  $system = $env:SystemRoot
  $script:environment = @{
    SystemRoot=$system;WINDIR=$system;COMSPEC=(Join-Path $system 'System32\cmd.exe');PATH=(Join-Path $system 'System32');
    TEMP=$temp;TMP=$temp;USERPROFILE=$testProfile;APPDATA=(Join-Path $testProfile 'AppData\Roaming');LOCALAPPDATA=$local;
    PROGRAMDATA=$program;PUBLIC=$public;PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True';
    PADDLE_PDX_DISABLE_MODEL_DOWNLOAD='1';HF_HUB_OFFLINE='1';
    HTTP_PROXY='http://127.0.0.1:9';HTTPS_PROXY='http://127.0.0.1:9';ALL_PROXY='http://127.0.0.1:9';NO_PROXY=''
  }
  $report.environment = @{system_only_path=$true;development_variables_removed=$true;chinese_temp_and_profile=$true;
    readonly_install_and_programdata=$true;default_appdata_and_model_selection=$true;unreachable_http_proxy=$true}
  Write-Host '开始从安装包恢复资源并运行真实 OCR，请等待约 1～3 分钟…'
  Start-Worker
  $repaired = Send 'resources.repair'
  Check $repaired.ready '本地资源或识别运行库未就绪。'
  Check ((Test-Path (Join-Path $public 'Konggu\ocr_models')) -and (Test-Path (Join-Path $local 'Konggu\resources'))) '默认用户目录或无权限后的模型目录回退不符合预期。'
  Passed '中文临时目录、只读安装目录及模型目录权限回退'
  $null = Send 'settings.calendar.save' @{settings=@{semester_start_date='2026-03-02';teaching_weeks=18}}
  $inputs = Join-Path $OutputRoot '合成材料'
  $cn = Join-Path $inputs '办公室-合成甲-干事-中方课表.pdf'
  $en = Join-Path $inputs '办公室-合成甲-干事-英方课表.pdf'
  $parsed = Parse @($cn,$en)
  Check ($parsed.quality_state -eq 'accepted' -and $parsed.can_export) '合成文本课表未通过预览与导出检查。'
  $job = $parsed.result_ref
  $review = Send 'review.get' @{job_id=$job}
  $block = $review.blocks[0]; $count = @($review.blocks).Count
  $deleted = Send 'review.mutate' @{job_id=$job;action='delete';source_hash=$block.source_hash;block_id=$block.block_id;
    revision=$review.revision;operation_id=[guid]::NewGuid().ToString();reason='合成材料跨电脑自检删除';operator_id='兼容性自检'}
  Check (@($deleted.blocks).Count -eq ($count-1)) '删除课程未反映在预览中。'
  $restored = Send 'review.mutate' @{job_id=$job;action='restore';source_hash=$block.source_hash;block_id=$block.block_id;
    revision=$deleted.revision;operation_id=[guid]::NewGuid().ToString();reason='合成材料跨电脑自检恢复';operator_id='兼容性自检'}
  Check (@($restored.blocks).Count -eq $count) '恢复课程后数量不一致。'
  $excel = Join-Path $OutputRoot '合成空课.xlsx'
  $null = Send 'exports.excel' @{result_ref=$job;target_path=$excel}
  $zip = [IO.Compression.ZipFile]::OpenRead($excel)
  try { Check ($null -ne $zip.GetEntry('xl/workbook.xml')) '导出文件不是完整的 Excel 工作簿。' } finally { $zip.Dispose() }
  Passed '文本导入、预览、删除恢复、重算与 Excel 导出'
  $prepared = Send 'ai.prepare' @{job_id=$job;source_hash=$block.source_hash;page=0}
  Check ($prepared.configured -and $prepared.image.StartsWith('data:image/png;base64,')) 'AI 凭据或页面渲染未就绪。'
  Passed 'AI 凭据可加载、页面预览可生成（未发送 API 请求）'
  $image = Parse @($cn,(Join-Path $inputs 'image\办公室-合成甲-干事-英方课表.pdf'))
  Check ($image.result_ref -and -not $image.can_export -and $image.quality_state -in @('blocked','needs_review')) '图片样本未产生预期的待复核或阻断状态。'
  $texts = New-Object Text.StringBuilder
  Get-ChildItem -LiteralPath (Join-Path $local 'Konggu\cache') -Filter '*.json' -Recurse | ForEach-Object {
    $cache = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in $cache.items) { [void]$texts.Append($item.text) }
  }
  $recognized = $texts.ToString().Replace(' ','').ToUpperInvariant()
  Check ($recognized.Contains('2468') -and $recognized.Contains('PACK')) '实际图片 OCR 未读出合成标记。'
  $mustNotExist = Join-Path $OutputRoot '不应生成.xlsx'
  $denied = Send 'exports.excel' @{result_ref=$image.result_ref;target_path=$mustNotExist} -AllowFailure
  Check (-not $denied.ok -and -not (Test-Path $mustNotExist) -and $denied.error -match '阻断|未确认') '未复核结果没有正确阻止正式导出。'
  Passed '实际图片 OCR、无有效 HTTP 代理条件下的本地处理、质量门禁'
  Stop-Worker; Start-Worker
  $reloaded = Send 'review.get' @{job_id=$job}
  Check (@($reloaded.blocks).Count -eq $count -and @($reloaded.corrections).Count -ge 2) '重启后任务或修正记录丢失。'
  Stop-Worker
  Check (((Get-FileHash -LiteralPath $copiedWorker).Hash.ToLowerInvariant()) -eq $expectedHash) '识别程序文件发生非预期改变。'
  Passed '重启持久化、正常退出与安装文件保持'
  $report.ok = $true
} catch {
  $reason = $_.Exception.Message
  foreach ($privatePath in @($OutputRoot,$env:USERPROFILE,$WorkerPath)) {
    if ($privatePath) { $reason = $reason.Replace($privatePath,'<本机目录>') }
  }
  $report.error = $reason
  Write-Host ('未通过：'+$reason) -ForegroundColor Red
} finally {
  if ($null -ne $process) {
    try { Stop-Worker } catch { $report.ok=$false; $report.exit_error='本工具启动的识别进程未正常退出。' }
  }
  foreach ($path in $restoreAcls.Keys) { Set-Acl -LiteralPath $path -AclObject $restoreAcls[$path] }
  $report.elapsed_seconds = [Math]::Round(((Get-Date)-$started).TotalSeconds,2)
  if ($OutputRoot -and (Test-Path -LiteralPath $OutputRoot)) {
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $OutputRoot '自检结果.json') -Encoding UTF8
    $lines = @('空谷 0.9.0 Windows 自检结果','全部自动检查通过：'+$report.ok,'Windows build：'+$report.os_build,
      '识别程序 SHA-256：'+$report.worker_sha256,'用时（秒）：'+$report.elapsed_seconds,
      '说明：本工具只使用合成材料，不上传课表、不发送 API 请求，不等同于物理断网或原生窗口操作验收。')
    foreach ($step in $steps) { $lines += '通过：'+$step.step }
    if ($report.error) { $lines += '未通过：'+$report.error }
    $lines | Set-Content -LiteralPath (Join-Path $OutputRoot '自检结果.txt') -Encoding UTF8
    Write-Host ('完整结果保存在：'+$OutputRoot)
    $tag = Get-Date -Format 'yyyyMMdd-HHmmss'
    try {
      foreach ($ext in @('json','txt')) { Copy-Item -LiteralPath (Join-Path $OutputRoot ('自检结果.'+$ext)) -Destination (Join-Path $PSScriptRoot ('自检结果-'+$tag+'.'+$ext)) -ErrorAction Stop }
      Write-Host '自检结果已同时复制到工具所在目录。请只发回这两个报告文件。'
    } catch { Write-Host '工具目录不可写，请从上面显示的完整路径取回报告。' }
  }
}
if (-not $report.ok) { exit 1 }
