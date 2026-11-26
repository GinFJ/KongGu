"""Execute the real calendar projection for manual multi-period courses."""
import json
from pathlib import Path
import subprocess


def test_manual_periods_follow_clock_order_and_do_not_fill_unoccupied_gaps():
    root = Path(__file__).resolve().parents[1]
    script = r'''
const fs=require('node:fs'),ts=require('typescript');
const text=fs.readFileSync('src/calendarCourseEvents.ts','utf8');
const js=ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2021}}).outputText;
const m={exports:{}};new Function('exports','require','module',js)(m.exports,require,m);
const cases=[[5,12,13],[1,3],[3,4],[12,13],[13,5],[1,1,3],[]];
console.log(JSON.stringify(cases.map(periods=>m.exports.courseEvents([
 {block_id:'anonymous',name:'成员',course:'核对课程',periods,week:1,weekday:'周一'}
],()=> '2026-09-07'))));
'''
    rows = json.loads(subprocess.check_output(['node', '-e', script], cwd=root, text=True, encoding='utf8'))
    ranges = lambda events: [(e['start'][11:16], e['end'][11:16]) for e in events]
    assert ranges(rows[0]) == [('12:40', '15:15')]
    assert ranges(rows[1]) == [('08:10', '08:55'), ('10:15', '11:00')]
    assert ranges(rows[2]) == [('10:15', '11:50')]
    assert ranges(rows[3]) == [('12:40', '14:15')]
    assert ranges(rows[4]) == [('13:30', '15:15')]
    assert ranges(rows[5]) == ranges(rows[1])
    assert rows[6] == []
    assert all(not e['editable'] and not e['durationEditable'] for e in rows[1])
    assert len({e['id'] for e in rows[1]}) == 2
    assert rows[0][0]['editable']
