import { Calendar, EventDropArg } from "@fullcalendar/core";
import zhCnLocale from "@fullcalendar/core/locales/zh-cn";
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
  onEdit: (block: CourseRow, patch: Partial<CourseRow>) => Promise<void>,
  initialDate?: Date,
  onError?: (message: string) => void
) {
  const firstWeek = courses[0]?.week || 1;
  const firstDate = dateFor(settings.semester_start_date, firstWeek, "周一");
  const calendar = new Calendar(element, {
    plugins: [timeGridPlugin, interactionPlugin],
    initialView: "timeGridWeek",
    initialDate: initialDate ?? firstDate,
    locale: "zh-cn",
    locales: [zhCnLocale],
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
    eventDrop: (arg) => void handleChange(arg, settings, onEdit, onError),
    eventResize: (arg) => void handleChange(arg, settings, onEdit, onError)
  });
  calendar.render();
  return calendar;
}

async function handleChange(
  arg: EventDropArg | EventResizeDoneArg,
  settings: CalendarSettings,
  onEdit: (block: CourseRow, patch: Partial<CourseRow>) => Promise<void>,
  onError?: (message: string) => void
) {
  const start = arg.event.start;
  const end = arg.event.end;
  const block = arg.event.extendedProps.block as CourseRow;
  if (!start || !end || start.toDateString() !== end.toDateString()) {
    onError?.("周课表拖拽只能在同一天内移动；跨天调整请使用“原文核对”。");
    revertSafely(arg);
    return;
  }
  const weekAndDay = weekAndDayFor(settings.semester_start_date, start);
  const periods = periodsFor(start, end);
  if (!weekAndDay || weekAndDay.week < 1 || weekAndDay.week > settings.teaching_weeks) {
    onError?.("目标日期超出当前学期教学周，请先在设置里调整第 1 周或周数。");
    revertSafely(arg);
    return;
  }
  if (!periods.length) {
    onError?.("拖拽位置无法吸附到具体节次；请拖到节次网格上，或使用“原文核对”修改周次和节次。");
    revertSafely(arg);
    return;
  }
  try {
    await onEdit(block, { week: weekAndDay.week, weekday: weekAndDay.weekday, periods });
  } catch {
    // 保存失败的原因由上层状态栏统一显示，这里只恢复位置。
    revertSafely(arg);
  }
}

function revertSafely(arg: EventDropArg | EventResizeDoneArg) {
  try {
    arg.revert();
  } catch {
    // 保存过程中结果刷新可能已经重建日历，销毁后的实例不能再执行 revert。
  }
}

const SNAP_TOLERANCE_MINUTES = 15;

function periodsFor(start: Date, end: Date) {
  const ordered = Object.entries(periodTimes).map(([period, time]) => ({
    period: Number(period),
    start: time[0],
    end: time[1]
  })).sort((a, b) => a.start.localeCompare(b.start));
  // 拖拽/缩放的落点很少正好对齐节次边界（5 分钟粒度），吸附到最近的合法边界，
  // 避免用户看到“拖了没反应”却没有解释。
  const first = nearestSlotIndex(ordered, timeText(start), "start");
  const last = nearestSlotIndex(ordered, timeText(end), "end");
  if (first < 0 || last < 0 || last < first) return [];
  return ordered.slice(first, last + 1).map((item) => item.period);
}

function nearestSlotIndex(
  ordered: Array<{ period: number; start: string; end: string }>,
  text: string,
  edge: "start" | "end"
) {
  const target = minutesOf(text);
  let best = -1;
  let bestDelta = Number.POSITIVE_INFINITY;
  ordered.forEach((item, index) => {
    const delta = Math.abs(minutesOf(edge === "start" ? item.start : item.end) - target);
    if (delta <= SNAP_TOLERANCE_MINUTES && delta < bestDelta) {
      bestDelta = delta;
      best = index;
    }
  });
  return best;
}

function minutesOf(text: string) {
  const [hours, minutes] = text.split(":").map(Number);
  return hours * 60 + minutes;
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
