"""Department rosters and slot occupancy for the desktop results view."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.availability_preview_service import build_display_name_map
from app.services.department_scope import department_members

if TYPE_CHECKING:
    from app.services.desktop_workflow import DesktopWorkflowResult


def build_department_scopes(workflow: DesktopWorkflowResult, preview_rows: list[dict]) -> list[dict]:
    if not workflow.process_result.members:
        return []
    generation = workflow.generation_result
    student_keys = set(generation.students)
    departments = sorted({member.department.strip() for member in workflow.process_result.members
                          if member.department and member.department.strip() and member.member_key in student_keys})
    # Only project slots present in the association preview. Missing weeks or
    # day/period cells cannot become free through filtering.
    slots = [(row["周次"], row["星期"], row["节次"]) for row in preview_rows]
    scopes = []
    for department in departments:
        members = department_members(workflow, department)
        labels = build_display_name_map(members)
        positions = {key: index for index, key in enumerate(members)}
        occupied = [sorted(positions[key] for key in generation.occupancy.get(slot, ()) if key in positions)
                    for slot in slots]
        scopes.append({"department": department, "labels": [labels[key] for key in members], "busy": occupied})
    return scopes
