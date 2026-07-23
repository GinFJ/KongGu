"""Member identity extraction and conflict-safe display helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .models import MemberIdentity


DEPARTMENTS = ("办公室", "外联部", "宣传部", "活动部")
ROLES = ("副部长", "部长", "干事", "负责人", "成员")
_IGNORED = {*DEPARTMENTS, *ROLES, "中方", "英方", "中方课表", "英方课表", "课表"}


def identity_from_filename(file_name: str, fallback_name: str = "") -> MemberIdentity:
    """Extract name, department and role without retaining the source path."""

    stem = Path(file_name).stem
    department = next((item for item in DEPARTMENTS if item in stem), None)
    role = next((item for item in ROLES if item in stem), None)
    name = fallback_name.strip() or _infer_name(stem)
    return MemberIdentity(name=name, department=department, role=role)


def _infer_name(stem: str) -> str:
    normalized = re.sub(r"[-_\s]+", " ", stem)
    for token in normalized.split():
        cleaned = re.sub(r"[^\u4e00-\u9fff]", "", token)
        if 2 <= len(cleaned) <= 4 and cleaned not in _IGNORED:
            return cleaned
    cleaned = stem
    for word in sorted(_IGNORED, key=len, reverse=True):
        cleaned = cleaned.replace(word, " ")
    matches = re.findall(r"[\u4e00-\u9fff]{2,4}", cleaned)
    return matches[0] if matches else ""
