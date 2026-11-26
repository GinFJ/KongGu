"""AI suggestions must preserve source binding, review gates and audit history."""
import base64
from copy import deepcopy
import io
import json
from uuid import uuid4
from urllib.error import HTTPError

import fitz
import pytest

from app.services import ai_review as ai, ai_transport as transport
from app.services.review_service import get_review_payload, confirm_issue
from app.services.workflow_persistence import restore_workflow
from app.services.desktop_workflow import export_excel
from core import schedule_core as sc
from tests.test_review_operations import saved, save


def observation(**changes):
    course=dict(course='SYNTHETIC101',location='R101',weeks=[1],weekday='周一',date='2026-09-07',
                periods=[3,4],evidence='Monday row under periods 3 and 4',uncertainty='')
    course.update(changes)
    return dict(result=dict(courses=[course],coverage='Week 1 Monday only',warnings=[]),
                model=transport.DEFAULT_MODEL,input_tokens=200,output_tokens=80)


def prepared(saved):
    store,job,_,_=saved
    review=get_review_payload(store,job)
    return ai.prepare(store,job,review['sources'][0]['content_hash'],0)


def test_masked_pixels_are_white_and_invalid_geometry_rejected():
    with fitz.open() as doc:
        page=doc.new_page(width=100,height=100)
        page.draw_rect(page.rect,color=(0,0,0),fill=(0,0,0))
        original=page.get_pixmap().tobytes('png')
    pix=fitz.Pixmap(ai.mask_png(original,[[0,.1,.5,.6]]))
    assert pix.pixel(20,20)==(255,255,255)
    assert pix.pixel(80,80)==(0,0,0)
    for mask in ([0,0,float('nan'),1],[.8,0,.2,1],[-1,0,.2,1]):
        with pytest.raises(ValueError): ai.mask_png(original,[mask])


def test_ai_requires_consent_source_and_single_use_preview(saved,monkeypatch):
    p=prepared(saved)
    calls=[]
    monkeypatch.setattr(transport,'extract_page',lambda *a: calls.append(a) or observation())
    with pytest.raises(ValueError,match='同意'): ai.run(saved[0],dict(token=p['token']))
    assert not calls
    result=ai.run(saved[0],dict(token=p['token'],consent=True,masks=[]))
    assert len(calls)==1 and result['courses'][0]['state']=='missing'
    with pytest.raises(ValueError,match='已使用'): ai.run(saved[0],dict(token=p['token'],consent=True))
    assert len(calls)==1


def test_suggestion_blocks_export_and_adoption_is_audited_and_recomputed(saved,monkeypatch,tmp_path):
    store,job,_,member=saved
    monkeypatch.setattr(transport,'extract_page',lambda *a: observation())
    p=prepared(saved)
    result=ai.run(store,dict(token=p['token'],consent=True,masks=[]))
    review=get_review_payload(store,job)
    assert review['quality_state']=='needs_review'
    assert len(review['blocks'])==2
    with pytest.raises(ValueError):
        export_excel(schedule_core=sc,workflow_result=restore_workflow(store.get_job(job)['result'],sc),target_path=str(tmp_path/'blocked.xlsx'))
    response=ai.adopt(store,sc,dict(job_id=job,observation_id=result['observation_id'],candidate_id='0-1',
        confirmed=True,reason='Checked synthetic page',revision=review['revision'],operation_id=str(uuid4())))
    assert len(response['blocks'])==3
    new=next(b for b in response['blocks'] if b.get('course')=='SYNTHETIC101')
    assert new['periods']==[3,4] and new['name']=='成员甲'
    history=response['corrections'][-1]
    assert result['observation_id'] in history['reason']
    assert transport.PROMPT_VERSION in history['reason']
    assert response['quality_state']=='needs_review'
    gate=next(i for i in response['issues'] if i['code']=='AI_PAGE_REVIEW_REQUIRED')
    confirm_issue(store,job,gate['issue_id'],reason='All page differences checked')
    from tests.course_verification_helpers import verify_test_job
    verify_test_job(store, job)
    workflow=restore_workflow(store.get_job(job)['result'],sc)
    assert member in workflow.generation_result.occupancy[(1,'周一',3)]
    export_excel(schedule_core=sc,workflow_result=workflow,target_path=str(tmp_path/'accepted.xlsx'))


def test_cache_has_no_second_call_and_source_changes_refuse(saved,monkeypatch):
    calls=[]
    monkeypatch.setattr(transport,'extract_page',lambda *a: calls.append(a) or observation())
    first=ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    second=ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    assert first['observation_id']==second['observation_id'] and second['cached'] and len(calls)==1
    p=prepared(saved)
    with open(saved[2][0],'ab') as stream: stream.write(b'\nchanged')
    with pytest.raises(ValueError,match='变化'): ai.run(saved[0],dict(token=p['token'],consent=True,masks=[]))
    assert len(calls)==1


