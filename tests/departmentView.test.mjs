import assert from "node:assert/strict";
import test from "node:test";
import { availabilityForDepartment, departmentOptions, memberDepartmentOptions } from "../src/departmentView.ts";
import { availabilityWeekGrid, availabilitySlotDetail, availabilityToolbar, departmentPicker } from "../src/availabilityView.ts";

const source = [1, 2].map(period => ({
  "周次": 11, "日期": "2026-05-11", "星期": "周一", "节次": period, "时间": period + ":00",
  "空闲人数": 2, "空闲人员": "全协会名单", "占用人数": 1, "有课人员": "全协会有课名单"
}));
const result = {
  availability_preview: source, summary: { member_count: 3 },
  department_scopes: [
    { department: "办公室", labels: ["甲", "乙"], busy: [[1], [0, 1]] },
    { department: "宣传部", labels: ["乙"], busy: [[], []] }
  ]
};

test("department selection projects roster IDs into both lists including all-busy and all-free slots", () => {
  const office = availabilityForDepartment(result, "办公室");
  assert.deepEqual(departmentOptions(result), ["办公室", "宣传部"]);
  assert.equal(office.memberCount, 2);
  assert.deepEqual(office.rows.map(r => [r["空闲人数"], r["空闲人员"], r["占用人数"], r["有课人员"]]), [
    [1, "甲", 1, "乙"], [0, "", 2, "甲、乙"]
  ]);
  assert.equal(availabilityForDepartment(result, "宣传部").rows[0]["空闲人员"], "乙");
  const grid = availabilityWeekGrid(office.rows, 11, office.memberCount);
  assert.match(grid, /无人空闲/); assert.match(grid, /data-slot-index="1"/); assert.match(grid, /未覆盖/);
  assert.doesNotMatch(grid, /全协会名单/);
  const detail = availabilitySlotDetail(office.rows[0], "办公室");
  assert.match(detail, /办公室/); assert.match(detail, /有课人员（1）/);
  assert.doesNotMatch(detail, /全协会有课名单/);
  assert.deepEqual(availabilityForDepartment(result, ""), { rows: source, memberCount: 3 });
});

test("unknown or missing department data never substitutes the association roster", () => {
  assert.equal(availabilityForDepartment(result, "其他").rows.length, 0);
  assert.deepEqual(departmentOptions({ ...result, department_scopes: undefined }), []);
  const unaligned = { ...result, department_scopes: [{ ...result.department_scopes[0], busy: [] }] };
  assert.equal(availabilityForDepartment(unaligned, "办公室").rows.length, 0);
});

test("names containing delimiters are not split and scope selectors escape department text", () => {
  const special = { ...result, department_scopes: [{ department: "<组>", labels: ["甲、乙", "丙"], busy: [[1], []] }] };
  const { rows } = availabilityForDepartment(special, "<组>");
  assert.equal(rows[0]["空闲人数"], 1); assert.equal(rows[0]["空闲人员"], "甲、乙");
  const toolbar = availabilityToolbar(rows, 11, "list", departmentOptions(special), "<组>");
  assert.match(toolbar, /id="availabilityDepartment"/); assert.match(toolbar, /value="&lt;组&gt;" selected/);
  assert.doesNotMatch(toolbar, /<组>/);
});

test("coverage gaps do not create weeks and members remain filterable before coverage is known", () => {
  const rows = [...source, { ...source[0], "周次": 13 }];
  const toolbar = availabilityToolbar(rows, 13, "week", [], "");
  assert.doesNotMatch(toolbar, /第 12 周/);
  assert.match(toolbar, /data-week-step="1" aria-label="下一周" disabled/);
  assert.doesNotMatch(toolbar, /data-week-step="-1" aria-label="上一周" disabled/);
  const members = [{ department: " 宣传部 " }, { department: "" }, { department: "宣传部" }];
  assert.deepEqual(memberDepartmentOptions({ members }), ["宣传部"]);
  assert.match(departmentPicker(["宣传部"], "宣传部", "memberDepartment"), /id="memberDepartment"/);
});
