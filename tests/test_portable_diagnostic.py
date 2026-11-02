"""The distributed helper must leave an actionable report even with no worker."""
import shutil
import subprocess
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time

import pytest


def _powershell_environment(**extra):
    environment = dict(os.environ, **extra)
    # PowerShell 7's inherited module directories can shadow Windows
    # PowerShell 5.1's built-in Utility module in a Python-hosted invocation.
    for name in list(environment):
        if name.casefold() == "psmodulepath":
            environment.pop(name)
    return environment


@pytest.fixture(scope="module")
def diagnostic_worker(tmp_path_factory):
    if shutil.which("powershell.exe") is None:
        pytest.skip("Windows diagnostic helper")
    root = tmp_path_factory.mktemp("diagnostic-worker")
    source = root / "FakeWorker.cs"
    source.write_text(r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
public class FakeWorker {
  public static int Main(string[] args) {
    Console.OutputEncoding = new UTF8Encoding(false);
    if (Array.IndexOf(args, "--hold") >= 0) { Thread.Sleep(60000); return 0; }
    string exe = Process.GetCurrentProcess().MainModule.FileName;
    string mode = Path.GetFileNameWithoutExtension(exe);
    if (mode == "ready") { Console.WriteLine(@"{""ok"":true,""ready"":true,""missing"":[],""invalid"":[],""ocr"":{""runtime"":{""stage"":""ready""}}}"); return 0; }
    if (mode == "crash") { Console.Error.WriteLine(@"LOAD_LIBRARY_REASON C:\Users\example\private.dll"); return 1; }
    if (mode == "errors") {
      Console.WriteLine("FULL_STDOUT_MUST_NOT_BE_REPORTED");
      Console.WriteLine(@"{""ok"":false,""error"":""TOP_REASON C:\\Users\\example\\top.txt"",""status"":{""ready"":false,""missing"":[],""invalid"":[],""manifest_error"":""MANIFEST_REASON LONG_PLACEHOLDER"",""ocr"":{""error"":""MODEL_REASON PROFILE_PLACEHOLDER\\folder\\SENSITIVE_USER_FILENAME"",""runtime"":{""stage"":""init"",""error"":""DLL_REASON C:\\Users\\example\\private.dll""}}}}".Replace("PROFILE_PLACEHOLDER",Environment.GetFolderPath(Environment.SpecialFolder.UserProfile).Replace("\\","\\\\")).Replace("LONG_PLACEHOLDER",new string('X',2000)));
      return 1;
    }
    Process child = Process.Start(new ProcessStartInfo(exe, "--hold") {
      UseShellExecute = false, CreateNoWindow = true
    });
    File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("KONGGU_DIAG_TEST_STATE"), "child.pid"), child.Id.ToString());
    if (mode == "treewait") Thread.Sleep(60000);
    return 0;
  }
}
''', encoding="utf-8")
    command = ("Add-Type -TypeDefinition ([IO.File]::ReadAllText($args[0])) "
               "-OutputAssembly $args[1] -OutputType ConsoleApplication")
    # -File avoids command-string interpolation of paths and keeps the helper
    # synthetic; no app, model, compiler installation or network is needed.
    compile_script = root / "compile.ps1"
    compile_script.write_text(command, encoding="utf-8")
    executable = root / "worker.exe"
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(compile_script),
                    str(source), str(executable)], check=True, capture_output=True, timeout=20,
                   env=_powershell_environment())
    for mode in ["errors", "ready", "crash", "treewait", "pipewait"]:
        shutil.copy2(executable, root / f"{mode}.exe")
    return root


def _run_diagnostic(worker, output, **kwargs):
    script = Path(__file__).resolve().parents[1] / "tools/diagnose_konggu_ocr.ps1"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-WorkerPath", str(worker), "-OutputDirectory", str(output), "-NoPause", *kwargs.pop("extra", [])],
        capture_output=True, env=_powershell_environment(), **kwargs,
    )


def test_worker_errors_are_reported_without_paths_or_full_stdout(tmp_path, diagnostic_worker):
    output = tmp_path / "reports"
    proc = _run_diagnostic(diagnostic_worker / "errors.exe", output, timeout=15)
    text = next(output.glob("*.txt")).read_text(encoding="utf-8-sig")
    for reason in ["TOP_REASON", "MANIFEST_REASON", "MODEL_REASON", "DLL_REASON"]:
        assert reason in text
    assert "example" not in text and "private.dll" not in text
    assert "SENSITIVE_USER_FILENAME" not in text
    assert all(len(line) <= 505 for line in text.splitlines())
    assert "FULL_STDOUT_MUST_NOT_BE_REPORTED" not in text
    assert proc.returncode == 2


@pytest.mark.parametrize("mode,exit_code,marker", [("ready", 0, "PASSED"), ("crash", 2, "LOAD_LIBRARY_REASON")])
def test_real_powershell_launch_preserves_success_and_reports_startup_failure(tmp_path, diagnostic_worker, mode, exit_code, marker):
    output = tmp_path / "reports"
    proc = _run_diagnostic(diagnostic_worker / f"{mode}.exe", output, timeout=15)
    text = next(output.glob("*.txt")).read_text(encoding="utf-8-sig")
    assert proc.returncode == exit_code and marker in text
    assert "example" not in text and "private.dll" not in text


@pytest.mark.parametrize("mode", ["treewait", "pipewait"])
def test_total_deadline_covers_child_tree_and_inherited_output_pipes(tmp_path, diagnostic_worker, mode):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    environment = _powershell_environment(KONGGU_DIAG_TEST_STATE=str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "tools/diagnose_konggu_ocr.ps1"
    sibling = subprocess.Popen([str(diagnostic_worker / f"{mode}.exe"), "--hold"])
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
               "-WorkerPath", str(diagnostic_worker / f"{mode}.exe"), "-OutputDirectory", str(tmp_path / "reports"),
               "-TimeoutSeconds", "10", "-NoPause"]
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child_handle = None
    started = time.monotonic()
    try:
        deadline = started + 8
        pid_file = tmp_path / "child.pid"
        while not pid_file.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.02)
        assert pid_file.exists(), "Synthetic worker must actually start its child"
        child_handle = kernel.OpenProcess(0x100000 | 0x1000 | 1, False, int(pid_file.read_text()))
        assert child_handle
        process.communicate(timeout=max(1, 16 - (time.monotonic() - started)))
        assert process.returncode == 2
        assert time.monotonic() - started < 16
        assert kernel.WaitForSingleObject(child_handle, 1500) == 0, "Only killing the onefile parent leaves its OCR child alive"
        assert sibling.poll() is None, "Another same-named worker is outside this diagnostic's process tree"
        text = next((tmp_path / "reports").glob("*.txt")).read_text(encoding="utf-8-sig")
        assert "FAILED" in text and "10" in text
    finally:
        if process.poll() is None:
            process.kill()
        if child_handle:
            if kernel.WaitForSingleObject(child_handle, 0) == 258:
                kernel.TerminateProcess(child_handle, 3)
            kernel.CloseHandle(child_handle)
        sibling.terminate()
        sibling.wait(timeout=5)
        process.communicate(timeout=5)


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows diagnostic helper")
def test_missing_worker_writes_start_and_failure_report_without_running_app(tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools/diagnose_konggu_ocr.ps1"
    output = tmp_path / "诊断记录"
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-WorkerPath", str(tmp_path / "missing-worker.exe"), "-OutputDirectory", str(output), "-NoPause"],
        capture_output=True, timeout=20,
    )
    reports = list(output.glob("*.txt"))
    assert reports, "A launch failure must still leave a report; an open app is not required"
    text = reports[0].read_text(encoding="utf-8-sig")
    assert "STARTED" in text and "FAILED" in text
    assert "安装" in text
    assert proc.returncode == 2
