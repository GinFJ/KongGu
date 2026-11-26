"""Check the week presentation against supplied records, especially unknown cells."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_week_grid_preserves_source_counts_coverage_and_slot_identity():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to run the frontend presentation checks.")
    script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const code = ts.transpileModule(fs.readFileSync('src/availabilityView.ts', 'utf8'), {compilerOptions: {target: ts.ScriptTarget.ES2021, module: ts.ModuleKind.ESNext}}).outputText;
const {availabilityWeeks, availabilityWeekGrid, availabilitySlotDetail} = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const row=(week,day,period,time,count,names='成员甲')=>({'周次':week,'星期':day,'日期':'2026-11-09','节次':period,'时间':time,'空闲人数':count,'空闲人员':names,'占用人数':2-count,'有课人员':'成员乙'});
const rows=[row(10,'周一',1,'08:10—08:55',2,'另一周'),row(11,'周一',5,'14:30—15:15',1,'<script>'),row(11,'周二',5,'14:30—15:15',0,''),row(11,'周一',12,'12:40—13:25',2,'成员甲、成员乙'),row(11,'周一',4,'11:05—11:50',1)];
assert.deepEqual(availabilityWeeks([...rows,{'周次':'bad'},{'周次':-1}]),[10,11]);
const html=availabilityWeekGrid(rows,11,2);
assert.ok(html.indexOf('第 4 节')<html.indexOf('第 12 节'));
assert.ok(html.indexOf('第 12 节')<html.indexOf('第 5 节'));
assert.match(html,/无人空闲/); assert.match(html,/class="uncovered">未覆盖/);
assert.match(html,/2 人空闲/); assert.match(html,/data-slot-index="1"/);
assert.doesNotMatch(html,/<script>|另一周/); assert.match(html,/&lt;script&gt;/);
assert.doesNotMatch(html,/data-slot-index="0"/);
assert.match(availabilityWeekGrid([rows[1],rows[1]],11,2),/记录不唯一/);
assert.doesNotMatch(availabilityWeekGrid([rows[1],rows[1]],11,2),/data-slot-index/);
assert.match(availabilityWeekGrid([row(11,'周一',1,'08:10','')],11,2),/人数待核对/);
assert.match(availabilityWeekGrid([row(11,'周一',1,'08:10',3)],11,2),/人数待核对/);
assert.match(availabilityWeekGrid([],11,2),/没有可展示/);
const detail=availabilitySlotDetail(rows[1]);assert.match(detail,/&lt;script&gt;/);assert.doesNotMatch(detail,/<script>/);
'''
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
