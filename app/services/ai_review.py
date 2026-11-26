"""Page-scoped AI observations, explicit upload consent and audited adoption."""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from uuid import uuid4

import fitz

from app.services import ai_transport
from app.services.review_operations import review_revision, mutate_courses
from app.services.review_service import get_review_payload
from core.legacy_adapter import hash_file
from core.signature import build_parser_signature
from core.models import ParseIssue
from core.quality import quality_state

_PREVIEWS = {}
_LOCK = threading.Lock()


def clear_previews():
    with _LOCK:
        _PREVIEWS.clear()


def _tables(store):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS ai_observations (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, binding TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS ai_calls (id TEXT PRIMARY KEY, day TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0)')
        db.execute('DELETE FROM ai_observations WHERE created < ?', (time.time()-30*86400,))
        db.execute('DELETE FROM ai_calls WHERE day < ?', (datetime.fromtimestamp(time.time()-32*86400, timezone.utc).date().isoformat(),))


def _context(store, job_id, source_hash):
    job = store.get_job(job_id, include_sensitive_paths=True)
    if not job or not job.get('result'):
        raise ValueError('请先导入并解析课表，再打开原文核对。')
    snapshot = job['result']
    if snapshot['parser_signature'] != build_parser_signature().digest:
        raise ValueError('学期设置或解析规则已变化，请重新生成后使用 AI。')
    review = get_review_payload(store, job_id)
    sources = [s for s in review['sources'] if s.get('content_hash') == source_hash]
    if len(sources) != 1:
        raise ValueError('无法唯一确定原件，请检查重复文件和身份。')
    source = sources[0]
    path = source.get('source_path')
    if not path or not Path(path).is_file() or hash_file(path) != source_hash:
        raise ValueError('原件已移动或变化，请重新导入。')
    return snapshot, source


def prepare(store, job_id, source_hash, page_number):
    snapshot, source = _context(store, job_id, source_hash)
    page_number = int(page_number)
    with fitz.open(source['source_path']) as doc:
        if not 0 <= page_number < len(doc):
            raise ValueError('页码超出原件范围。')
        page = doc[page_number]
        scale = min(2.5, 2400/max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        # Text-layer names can be located reliably. Image-only names need user masking.
        names = {b.get('name') for b in snapshot['generation'].get('blocks', []) if b.get('source_hash') == source_hash}
        for name in names:
            if not name:
                continue
            for rectangle in page.search_for(name):
                transformed = rectangle * page.rotation_matrix
                r = fitz.Rect(transformed.x0*scale-2, transformed.y0*scale-2,
                              transformed.x1*scale+2, transformed.y1*scale+2).irect
                pix.set_rect(r & pix.irect, (255,255,255))
        png = pix.tobytes('png')
        width, height = pix.width, pix.height
        count = len(doc)
    if len(png) > 8_000_000:
        raise ValueError('该页面图像过大，请先提供清晰且尺寸合适的原件。')
    token = str(uuid4())
    binding = dict(job_id=job_id, source_hash=source_hash, page=page_number,
                   parser_signature=snapshot['parser_signature'], file_name=source['file_name'])
    with _LOCK:
        for old in list(_PREVIEWS):
            if time.time()-_PREVIEWS[old]['created'] > 900:
                del _PREVIEWS[old]
        if len(_PREVIEWS) >= 8:
            del _PREVIEWS[next(iter(_PREVIEWS))]
        _PREVIEWS[token] = dict(binding=binding, png=png, created=time.time(), store=str(store.path))
    return dict(ok=True, token=token, image='data:image/png;base64,'+base64.b64encode(png).decode(),
                width=width, height=height, page=page_number, page_count=count, **ai_transport.configuration())


def mask_png(png, masks):
    if not isinstance(masks, list) or len(masks) > 50:
        raise ValueError('遮盖区域过多，请重新预览。')
    pix = fitz.Pixmap(png)
    for rectangle in masks:
        if not isinstance(rectangle, list) or len(rectangle) != 4:
            raise ValueError('遮盖区域无效。')
        x0,y0,x1,y1 = rectangle
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in rectangle) or x0>=x1 or y0>=y1:
            raise ValueError('遮盖区域无效。')
        pix.set_rect(fitz.IRect(math.floor(x0*pix.width), math.floor(y0*pix.height),
                               math.ceil(x1*pix.width), math.ceil(y1*pix.height)), (255,255,255))
    return pix.tobytes('png')


def compare(result, snapshot, source, page):
    calendar = {(int(r['week']),r['weekday']):str(r['date'])[:10] for r in snapshot['generation'].get('calendar_rows', [])}
    dates = {date:key for key,date in calendar.items()}
    local = [b for b in snapshot['generation'].get('blocks', []) if b.get('source_hash') == source['content_hash']]
    rows=[]
    covered=set()
    for index, raw in enumerate(result['courses']):
        if not isinstance(raw,dict):
            raise ValueError('AI 课程记录无效，请重新识别。')
        course=str(raw.get('course') or '')[:500]
        evidence=str(raw.get('evidence') or '')[:1000]
        uncertainty=str(raw.get('uncertainty') or '')[:1000]
        location=str(raw.get('location') or '')[:500]
        weeks=raw.get('weeks') or []
        periods=raw.get('periods') or []
        day=raw.get('weekday')
        date=str(raw.get('date') or '')
        if not isinstance(weeks,list) or not isinstance(periods,list):
            raise ValueError('AI 时间字段无效，请重新识别。')
        if len(weeks)>30 or len(periods)>13 or any(type(v) is not int for v in weeks+periods):
            raise ValueError('AI 周次或节次超出可处理范围。')
        if date:
            mapped=dates.get(date)
            if not mapped or (weeks and mapped[0] not in weeks) or day != mapped[1]:
                uncertainty = '日期、周次或星期与本批校历不一致。'
            elif mapped:
                weeks=[mapped[0]]
        if not course or not evidence or not weeks or not periods or any(p not in range(1,14) for p in periods):
            uncertainty = uncertainty or '缺少课程、原页依据、周次或节次。'
        if source['kind']=='中方' and any(p>11 for p in periods):
            uncertainty='中方午列不能补录为课程。'
        for week in sorted(set(weeks)) or [0]:
            reason=uncertainty
            if (week,day) not in calendar:
                reason=reason or '周次、星期不在本批校历中。'
            overlaps=[b for b in local if b['week']==week and b['weekday']==day and set(b.get('periods') or [b.get('period')]) & set(periods)]
            local_periods={p for b in overlaps for p in (b.get('periods') or [b.get('period')])}
            missing=sorted(set(periods)-local_periods)
            if reason:
                state='uncertain'
            elif location.startswith('校外'):
                state='excluded'
            elif not missing:
                state='matched'
            else:
                state='missing'
            if state not in ('uncertain','excluded'):
                covered.update((week,day,p) for p in periods)
            rows.append(dict(id=f'{index}-{week}', course=course, location=location, evidence=evidence,
                uncertainty=reason, week=week, weekday=day, periods=periods, missing_periods=missing,
                page=page, state=state, date=calendar.get((week,day),date)))
    local_only=[]
    for b in local:
        if b.get('page')==page:
            missing=[p for p in (b.get('periods') or [b.get('period')]) if (b['week'],b['weekday'],p) not in covered]
            if missing:
                local_only.append(dict(block_id=b['block_id'], course=b.get('course'),week=b['week'],weekday=b['weekday'],periods=missing))
    return dict(courses=rows, local_only=local_only, coverage=str(result.get('coverage',''))[:2000],
                warnings=[str(w)[:1000] for w in result.get('warnings',[])[:30]])


def _record_review_gate(store, job_id, observation_id, binding, comparison):
    needs_review = (comparison['local_only'] or comparison['warnings'] or
                    any(c['state'] != 'matched' for c in comparison['courses']))
    if not needs_review:
        return
    issue=ParseIssue(code='AI_PAGE_REVIEW_REQUIRED',
        message=f"第 {binding['page']+1} 页 AI 核对存在差异或不确定内容，需对照原文处理。",
        source_hash=binding['source_hash'],source_file=binding['file_name'],
        blocks_export=True,suggestion='先核对 AI 建议、补录漏课或修正现有记录，再确认本页已核对。AI 未识别的课程不能直接删除。',
        issue_id='ai-'+observation_id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:
            raise ValueError('任务已被清理，AI 结果未应用。')
        snapshot=json.loads(row['result_json'])
        if snapshot['parser_signature'] != binding['parser_signature']:
            raise ValueError('任务设置已变化，请重新识别。')
        if not any(i['issue_id']==issue.issue_id for i in snapshot['issues']):
            snapshot['issues'].append(asdict(issue))
        else:
            for existing in snapshot['issues']:
                if existing['issue_id']==issue.issue_id:
                    existing['confirmed']=False
        snapshot['quality_state']=quality_state([ParseIssue(**i) for i in snapshot['issues']])
        store.save_job_result(job_id,result_json=json.dumps(snapshot,ensure_ascii=False),
            error=str(row['error'] or ''),quality_state=snapshot['quality_state'],
            current=row['current'],total=row['total'],issues=snapshot['issues'],connection=db)


def retain_prior_ai_warnings(workflow, store):
    """Reimport must not silently forget an AI-observed uncertainty for the same source."""
    from app.services.workflow_persistence import snapshot_workflow
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_observations'").fetchone():
            return
        records=db.execute('SELECT id,binding,result FROM ai_observations ORDER BY created DESC LIMIT 1000').fetchall()
    snapshot=snapshot_workflow(workflow)
    sources={s['content_hash']:s for s in snapshot['generation']['sources']}
    seen=set()
    for record in records:
        binding=json.loads(record['binding'])
        source=sources.get(binding['source_hash'])
        if not source or binding['parser_signature'] != snapshot['parser_signature'] or source['file_name']!=binding['file_name']:
            continue
        page_key=(binding['source_hash'],binding['page'])
        if page_key in seen:
            continue
        # Every observation can carry independent evidence (including different masks).
        result=json.loads(record['result'])
        comparison=compare(result['result'],snapshot,source,binding['page'])
        if (comparison['local_only'] or comparison['warnings'] or any(c['state']!='matched' for c in comparison['courses'])):
            seen.add(page_key)
            workflow.process_result.issues.append(ParseIssue(code='AI_PAGE_REVIEW_REQUIRED',
                message=f"第 {binding['page']+1} 页此前 AI 核对有差异或不确定内容，本次导入须重新核对。",
                source_hash=binding['source_hash'],source_file=binding['file_name'],blocks_export=True,
                suggestion='打开原文核对，检查该页占用与不确定内容，不能把重新导入当作已解决。',
                issue_id='ai-reimport-'+record['id']))
    workflow.process_result.quality_state=quality_state(workflow.process_result.issues)


def run(store, request):
    if request.get('consent') is not True:
        raise ValueError('请先检查发送预览并同意联网识别。')
    token=str(request.get('token') or '')
    with _LOCK:
        preview=_PREVIEWS.pop(token,None)
    if not preview or preview['store'] != str(store.path) or time.time()-preview['created']>900:
        raise ValueError('发送预览已过期或已使用，请重新预览。')
    binding=preview['binding']
    snapshot,source=_context(store,binding['job_id'],binding['source_hash'])
    if snapshot['parser_signature']!=binding['parser_signature'] or source['file_name']!=binding['file_name']:
        raise ValueError('原件或学期设置已变化，请重新预览。')
    model=request.get('model',ai_transport.DEFAULT_MODEL)
    if model not in ai_transport.MODELS:
        raise ValueError('不支持的模型。')
    png=mask_png(preview['png'],request.get('masks',[]))
    binding.update(image_hash=hashlib.sha256(png).hexdigest(), model=model,prompt_version=ai_transport.PROMPT_VERSION)
    _tables(store)
    encoded=json.dumps(binding,sort_keys=True,ensure_ascii=False)
    with store.connect() as db:
        cached=db.execute('SELECT id,result FROM ai_observations WHERE binding=? ORDER BY created DESC LIMIT 1',(encoded,)).fetchone()
    if cached:
        result=json.loads(cached['result'])
        comparison=compare(result['result'],snapshot,source,binding['page'])
        _record_review_gate(store,binding['job_id'],cached['id'],binding,comparison)
        return dict(ok=True, observation_id=cached['id'], cached=True,
                    **comparison,job_id=binding['job_id'],
                    model=result['model'],input_tokens=0,output_tokens=0,source_hash=binding['source_hash'])
    call_id=str(uuid4())
    today=datetime.now(timezone.utc).date().isoformat()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        count=db.execute('SELECT count(*) FROM ai_calls WHERE day=?',(today,)).fetchone()[0]
        if count>=50:
            raise ValueError('本机今日已达到 50 次联网请求上限，请明日再试。此上限不能约束安装包之外的调用。')
        db.execute('INSERT INTO ai_calls(id,day) VALUES (?,?)',(call_id,today))
    calendar=[{k:r[k] for k in ('week','weekday','date')} for r in snapshot['generation'].get('calendar_rows',[])]
    result=ai_transport.extract_page('data:image/png;base64,'+base64.b64encode(png).decode(),source['kind'],calendar,model)
    # Record completed observations locally even if the user edited meanwhile; adoption checks current data.
    comparison=compare(result['result'],snapshot,source,binding['page'])
    with store.connect() as db:
        db.execute('INSERT INTO ai_observations VALUES (?,?,?,?,?)',(call_id,binding['job_id'],encoded,json.dumps(result,ensure_ascii=False),time.time()))
        db.execute('UPDATE ai_calls SET input_tokens=?,output_tokens=? WHERE id=?',(result['input_tokens'],result['output_tokens'],call_id))
    _record_review_gate(store,binding['job_id'],call_id,binding,comparison)
    return dict(ok=True,observation_id=call_id,cached=False,job_id=binding['job_id'],**comparison,model=result['model'],
                input_tokens=result['input_tokens'],output_tokens=result['output_tokens'],source_hash=binding['source_hash'])


def adopt(store, schedule_core, request):
    if request.get('confirmed') is not True or not str(request.get('reason') or '').strip():
        raise ValueError('请对照原文并填写核对原因后补录。')
    _tables(store)
    with store.connect() as db:
        stored=db.execute('SELECT * FROM ai_observations WHERE id=?',(str(request.get('observation_id') or ''),)).fetchone()
    if not stored or stored['job_id']!=request.get('job_id'):
        raise ValueError('未找到本任务的 AI 建议，请重新识别。')
    binding=json.loads(stored['binding'])
    snapshot,source=_context(store,stored['job_id'],binding['source_hash'])
    if snapshot['parser_signature']!=binding['parser_signature'] or source['file_name']!=binding['file_name']:
        raise ValueError('AI 建议已过期，请重新识别。')
    result=json.loads(stored['result'])
    candidates=compare(result['result'],snapshot,source,binding['page'])['courses']
    candidate=next((c for c in candidates if c['id']==request.get('candidate_id')),None)
    if not candidate or candidate['state']!='missing':
        raise ValueError('该建议已处理或仍缺少依据，请检查当前课程。')
    fields={k:candidate[k] for k in ('course','week','weekday','page')}
    fields['periods']=candidate['missing_periods']
    reason=f"AI建议 {stored['id']} / {binding['model']} / {binding['prompt_version']}；原页依据：{candidate['evidence'][:300]}；人工核对：{str(request['reason'])[:900]}"
    return mutate_courses(store=store,schedule_core=schedule_core,job_id=stored['job_id'],request=dict(
        action='add',source_hash=binding['source_hash'],revision=request.get('revision'),
        operation_id=request.get('operation_id'),course=fields,reason=reason,operator_id='本机用户（AI建议经人工核对）'))
