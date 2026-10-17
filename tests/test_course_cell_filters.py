import pytest

from core import schedule_core


@pytest.mark.parametrize("text", ["大3L99 E1A999", "大2X88 内0999(教师指定)"])
def test_numbered_course_alias_is_kept(text):
    assert not schedule_core._looks_like_room(text)
    assert schedule_core._is_chinese_full_term_course_text(text)


@pytest.mark.parametrize("text", ["思R99 校外999", "思R99\n校外实践基地", "大3L99 校外999"])
def test_course_with_off_campus_location_is_excluded(text):
    assert schedule_core._is_empty_or_non_class(text)
    assert not schedule_core._is_chinese_full_term_course_text(text)


@pytest.mark.parametrize("text", ["校外", "校外999", "教研", "不排课"])
def test_non_course_cells_are_still_ignored(text):
    assert schedule_core._is_empty_or_non_class(text)


@pytest.mark.parametrize("text", ["大301", "E1A999", "内0999", "L499"])
def test_room_only_cells_are_still_ignored(text):
    assert schedule_core._looks_like_room(text)
