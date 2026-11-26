import assert from "node:assert/strict";
import test from "node:test";
import { outsideSemester, suggestedDates } from "../src/batchDates.ts";
import { workbookName } from "../src/exportOptions.ts";

test("new batch defaults stay inside the semester at both edges", () => {
  const term = { semester_start_date: "2026-09-21", teaching_weeks: 16 };
  const dates = (month, day) => suggestedDates(term, new Date(2026, month - 1, day, 12));
  assert.deepEqual(dates(8, 1), { start_date: "2026-09-21", end_date: "2026-09-30" });
  assert.deepEqual(dates(11, 15), { start_date: "2026-11-01", end_date: "2026-11-30" });
  assert.deepEqual(suggestedDates(term, new Date(2027, 0, 4)), {start_date: "2027-01-01", end_date: "2027-01-10"});
  assert.equal(outsideSemester({start_date: "2027-01-10", end_date: "2027-01-11"}, term), true);
});

test("a date-based workbook carries its inclusive dates across the month boundary", () => {
  const dates = { start_date: "2026-11-30", end_date: "2026-12-04" };
  assert.equal(workbookName("办公室", {mode: "qinghe"}, dates), "办公室_青禾排班空课表_2026-11-30至2026-12-04.xlsx");
  assert.equal(workbookName("", {mode: "qinghe", week: 3}), "全部部门_青禾工作日空课表_第3周.xlsx");
});
