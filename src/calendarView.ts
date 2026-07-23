import { Calendar, EventDropArg } from "@fullcalendar/core";
import interactionPlugin, { EventResizeDoneArg } from "@fullcalendar/interaction";
import timeGridPlugin from "@fullcalendar/timegrid";
import type { CalendarSettings, CourseRow } from "./types";

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const periodTimes: Record<number, [string, string]> = {
  1: ["08:10", "08:55"], 2: ["09:00", "09:45"], 3: ["10:15", "11:00"],
  4: ["11:05", "11:50"], 12: ["12:40", "13:25"], 13: ["13:30", "14:15"],
  5: ["14:30", "15:15"], 6: ["15:20", "16:05"], 7: ["16:25", "17:10"],
  8: ["17:15", "18:00"], 9: ["19:10", "19:55"], 10: ["20:00", "20:45"],
  11: ["20:50", "21:35"]
};

export function mountCalendar(
  element: HTMLElement,
  courses: CourseRow[],
  settings: CalendarSettings,
  onEdit: (block: CourseRow, patch: Partial<CourseRow>) => Promise<void>
) {
  const firstWeek = courses[0]?.week || 1;
  const firstDate = dateFor(settings.semester_start_date, firstWeek, "周一");
  const calendar = new Calendar(element, {
    plugins: [timeGridPlugin, interactionPlugin],
    initialView: "timeGridWeek",
    initialDate: firstDate,
    locale: "zh-cn",
    firstDay: 1,
    allDaySlot: false,
    editable: true,
    eventDurationEditable: true,
    slotMinTime: "07:45:00",
    slotMaxTime: "22:00:00",
    slotDuration: "00:15:00",
    snapDuration: "00:05:00",
    height: "100%",
    nowIndicator: false,
    headerToolbar: { left: "prev,next today", center: "title", right: "" },
    events: courses.flatMap((course) => {
      const periods = course.periods?.length ? course.periods : course.period ? [course.period] : [];
      if (!periods.length) return [];
      const first = periodTimes[periods[0]];
      const last = periodTimes[periods[periods.length - 1]];
      if (!first || !last) return [];
      const day = dateFor(settings.semester_start_date, course.week, course.weekday);
      return [{
        id: course.block_id,
        title: `${course.course || "课程"} · ${course.name}`,
        start: `${day}T${first[0]}:00`,
        end: `${day}T${last[1]}:00`,
        extendedProps: { block: course }
      }];
    }),
    eventDrop: (arg) => void handleChange(arg, settings, onEdit),
    eventResize: (arg) => void handleChange(arg, settings, onEdit)
  });
  calendar.render();
  return calendar;
}

async function handleChange(
  arg: EventDropArg | EventResizeDoneArg,
  settings: CalendarSettings,
  onEdit: (block: CourseRow, patch: Partial<CourseRow>) => Promise<void>
) {
  const start = arg.event.start;
  const end = arg.event.end;
  const block = arg.event.extendedProps.block as CourseRow;
  if (!start || !end || start.toDateString() !== end.toDateString()) {
    arg.revert();
    return;
  }
  const weekAndDay = weekAndDayFor(settings.semester_start_date, start);
  const periods = periodsFor(start, end);
  if (
    !weekAndDay ||
    weekAndDay.week < 1 ||
    weekAndDay.week > settings.teaching_weeks ||
    !periods.length
  ) {
    arg.revert();
    return;
  }
  try {
    await onEdit(block, { week: weekAndDay.week, weekday: weekAndDay.weekday, periods });
  } catch {
    arg.revert();
  }
}

function periodsFor(start: Date, end: Date) {
  const startText = timeText(start);
  const endText = timeText(end);
  const ordered = Object.entries(periodTimes).map(([period, time]) => ({
    period: Number(period),
    start: time[0],
    end: time[1]
  })).sort((a, b) => a.start.localeCompare(b.start));
  const first = ordered.findIndex((item) => item.start === startText);
  const last = ordered.findIndex((item) => item.end === endText);
  if (first < 0 || last < first) return [];
  return ordered.slice(first, last + 1).map((item) => item.period);
}

function timeText(value: Date) {
  return `${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
}

function dateFor(startDate: string, week: number, weekday: string) {
  const start = new Date(`${startDate}T00:00:00`);
  const offset = (week - 1) * 7 + Math.max(0, weekdays.indexOf(weekday));
  start.setDate(start.getDate() + offset);
  return localDate(start);
}

function weekAndDayFor(startDate: string, value: Date) {
  const start = new Date(`${startDate}T00:00:00`);
  const current = new Date(value.getFullYear(), value.getMonth(), value.getDate());
  const diff = Math.round((current.getTime() - start.getTime()) / 86_400_000);
  if (diff < 0) return null;
  return { week: Math.floor(diff / 7) + 1, weekday: weekdays[diff % 7] };
}

function localDate(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}
