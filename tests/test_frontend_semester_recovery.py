"""Exercise the real issue action, settings save and late polling replies."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_semester_action_and_completed_job_race():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the desktop frontend.")
    script = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const ast = ts.createSourceFile('main.ts', fs.readFileSync('src/main.ts', 'utf8'), ts.ScriptTarget.Latest, true);
const names = new Set(['bindActions', 'issueAction', 'waitForJob', 'saveSettings', 'getElement']);
const source = ast.statements.filter(n => ts.isFunctionDeclaration(n) && names.has(n.name?.text))
  .map(n => n.getText(ast)).join('\n');
const input = name => ({focus() { focus = name; }, scrollIntoView() { scroll = name; }});
const fields = {'#semesterStart': input('date'), '#teachingWeeks': input('weeks'), '#semesterSettings': {open:false}};
let click, scroll, focus;
const globals = {state: {}, completedJobId: '', outsideSemester: () => false, savedCalendarSettings: {semester_start_date:'2026-03-02', teaching_weeks:18},
  escapeHtml: value => value, window:{addEventListener() {}, confirm: () => true}, appRoot: {addEventListener(type, fn) {if (type === 'click') click = fn;}},
  document:{querySelector: key => fields[key] ? Object.assign(fields[key], {addEventListener() {}}) : null},
  chooseFiles() {}, parseSchedules() {}, cancelJob() {}, retryFailed() {}, exportExcel() {}, repairResources() {},
  clearLocalData() {}, persistDraft() {}, render() {}, setStatus() {}, delay() {}, client: {}};
const ctx = vm.createContext(globals);
vm.runInContext(ts.transpileModule(source, {compilerOptions:{target: ts.ScriptTarget.ES2020}}).outputText, ctx);
vm.runInContext('bindActions()', ctx);
for (const [code, field] of [['PRE_TERM_DATE', 'date'], ['WEEK_OUT_OF_SEMESTER', 'weeks']]) {
  ctx.issue = {code, severity:'error', confirmed:false};
  const html = vm.runInContext('issueAction(issue)', ctx);
  const match = html.match(/data-semester-setting="(semesterStart|teachingWeeks)"/);
  assert.ok(match, html);
  assert.match(html, /前往学期设置/);
  fields['#semesterSettings'].open = false;
  click({target:{closest: selector => selector === '[data-semester-setting]' ? {dataset:{semesterSetting:match[1]}} : null}});
  assert.equal(fields['#semesterSettings'].open, true);
  assert.equal(scroll, field); assert.equal(focus, field);
}
ctx.issue = {code:'MISSING_INPUT', severity:'error', confirmed:false};
assert.doesNotMatch(vm.runInContext('issueAction(issue)', ctx), /data-semester-setting|data-confirm-inline/);
ctx.issue = {code:'TIMETABLE_COVERAGE_LIMIT', severity:'info', confirmed:false, blocks_export:false};
assert.equal(vm.runInContext('issueAction(issue)', ctx), '');
Object.assign(ctx.state, {result:{can_export:false}, files:['anonymous.pdf'], calendarSettings:{semester_start_date:'2026-09-07', teaching_weeks:20}});
ctx.client.request = async (name, {settings}) => {assert.equal(name, 'settings.calendar.save'); return {settings};};
await vm.runInContext('saveSettings()', ctx);
assert.equal(ctx.state.result, null); assert.equal(ctx.state.stage, 'ready'); assert.equal(ctx.state.files.length, 1);
for (const mode of ['complete', 'different-job']) {
  for (const failure of [true, false]) {
    Object.assign(ctx.state, {jobId:'old', statusText:'old'});
    ctx.completedJobId = '';
    ctx.client.request = async () => {
      if (mode === 'complete') ctx.completedJobId = 'old';
      else ctx.state.jobId = 'new';
      ctx.state.statusText = 'fresh';
      if (failure) throw new Error('处理等待超时，请重试');
      return {job:{status:'failed', current:10, total:10, files:[], error:'outdated'}};
    };
    await vm.runInContext("waitForJob('old')", ctx);
    assert.equal(ctx.state.statusText, 'fresh', mode + '/' + failure);
  }
}
"""
    result = subprocess.run([node, "--input-type=module", "-e", script],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
