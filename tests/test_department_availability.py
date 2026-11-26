from types import SimpleNamespace

from app.services.department_availability import build_department_scopes
from core.models import MemberSchedule


def test_department_rosters_intersect_real_occupancy_and_keep_preview_slot_order():
    first = MemberSchedule(name="示例甲", department="办公室", role="干事")
    namesake = MemberSchedule(name="示例甲", department="办公室", role="部长")
    other = MemberSchedule(name="示例甲", department="宣传部", role="干事")
    missing = MemberSchedule(name="示例乙")
    roster = [first, namesake, other, missing]
    generation = SimpleNamespace(students=[m.member_key for m in roster], occupancy={
        (11, "周一", 2): {other.member_key, missing.member_key},
        (11, "周一", 3): {namesake.member_key},
        (12, "周一", 2): {first.member_key},
    })
    workflow = SimpleNamespace(generation_result=generation, process_result=SimpleNamespace(members=roster))
    rows = [{"周次": 11, "星期": "周一", "节次": period} for period in [3, 2]]
    results = {s["department"]: s for s in build_department_scopes(workflow, rows)}
    assert set(results) == {"办公室", "宣传部"}
    assert results["办公室"]["busy"] == [[1], []]
    assert len(set(results["办公室"]["labels"])) == 2
    assert results["宣传部"]["busy"] == [[], [0]]
    assert results["宣传部"]["labels"] == ["示例甲"]
    assert all(s["busy"] == [] for s in build_department_scopes(workflow, []))
