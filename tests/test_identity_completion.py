"""Missing filename metadata must not split an unambiguous source pair."""
import hashlib

import pandas as pd
import pytest

from app.services.generate_availability_service import generate_availability
from core import schedule_core as sc
from core.models import PdfSource
from core.quality import evaluate_quality


def run_pair(monkeypatch, names):
    sources = [PdfSource(file_name=name, kind=kind, source_path=name,
                         content_hash=hashlib.sha256(name.encode()).hexdigest())
               for name, kind in names]
    def parse(inputs, **kwargs):
        blocks = [dict(name='示例甲', source_type=s['kind'], kind=s['kind'],
                       source_file=s['file_name'], week=1, weekday='周一',
                       date='2026-03-02', period=n+1, course='课程', text_source='内嵌文本')
                  for n,s in enumerate(inputs)]
        return blocks, pd.DataFrame([dict(date='2026-03-02', week=1, weekday='周一')]), [], pd.DataFrame(blocks)
    monkeypatch.setattr(sc, 'parse_actual_pdf_sources', parse)
    monkeypatch.setattr(sc, 'detect_schedule_layout_profile', lambda *a: None)
    return generate_availability(schedule_core=sc, selected_sources=sources,
                                 root_dir='', weekdays=['周一'], pdf_inspections=[])


@pytest.mark.parametrize('partial', ['外联部示例甲中方课表.pdf', '示例甲.pdf'])
@pytest.mark.parametrize('reverse', [False, True])
def test_unique_complementary_pair_has_one_member_and_both_occupancies(monkeypatch, partial, reverse):
    names = [(partial, '中方'), ('外联部-示例甲-干事-英方课表.pdf', '英方')]
    result = run_pair(monkeypatch, names[::-1] if reverse else names)
    assert len(result.member_schedules) == 1
    member = result.member_schedules[0]
    assert member.has_chinese and member.has_english
    assert member.member_key == '示例甲｜外联部｜干事'
    assert all(values == {member.member_key} for values in result.occupancy.values())
    completed = [b for b in result.course_blocks if b.provenance.get('identity_completion')]
    assert len(completed) == 1
    assert completed[0].provenance['identity_completion']['peer_source_hash']
    assert not any(i.blocks_export for i in evaluate_quality(
        file_records=result.file_records, members=result.member_schedules)[1])


@pytest.mark.parametrize('names', [
    [('示例甲.pdf','中方'),('外联部-示例甲-干事-英方课表.pdf','英方'),('办公室-示例甲-干事-英方课表.pdf','英方')],
    [('办公室示例甲中方课表.pdf','中方'),('外联部-示例甲-干事-英方课表.pdf','英方')],
    [('示例甲-部长-中方课表.pdf','中方'),('外联部-示例甲-干事-英方课表.pdf','英方')],
    [('示例甲-中方课表.pdf','中方'),('示例甲-英方课表.pdf','英方')],
    [('示例甲-中方课表.pdf','中方'),('外联部-示例甲-干事-中方课表.pdf','中方')],
])
def test_ambiguous_conflicting_or_same_kind_sources_are_not_completed(monkeypatch, names):
    result = run_pair(monkeypatch, names)
    assert not any(b.provenance.get('identity_completion') for b in result.course_blocks)
    assert any(i.blocks_export for i in evaluate_quality(
        file_records=result.file_records, members=result.member_schedules)[1])
