import type { CourseRow } from "./types";

export const periodTimes: Record<number, [string, string]> = {
  1: ["08:10", "08:55"], 2: ["09:00", "09:45"], 3: ["10:15", "11:00"],
  4: ["11:05", "11:50"], 12: ["12:40", "13:25"], 13: ["13:30", "14:15"],
  5: ["14:30", "15:15"], 6: ["15:20", "16:05"], 7: ["16:25", "17:10"],
  8: ["17:15", "18:00"], 9: ["19:10", "19:55"], 10: ["20:00", "20:45"],
  11: ["20:50", "21:35"]
};

const order = Object.keys(periodTimes).map(Number)
  .sort((a, b) => periodTimes[a][0].localeCompare(periodTimes[b][0]));

export type CourseSlot = { day: string; period: number; courses: CourseRow[]; memberCount: number };

export function groupedCourseEvents(courses: CourseRow[], dayFor: (course: CourseRow) => string) {
  const slots = new Map<string, CourseSlot>();
  for (const course of courses) {
    const day = dayFor(course);
    const periods = new Set(course.periods?.length ? course.periods : course.period ? [course.period] : []);
    for (const period of periods) {
      if (!periodTimes[period]) continue;
      const key = `${day}:${period}`;
      const slot = slots.get(key) || { day, period, courses: [], memberCount: 0 };
      slot.courses.push(course);
      slots.set(key, slot);
    }
  }
  return [...slots.values()].map(slot => {
    slot.memberCount = new Set(slot.courses.map(course => course.member_key || course.name)).size;
    const [start, end] = periodTimes[slot.period];
    return { id: `slot-${slot.day}-${slot.period}`, title: `${slot.memberCount} 人有课`,
      start: `${slot.day}T${start}:00`, end: `${slot.day}T${end}:00`, editable: false,
      durationEditable: false, classNames: ["groupedCourse"], extendedProps: {slot} };
  });
}

export function courseEvents(courses: CourseRow[], dayFor: (course: CourseRow) => string) {
  return courses.flatMap(course => {
    const raw = course.periods?.length ? course.periods : course.period ? [course.period] : [];
    const periods = [...new Set(raw)].filter(p => periodTimes[p])
      .sort((a, b) => order.indexOf(a) - order.indexOf(b));
    const groups: number[][] = [];
    for (const period of periods) {
      const last = groups[groups.length - 1];
      if (last && order.indexOf(period) === order.indexOf(last[last.length - 1]) + 1) last.push(period);
      else groups.push([period]);
    }
    const split = groups.length > 1;
    const day = dayFor(course);
    return groups.map((group, index) => ({
      id: split ? `${course.block_id}-part-${index}` : course.block_id,
      title: `${course.course || "课程"} · ${course.name}${split ? " · 分段" : ""}`,
      start: `${day}T${periodTimes[group[0]][0]}:00`,
      end: `${day}T${periodTimes[group[group.length - 1]][1]}:00`,
      // Dragging just one segment must not silently drop the other periods.
      editable: !split,
      durationEditable: !split,
      extendedProps: { block: course, split }
    }));
  });
}
