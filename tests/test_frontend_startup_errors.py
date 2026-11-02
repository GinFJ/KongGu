"""Execute the real frontend error formatter without a desktop or OCR worker."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLATION_MESSAGE = "安装组件未能启动，请使用完整安装包重新安装；仍无法启动时请联系维护者。"


@pytest.fixture(scope='module')
def results():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required to execute frontend contracts.')
    script = r'''
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source = fs.readFileSync('src/main.ts', 'utf8');
const ast = ts.createSourceFile('main.ts', source, ts.ScriptTarget.Latest, true);
const names = new Set(['friendlyMessage', 'formatError', 'isInstallationStartupError', 'redactStartupDiagnostic']);
const pieces = ast.statements.filter(n => ts.isFunctionDeclaration(n) && names.has(n.name?.text)
  || ts.isVariableStatement(n) && n.declarationList.declarations.some(d => d.name.getText(ast) === 'INSTALLATION_STARTUP_MESSAGE'));
const code = ts.transpileModule(pieces.map(n => n.getText(ast)).join('\n'), {compilerOptions:{target:ts.ScriptTarget.ES2020}}).outputText;
const cases = [
 ['registered', 'sidecar not configured under tauri.conf.json > bundle > externalBin: binaries/konggu-worker', true],
 ['missing_named', 'Command.sidecar failed to spawn binaries/konggu-worker.exe: No such file or directory (os error 2)', true],
 ['private_path', 'failed to spawn sidecar "C:\\Users\\ExamplePerson\\AppData\\Local\\Konggu\\konggu-worker.exe": No such file or directory (os error 2)', true],
 ['missing_boot', 'No such file or directory (os error 2)', true],
 ['missing_pdf', "No such file or directory (os error 2): 'D:\\Documents\\anonymous.pdf'", false],
 ['ocr_model', '本地模型文件缺失，请修复识别资源。', true],
 ['browser', "Cannot read properties of undefined (reading 'invoke')", true],
];
const results = {};
for (const [name, message, startup] of cases) {
 const context = vm.createContext({state:{startupDiagnostic:''}, console});
 vm.runInContext(code, context);
 context.input = message; context.startup = startup;
 results[name] = {message:vm.runInContext('formatError(new Error(input), startup)', context), diagnostic:context.state.startupDiagnostic};
}
process.stdout.write(JSON.stringify(results));
'''
    completed = subprocess.run([node, '--input-type=module', '-e', script], cwd=ROOT,
                               capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize('case', ['registered', 'missing_named', 'private_path', 'missing_boot'])
def test_installation_startup_failure_is_actionable_chinese_instead_of_raw_native_error(results, case):
    assert results[case]['message'] == INSTALLATION_MESSAGE
    assert results[case]['diagnostic']


def test_registration_reason_remains_available_only_as_a_technical_diagnostic(results):
    detail = results['registered']['diagnostic']
    assert 'not configured' in detail and 'externalBin' in detail
    assert 'externalBin' not in results['registered']['message']
    assert 'ExamplePerson' not in results['private_path']['diagnostic']
    assert 'C:\\Users' not in results['private_path']['diagnostic']
    assert 'os error 2' in results['private_path']['diagnostic']


@pytest.mark.parametrize('case', ['missing_pdf', 'ocr_model', 'browser'])
def test_ordinary_file_model_and_browser_errors_are_not_misclassified_as_broken_installation(results, case):
    assert results[case]['message'] != INSTALLATION_MESSAGE
    assert results[case]['diagnostic'] == ''


def test_browser_unavailable_guidance_remains_distinct(results):
    assert '桌面运行环境未连接' in results['browser']['message']