@pytest.mark.parametrize('changes,state',[
    ({'date':'2026-09-08'},'uncertain'),({'weeks':[],'date':''},'uncertain'),
    ({'location':'校外实习'},'excluded'),({'periods':[12]},'uncertain'),
    ({'uncertainty':'cannot read date'},'uncertain'),({'periods':[1]},'matched')])
def test_uncertain_excluded_and_existing_slots_are_not_added(saved,changes,state):
    snap,source=ai._context(saved[0],saved[1],get_review_payload(saved[0],saved[1])['sources'][0]['content_hash'])
    output=ai.compare(observation(**changes)['result'],snap,source,0)
    assert output['courses'][0]['state']==state


def test_failure_does_not_modify_courses_or_leak_request(saved,monkeypatch):
    snapshot=deepcopy(saved[0].get_job(saved[1])['result'])
    def fail(*a): raise ValueError('AI 服务连接失败')
    monkeypatch.setattr(transport,'extract_page',fail)
    with pytest.raises(ValueError): ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    assert saved[0].get_job(saved[1])['result']==snapshot


def test_transport_never_exposes_remote_error_or_key(monkeypatch):
    monkeypatch.setattr(transport,'load_key',lambda:'test-private-credential')
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='https://api.openai.com/v1/responses'
            body=json.loads(request.data)
            assert body['store'] is False and 'tools' not in body
            raise HTTPError(request.full_url,401,'contains sensitive details',{},io.BytesIO(b'private-body'))
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *a:Opener())
    with pytest.raises(ValueError) as error: transport.extract_page('data:image/png;base64,AA==','中方',[])
    assert '凭据无效' in str(error.value) and 'private' not in str(error.value)


def test_transport_incomplete_result_rejected(monkeypatch):
    monkeypatch.setattr(transport,'load_key',lambda:'test-private-credential')
    class Opener:
        def open(self,*a,**k): return io.BytesIO(json.dumps(dict(status='incomplete',output=[])).encode())
    monkeypatch.setattr(transport.urllib.request,'build_opener',lambda *a:Opener())
    with pytest.raises(ValueError,match='未完整'): transport.extract_page('data:image/png;base64,AA==','中方',[])


def test_daily_limit_and_cross_job_adoption_refused(saved,monkeypatch):
    monkeypatch.setattr(transport,'extract_page',lambda *a:observation())
    result=ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    with pytest.raises(ValueError,match='本任务'):
        ai.adopt(saved[0],sc,dict(job_id='other',observation_id=result['observation_id'],confirmed=True,reason='checked'))
    ai._tables(saved[0])
    today=ai.datetime.now(ai.timezone.utc).date().isoformat()
    with saved[0].connect() as db:
        for i in range(50): db.execute('INSERT INTO ai_calls(id,day) VALUES (?,?)',(str(uuid4()),today))
    with pytest.raises(ValueError,match='50 次'):
        ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[],model='gpt-4.1'))


def test_stale_revision_cannot_overwrite_and_clear_cascades(saved,monkeypatch):
    monkeypatch.setattr(transport,'extract_page',lambda *a:observation())
    result=ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    with pytest.raises(ValueError,match='结果已经变化'):
        ai.adopt(saved[0],sc,dict(job_id=saved[1],observation_id=result['observation_id'],candidate_id='0-1',
            confirmed=True,reason='checked',revision='old',operation_id=str(uuid4())))
    saved[0].clear_all_data()
    with saved[0].connect() as db:
        assert db.execute('SELECT count(*) FROM ai_observations').fetchone()[0]==0


def test_reimport_does_not_drop_previous_ai_uncertainty(saved,monkeypatch):
    original=deepcopy(saved[0].get_job(saved[1])['result'])
    monkeypatch.setattr(transport,'extract_page',lambda *a:observation())
    ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    workflow=restore_workflow(original,sc)
    ai.retain_prior_ai_warnings(workflow,saved[0])
    assert workflow.process_result.quality_state=='needs_review'
    assert any(i.code=='AI_PAGE_REVIEW_REQUIRED' for i in workflow.process_result.issues)


def test_cached_difference_requires_fresh_confirmation(saved,monkeypatch):
    monkeypatch.setattr(transport,'extract_page',lambda *a:observation())
    r=ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    confirm_issue(saved[0],saved[1],'ai-'+r['observation_id'])
    assert get_review_payload(saved[0],saved[1])['quality_state']=='accepted'
    r=ai.run(saved[0],dict(token=prepared(saved)['token'],consent=True,masks=[]))
    assert r['cached']
    assert get_review_payload(saved[0],saved[1])['quality_state']=='needs_review'
