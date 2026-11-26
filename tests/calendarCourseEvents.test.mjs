import assert from "node:assert/strict";
import test from "node:test";
import { groupedCourseEvents } from "../src/calendarCourseEvents.ts";

test("grouped periods keep gaps, distinguish same-name members and count each member once", () => {
  const courses = [
    { day: "2026-03-02", member_key: "a", name: "同名", periods: [1, 1, 3], course: "甲" },
    { day: "2026-03-02", member_key: "a", name: "同名", period: 1, course: "乙" },
    { day: "2026-03-02", member_key: "b", name: "同名", periods: [1, 12], course: "丙" },
    { day: "2026-03-09", member_key: "c", name: "另一位", periods: [1], course: "丁" }
  ];
  const events = groupedCourseEvents(courses, row => row.day);
  const slots = new Map(events.map(event => [event.id, event.extendedProps.slot]));
  assert.equal(slots.size, 4);
  assert.equal(slots.get("slot-2026-03-02-1").memberCount, 2);
  assert.equal(slots.get("slot-2026-03-02-1").courses.length, 3);
  assert.equal(slots.get("slot-2026-03-02-3").memberCount, 1);
  assert.equal(slots.get("slot-2026-03-09-1").memberCount, 1);
  assert(!slots.has("slot-2026-03-02-2"));
  assert(events.every(event => !event.editable && !event.durationEditable));
  assert.equal(events.find(event => event.id.endsWith("-12")).start, "2026-03-02T12:40:00");
});
