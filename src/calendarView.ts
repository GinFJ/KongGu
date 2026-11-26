import { Calendar, EventDropArg } from "@fullcalendar/core";
import zhCnLocale from "@fullcalendar/core/locales/zh-cn";
import interactionPlugin, { EventResizeDoneArg } from "@fullcalendar/interaction";
import timeGridPlugin from "@fullcalendar/timegrid";
import type { CalendarSettings, CourseRow } from "./types";
import { courseEvents, groupedCourseEvents, periodTimes, type CourseSlot } from "./calendarCourseEvents";

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export function mountCalendar(
  element: HTMLElement,
  courses: CourseRow[],
  settings: CalendarSettings,
  onEdit: (block: CourseRow, patch: Partial<CourseRow>) => Promise<void>,
  initialDate?: Date,
  onError?: (message: string) => void,
  onSlot?: (slot: CourseSlot) => void,
  scope: { member: string } = { member: "" }
) {
  const members = new Map(courses.map(course => [course.member_key || course.name, course]));
  if (!members.has(scope.member)) scope.member = "";
  const filter = document.createElement("label");
  filter.className = "calendarFilter";
  filter.append("成员");
  const select = document.createElement("select");
  select.setAttribute("aria-label", "按成员查看课程");
  select.add(new Option("全部成员 · 按节汇总", ""));
  for (const [key, course] of members) {
    select.add(new Option([course.name, course.department, course.role].filter(Boolean).join(" · "), key));
  }
  select.value = scope.member;
  filter.append(select);
  filter.hidden = members.size < 2;
  const canvas = document.createElement("div");
  canvas.className = "calendarCanvas";
  canvas.classList.toggle("groupedWeek", members.size > 1 && !scope.member);
  element.replaceChildren(filter, canvas);
  const courseDays = courses.map(course => ({course, day: dateFor(settings.semester_start_date, course.week, course.weekday)}));
  const firstWeek = courses[0]?.week || 1;
  const firstDate = dateFor(settings.semester_start_date, firstWeek, "周一");
  const calendar = new Calendar(canvas, {
    plugins: [timeGridPlugin, interactionPlugin],
    initialView: "timeGridWeek",
    initialDate: initialDate ?? firstDate,
    locale: "zh-cn",
    locales: [zhCnLocale],
    firstDay: 1,
    allDaySlot: false,
    editable: true,
    eventDurationEditable: true,
    slotMinTime: "08:00:00",
    slotLabelInterval: "01:00:00",
    slotLabelFormat: {hour: "2-digit", minute: "2-digit", hour12: false},
    slotMaxTime: "22:00:00",
    slotDuration: canvas.classList.contains("groupedWeek") ? "00:30:00" : "00:15:00",
    snapDuration: "00:05:00",
    height: "100%",
    nowIndicator: false,
    headerToolbar: { left: "prev,next today", center: "title", right: "" },
    events: (range, success) => {
      const from = localDate(range.start), to = localDate(range.end);
      const visible = courseDays.filter(({course, day}) => day >= from && day < to &&
        (!scope.member || (course.member_key || course.name) === scope.member)).map(row => row.course);
      const dayFor = (course: CourseRow) => dateFor(settings.semester_start_date, course.week, course.weekday);
      success(members.size > 1 && !scope.member ? groupedCourseEvents(visible, dayFor) : courseEvents(visible, dayFor));
    },
    eventClick: ({event}) => { if (event.extendedProps.slot) onSlot?.(event.extendedProps.slot); },
    eventDidMount: ({ el, event }) => {
      const slot: CourseSlot | undefined = event.extendedProps.slot;
      if (slot) {
        el.tabIndex = 0;
        el.setAttribute("role", "button");
        el.setAttribute("aria-label", `${slot.day} 第 ${slot.period} 节，${event.title}，查看课程名单`);
        el.addEventListener("keydown", e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSlot?.(slot); }
        });
      }
      el.title = slot ? `第 ${slot.period} 节 · ${event.title}` : event.title + (event.extendedProps.split ? "；不连续课程请在“原文核对”修改。" : "");
    },
    eventDrop: (arg) => void handleChange(arg, settings, onEdit, onError),
    eventResize: (arg) => void handleChange(arg, settings, onEdit, onError)
  });
  select.addEventListener("change", () => {
    scope.member = select.value;
    const grouped = members.size > 1 && !scope.member;
    canvas.classList.toggle("groupedWeek", grouped);
    calendar.setOption("slotDuration", grouped ? "00:30:00" : "00:15:00");
    calendar.refetchEvents();
    calendar.updateSize();
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
