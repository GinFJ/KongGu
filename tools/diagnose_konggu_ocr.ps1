[CmdletBinding()]
param([string]$WorkerPath, [string]$OutputDirectory, [ValidateRange(10,600)][int]$TimeoutSeconds=180, [switch]$NoPause)
$ErrorActionPreference='Stop'
$exitCode=2
$reportPath=$null
$process=$null
function Protect-ReportText([string]$Message) {
    $Message=[regex]::Replace($Message,'(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n"''<>]+','[本地文件]')
    $Message=[regex]::Replace($Message,'(?<![\w])/(?:[^\s/]+/)+[^\s"''<>]*','[本地文件]')
    $Message=$Message -replace '[\r\n]+',' '
    if ($Message.Length -gt 500) { $Message=$Message.Substring(0,500)+'…' }
    return $Message
}
function Write-Report([string]$Message) {
    $Message=Protect-ReportText $Message
    [IO.File]::AppendAllText($reportPath,$Message+[Environment]::NewLine,[Text.Encoding]::UTF8)
    Write-Host $Message
}
function Write-ErrorField([string]$Label,$Value) {
    if (-not [string]::IsNullOrWhiteSpace([string]$Value)) { Write-Report ($Label+'：'+[string]$Value) }
}
function Initialize-DiagnosticProcess {
    # Start suspended, assign to a private Job Object, then resume. Descendants
    # inherit membership before any code runs; cleanup never scans by image
    # name or relies on a PID that might have exited and been reused.
    if ('KongguDiagnosticProcess' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using Microsoft.Win32.SafeHandles;
public sealed class KongguDiagnosticProcess : IDisposable {
  [StructLayout(LayoutKind.Sequential)] struct Security {
    public int Length; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
  }
  [StructLayout(LayoutKind.Sequential)] struct Startup {
    public int Size; public string Reserved, Desktop, Title;
    public int X,Y,XSize,YSize,XChars,YChars,Fill,Flags; public short Show,ReservedSize;
    public IntPtr Reserved2,Input,Output,Error;
  }
  [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr Process,Thread; public uint Pid,Tid; }
  [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
    public long ProcessTime,JobTime; public uint Flags; public UIntPtr MinWork,MaxWork;
    public uint Active; public UIntPtr Affinity; public uint Priority,Scheduling;
  }
  [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong A,B,C,D,E,F; }
  [StructLayout(LayoutKind.Sequential)] struct Limits {
    public BasicLimits Basic; public IoCounters Io; public UIntPtr ProcessMemory,JobMemory,PeakProcess,PeakJob;
  }
  [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr a,string name);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int type,ref Limits value,uint size);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool CreatePipe(out IntPtr read,out IntPtr write,ref Security security,int size);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetHandleInformation(IntPtr handle,uint mask,uint flags);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcess(string app,StringBuilder command,IntPtr a,IntPtr b,bool inherit,uint flags,IntPtr environment,string cwd,ref Startup startup,out ProcessInfo info);
  [DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
  [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
  [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  IntPtr job,process; StreamReader output,error;
  public Task<string> OutputTask { get; private set; }
  public Task<string> ErrorTask { get; private set; }
  static void Check(bool success) { if (!success) throw new Win32Exception(Marshal.GetLastWin32Error()); }
  static void Close(ref IntPtr handle) { if(handle!=IntPtr.Zero) { CloseHandle(handle);handle=IntPtr.Zero; } }
  public static KongguDiagnosticProcess Start(string executable,string arguments,string cwd) {
    var self=new KongguDiagnosticProcess();
    IntPtr outRead=IntPtr.Zero,outWrite=IntPtr.Zero,errRead=IntPtr.Zero,errWrite=IntPtr.Zero,inRead=IntPtr.Zero,inWrite=IntPtr.Zero;
    ProcessInfo info=new ProcessInfo();
    try {
      self.job=CreateJobObject(IntPtr.Zero,null);Check(self.job!=IntPtr.Zero);
      var limits=new Limits();limits.Basic.Flags=0x2000; // KILL_ON_JOB_CLOSE
      Check(SetInformationJobObject(self.job,9,ref limits,(uint)Marshal.SizeOf(typeof(Limits))));
      var security=new Security { Length=Marshal.SizeOf(typeof(Security)),Inherit=true };
      Check(CreatePipe(out outRead,out outWrite,ref security,0));Check(SetHandleInformation(outRead,1,0));
      Check(CreatePipe(out errRead,out errWrite,ref security,0));Check(SetHandleInformation(errRead,1,0));
      Check(CreatePipe(out inRead,out inWrite,ref security,0));Check(SetHandleInformation(inWrite,1,0));
      var startup=new Startup { Size=Marshal.SizeOf(typeof(Startup)),Flags=0x100,Input=inRead,Output=outWrite,Error=errWrite };
      Check(CreateProcess(null,new StringBuilder("\""+executable+"\" "+arguments),IntPtr.Zero,IntPtr.Zero,true,0x08000004,IntPtr.Zero,cwd,ref startup,out info));
      self.process=info.Process;
      Check(AssignProcessToJobObject(self.job,self.process));
      self.output=new StreamReader(new FileStream(new SafeFileHandle(outRead,true),FileAccess.Read),Encoding.UTF8);outRead=IntPtr.Zero;
      self.error=new StreamReader(new FileStream(new SafeFileHandle(errRead,true),FileAccess.Read),Encoding.UTF8);errRead=IntPtr.Zero;
      self.OutputTask=self.output.ReadToEndAsync();self.ErrorTask=self.error.ReadToEndAsync();
      Check(ResumeThread(info.Thread)!=0xffffffff);
      return self;
    } catch {
      if(self.process!=IntPtr.Zero) TerminateProcess(self.process,2);
      self.Dispose();throw;
    } finally {
      Close(ref info.Thread);Close(ref outRead);Close(ref outWrite);Close(ref errRead);Close(ref errWrite);Close(ref inRead);Close(ref inWrite);
    }
  }
  public bool WaitForExit(int milliseconds) { return WaitForSingleObject(process,(uint)Math.Max(0,milliseconds))==0; }
  public bool WaitForOutput(int milliseconds) { return Task.WaitAll(new Task[] {OutputTask,ErrorTask},Math.Max(0,milliseconds)); }
  public uint ExitCode { get { uint code;Check(GetExitCodeProcess(process,out code));return code; } }
  public void Dispose() {
    if(job!=IntPtr.Zero) { TerminateJobObject(job,2);Close(ref job); }
    if(process!=IntPtr.Zero) { WaitForSingleObject(process,1500);Close(ref process); }
    if(OutputTask!=null && ErrorTask!=null) { try { WaitForOutput(1500); } catch {} }
    if(output!=null) { output.Dispose();output=null; }
    if(error!=null) { error.Dispose();error=null; }
  }
}
'@
}
try {
    if (-not $OutputDirectory) { $OutputDirectory=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Konggu\diagnostics' }
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    $reportPath=Join-Path $OutputDirectory ('Konggu-check-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff')+'.txt')
    [IO.File]::WriteAllText($reportPath,"STARTED`r`n",[Text.Encoding]::UTF8)
    Write-Report ('空谷离线识别检查 '+(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Write-Report ('系统版本：'+[Environment]::OSVersion.Version+'；64位系统：'+[Environment]::Is64BitOperatingSystem)
    if (-not $WorkerPath) {
        $folders=@($PSScriptRoot)
        foreach ($base in @($env:ProgramFiles,${env:ProgramFiles(x86)},$env:LOCALAPPDATA)) {
            if ($base) { $folders+=(Join-Path $base 'Konggu'); $folders+=(Join-Path $base 'Programs\Konggu') }
        }
        foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
            Get-ItemProperty $key -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '^(Konggu|空谷)' -and $_.InstallLocation } | ForEach-Object { $folders+=$_.InstallLocation }
        }
        foreach ($folder in ($folders | Select-Object -Unique)) {
            if (-not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
            $candidate=Get-ChildItem -LiteralPath $folder -Filter 'konggu-worker*.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($candidate) { $WorkerPath=$candidate.FullName; break }
        }
    }
    if (-not $WorkerPath -or -not (Test-Path -LiteralPath $WorkerPath -PathType Leaf)) {
        throw '没有找到已安装的识别程序。请先运行完整的空谷离线安装包；若已安装到自选目录，请把这两个诊断文件复制到安装目录后双击运行。无需先打开空谷，也无需安装 Python。'
    }
    Write-Report ('识别程序 SHA-256：'+(Get-FileHash -LiteralPath $WorkerPath -Algorithm SHA256).Hash)
    $requestPath=Join-Path $OutputDirectory 'resource-check-request.json'
    [IO.File]::WriteAllText($requestPath,'{"command":"resources.repair"}',[Text.Encoding]::ASCII)
    Write-Report '正在从安装包准备识别资源并检查 OCR；不会读取或上传课表。'
    Initialize-DiagnosticProcess
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $process=[KongguDiagnosticProcess]::Start($WorkerPath,('--request "'+$requestPath+'"'),(Split-Path -Parent $WorkerPath))
    $remaining=[int][Math]::Max(0,$TimeoutSeconds*1000-$clock.ElapsedMilliseconds)
    if (-not $process.WaitForExit($remaining)) {
        throw ('识别检查超过 '+$TimeoutSeconds+' 秒。请保留本报告，并确认安全软件是否拦截空谷识别程序。')
    }
    $remaining=[int][Math]::Max(0,$TimeoutSeconds*1000-$clock.ElapsedMilliseconds)
    if (-not $process.WaitForOutput($remaining)) {
        throw ('识别输出回收超过 '+$TimeoutSeconds+' 秒；已停止本次诊断进程树。请保留本报告。')
    }
    $code=$process.ExitCode
    $outText=$process.OutputTask.GetAwaiter().GetResult()
    $errText=$process.ErrorTask.GetAwaiter().GetResult()
    Write-Report ('进程退出码：'+$code)
    $jsonLine=@($outText -split '\r?\n' | Where-Object { $_.TrimStart().StartsWith('{') }) | Select-Object -Last 1
    if (-not $jsonLine) {
        Write-Report ('运行时未返回结果；标准错误字符数：'+$errText.Length)
        Write-ErrorField '启动错误摘要' (($errText -split '\r?\n' | Where-Object { $_.Trim() } | Select-Object -Last 1) -join '')
        throw '识别程序没有完成启动。请使用完整离线安装包重新安装，并把本报告交给维护者检查运行库或安全软件拦截。'
    }
    $result=$jsonLine | ConvertFrom-Json
    $status=$result.status
    if ($null -eq $status) { $status=$result }
    Write-Report ('资源就绪：'+[bool]$status.ready)
    Write-Report ('资源缺失数：'+@($status.missing).Count+'；校验失败数：'+@($status.invalid).Count)
    Write-ErrorField '请求错误' $result.error
    Write-ErrorField '资源清单错误' $status.manifest_error
    Write-ErrorField '模型错误' $status.ocr.error
    Write-ErrorField '识别运行错误' $status.ocr.runtime.error
    if ($status.ocr.runtime.stage) { Write-Report ('识别初始化阶段：'+$status.ocr.runtime.stage) }
    if ($status.ready -and $code -eq 0) {
        Write-Report 'PASSED：内置识别环境检查通过。接下来在空谷中导入一组中英课表，完成核对与导出。'
        $exitCode=0
    } else { Write-Report 'FAILED：识别环境尚未就绪。请使用完整离线包修复安装；本报告可交给维护者定位，暂不要用于正式排班。' }
} catch {
    $message='FAILED：'+$_.Exception.Message
    if ($reportPath) { Write-Report $message } else { Write-Host $message -ForegroundColor Red }
} finally {
    if ($process) { $process.Dispose() }
    if ($reportPath) { Write-Host ('报告位置：'+$reportPath) -ForegroundColor Yellow }
    if (-not $NoPause) { [void](Read-Host '按 Enter 关闭窗口') }
}
exit $exitCode
