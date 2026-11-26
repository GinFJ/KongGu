export type BatchDates = { start_date: string; end_date: string };
type Semester = { semester_start_date: string; teaching_weeks: number };

export function currentMonth(today = new Date()): BatchDates {
  const year = today.getFullYear();
  const month = today.getMonth() + 1;
  const prefix = year + "-" + String(month).padStart(2, "0");
  return { start_date: prefix + "-01", end_date: prefix + "-" + new Date(year, month, 0).getDate() };
}

export function semesterDates(semester: Semester): BatchDates {
  const start = semester.semester_start_date;
  const end = new Date(start + "T00:00:00Z");
  end.setUTCDate(end.getUTCDate() + semester.teaching_weeks * 7 - 1);
  return { start_date: start, end_date: end.toISOString().slice(0, 10) };
}

export function suggestedDates(semester: Semester, today = new Date()): BatchDates {
  const term = semesterDates(semester);
  let month = currentMonth(today);
  if (month.end_date < term.start_date || month.start_date > term.end_date) {
    month = currentMonth(new Date(term.start_date + "T12:00:00"));
  }
  return {
    start_date: month.start_date < term.start_date ? term.start_date : month.start_date,
    end_date: month.end_date > term.end_date ? term.end_date : month.end_date
  };
}

export function outsideSemester(dates: BatchDates, semester: Semester): boolean {
  const term = semesterDates(semester);
  return Boolean(dates.start_date && dates.end_date &&
    (dates.start_date < term.start_date || dates.end_date > term.end_date));
}

export const dateFields = `
  <section id="batchDates" class="batchDates" aria-label="本次合表日期">
    <div><strong>本次合表日期</strong><small>全部成员共用，确认后按此范围导出。</small></div>
    <label>开始日期<input type="date" id="batchStart" required /></label>
    <label>结束日期<input type="date" id="batchEnd" required /></label>
    <button type="button" id="batchThisMonth">本月</button>
    <p id="batchCalendarHint" role="status" hidden></p>
  </section>`;
