"""Exercise the actual TypeScript client and startup actions with a simulated native process."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_worker_startup_diagnostics_and_retry():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the desktop frontend.")
    script = r"""
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const compile = source => ts.transpileModule(source, {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}
}).outputText;
let command, spawnCount = 0, writes = [], closeImmediately = false;
const Command = {sidecar(_name, _args, options) {
  assert.deepEqual({...options.env}, {TEMP: 'test-temp', TMP: 'test-temp'});
  command = new EventEmitter();
  command.stdout = new EventEmitter();
  command.stderr = new EventEmitter();
  command.spawn = async () => {
    spawnCount++;
    if (closeImmediately) {
      command.stderr.emit('data', '[PYI-123:ERROR] Extraction path length exceeds maximum path length!');
      command.stderr.emit('data', '[PYI-123:ERROR] Failed to extract entry: private/file.dll.');
      command.emit('close', {code: 1, signal: null});
    }
    return {write(line) { writes.push(JSON.parse(line)); }, kill: async () => {}};
  };
  return command;
}};
const runtime = {exports: {}, require(name) {
  if (name === '@tauri-apps/api/core') return {invoke: async name => {
    assert.equal(name, 'prepare_worker_temp'); return 'test-temp';
  }};
  return {Command};
}, window: {setTimeout, clearTimeout}, crypto, console};
vm.runInNewContext(compile(fs.readFileSync('src/sidecarClient.ts', 'utf8')), runtime);
const client = new runtime.exports.SidecarClient();
const tick = () => new Promise(resolve => setImmediate(resolve));
const requestError = () => client.request('resources.repair').then(
  () => assert.fail('expected request failure'), error => error.message);
let done = requestError();
await tick();
command.stderr.emit('data', '[PYI-123:ERROR] Failed to extract private/file.dll: failed to open target file!\n');
command.emit('close', {code: 1, signal: null});
let message = await done;
assert.match(message, /解包|解压/);
assert.match(message, /退出码 1/);
assert.doesNotMatch(message, /课表已中断|private/);
assert.equal(spawnCount, 1);

done = requestError();
await tick();
command.stdout.emit('data', JSON.stringify({type: 'event', event: 'sidecar.fatal',
  message: '已有一个空谷识别进程在运行，请关闭其他空谷窗口后重试。'}) + '\n');
command.emit('close', {code: 1, signal: null});
assert.match(await done, /关闭其他空谷窗口/);

closeImmediately = true;
message = await requestError();
assert.match(message, /临时目录路径过长/);
assert.doesNotMatch(message, /private/);
closeImmediately = false;
const request = client.request('resources.repair');
await tick();
command.stdout.emit('data', JSON.stringify({id: writes.at(-1).id, ok: true, data: 'ok'}) + '\n');
assert.equal(await request, 'ok');
done = client.request('job.get').then(() => assert.fail(), error => error.message);
await tick();
command.emit('close', {code: 7, signal: null});
message = await done;
assert.match(message, /退出码 7/);
assert.doesNotMatch(message, /解包|关闭其他空谷窗口/);

const main = fs.readFileSync('src/main.ts', 'utf8');
const ast = ts.createSourceFile('main.ts', main, ts.ScriptTarget.Latest, true);
const names = new Set(['boot', 'repairResources']);
const calls = [], state = {stage: 'failed', resourceStatus: null, busy: false, startupDiagnostic: ''};
const context = vm.createContext({state, client: {request: async (name) => {
  calls.push(name);
  return name === 'resources.repair' ? {status: {ready:true}} : {settings: {teaching_weeks: 17}};
}}, render() {}, setStatus() {}, delay() {}, savedCalendarSettings: {}});
vm.runInContext(compile(ast.statements.filter(n => ts.isFunctionDeclaration(n) && names.has(n.name?.text))
  .map(n => n.getText(ast)).join('\n')), context);
await vm.runInContext('repairResources()', context);
assert.deepEqual(calls.sort(), ['resources.repair', 'settings.calendar.get']);
assert.equal(state.stage, 'ready');
assert.equal(state.calendarSettings.teaching_weeks, 17);
assert.equal(state.busy, false);
"""
    run = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT,
                         capture_output=True, encoding="utf-8", timeout=20)
    assert run.returncode == 0, run.stderr
