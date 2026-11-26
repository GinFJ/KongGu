import type { ParseResult } from "./types";

export function departmentOptions(result: ParseResult): string[] {
  return (result.department_scopes || []).map(({ department }) => department);
}

export function memberDepartmentOptions(result: ParseResult): string[] {
  const names = result.members.map(member => String(member.department || "").trim()).filter(Boolean);
  return [...new Set(names)].sort((a, b) => a.localeCompare(b, "zh-CN"));
}

export function membersForDepartment(result: ParseResult, department: string) {
  return department ? result.members.filter(member => String(member.department || "").trim() === department) : result.members;
}

export function availabilityForDepartment(result: ParseResult, department: string) {
  if (!department) return { rows: result.availability_preview, memberCount: result.summary.member_count || 0 };
  const scope = result.department_scopes?.find(option => option.department === department);
  if (!scope || scope.busy.length !== result.availability_preview.length) return { rows: [], memberCount: 0 };
  const rows = result.availability_preview.map((row, index) => {
    const occupiedIds = new Set(scope.busy[index]);
    const free: string[] = [], busy: string[] = [];
    scope.labels.forEach((name, id) => (occupiedIds.has(id) ? busy : free).push(name));
    return { ...row, "空闲人数": free.length, "空闲人员": free.join("、"), "占用人数": busy.length, "有课人员": busy.join("、") };
  });
  return { rows, memberCount: scope.labels.length };
}
