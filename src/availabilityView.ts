type AvailabilityRow = Record<string, unknown>;
const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const escape = (value: unknown) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

export function availabilityWeeks(rows: AvailabilityRow[]): number[] {
  return [...new Set(rows.map(row => Number(row["周次"])).filter(week => Number.isInteger(week) && week > 0))].sort((a, b) => a - b);
}

export function departmentPicker(departments: string[], selected: string, id = "availabilityDepartment") {
  return `<label class="scopeField departmentSelect"><span>部门</span><select id="${escape(id)}" ${departments.length ? "" : "disabled"}>
    <option value="">全协会</option>${departments.map(name => `<option value="${escape(name)}"${name === selected ? " selected" : ""}>${escape(name)}</option>`).join("")}</select></label>`;
}

export function availabilityToolbar(
  rows: AvailabilityRow[], week: number, mode: "week" | "list", departments: string[] = [], selectedDepartment = ""
) {
  const dates = [...new Set(rows.filter(row => Number(row["周次"]) === week).map(row => String(row["日期"] || "")).filter(Boolean))].sort();
  const range = dates.length ? `${dates[0]} — ${dates[dates.length - 1]}` : "";
  const weeks = availabilityWeeks(rows);
  const position = weeks.indexOf(week);
  return `<div class="availabilityToolbar">${departmentPicker(departments, selectedDepartment)}
    <label class="scopeField weekSelect"><span>周次</span><select id="availabilityWeek" ${!weeks.length ? "disabled" : ""}>
    ${weeks.map(value => `<option value="${value}" ${value === week ? "selected" : ""}>第 ${value} 周</option>`).join("")}</select></label>
    <div class="weekStepper"><button type="button" data-week-step="-1" aria-label="上一周" ${position <= 0 ? "disabled" : ""}>‹</button><button type="button" data-week-step="1" aria-label="下一周" ${position < 0 || position >= weeks.length - 1 ? "disabled" : ""}>›</button></div>
    <span class="weekRange">${escape(range)}</span>
    <div class="viewToggle" role="group" aria-label="空课展示方式"><button data-availability-mode="week" aria-pressed="${mode === "week"}">周表</button><button data-availability-mode="list" aria-pressed="${mode === "list"}">明细</button></div></div>`;
}

// Only reorganize supplied rows. Missing cells never mean that members are free.
export function availabilityWeekGrid(rows: AvailabilityRow[], week: number, totalMembers: number, onlyIncludedDays = false) {
  const indexed = rows.map((row, index) => ({ row, index })).filter(({ row }) => Number(row["周次"]) === week);
  if (!indexed.length) return `<div class="emptyPanel"><b>没有可展示的空闲时段</b><span>请检查课表覆盖范围和待处理问题。</span></div>`;
  const included = new Set(indexed.map(({row}) => row["星期"]));
  const shownDays = onlyIncludedDays ? weekdays.filter(day => included.has(day))
    : weekdays.slice(0, included.has("周六") || included.has("周日") ? 7 : 5);
  const slots = [...new Map(indexed.map(({ row }) => [`${row["节次"]}|${row["时间"]}`, { period: String(row["节次"] ?? ""), time: String(row["时间"] ?? "") }])).values()];
  const clock = (time: string) => { const m = time.match(/(\d{1,2}):(\d{2})/); return m ? Number(m[1]) * 60 + Number(m[2]) : Infinity; };
  slots.sort((a, b) => clock(a.time) - clock(b.time) || a.period.localeCompare(b.period, "zh-CN", { numeric: true }));
  const cells = new Map<string, typeof indexed>();
  for (const item of indexed) {
    const key = `${item.row["星期"]}|${item.row["节次"]}|${item.row["时间"]}`;
    cells.set(key, [...(cells.get(key) || []), item]);
  }
  const header = shownDays.map(day => {
    const dates = [...new Set(indexed.filter(({ row }) => row["星期"] === day).map(({ row }) => String(row["日期"] || "")).filter(Boolean))];
    return `<th scope="col">${day}<span>${dates.length === 1 ? escape(dates[0].slice(5).replace("-", "/")) : ""}</span></th>`;
  }).join("");
  return `<div class="tableWrap weekGridWrap"><table class="availabilityGrid"><thead><tr><th scope="col">节次</th>${header}</tr></thead><tbody>${slots.map(slot => `<tr><th scope="row"><b>第 ${escape(slot.period)} 节</b><span>${escape(slot.time)}</span></th>${shownDays.map(day => {
    const matches = cells.get(`${day}|${slot.period}|${slot.time}`) || [];
    if (!matches.length) return `<td class="uncovered">未覆盖</td>`;
    if (matches.length !== 1) return `<td class="uncovered">记录不唯一<br />请查看明细</td>`;
    const { row, index } = matches[0];
    const count = row["空闲人数"] === "" || row["空闲人数"] == null ? NaN : Number(row["空闲人数"]);
    const valid = Number.isInteger(count) && count >= 0 && (totalMembers <= 0 || count <= totalMembers);
    const intensity = valid && count > 0 ? (count === totalMembers ? "full" : "some") : "none";
    const title = valid ? count ? `${count} 人空闲` : "无人空闲" : "人数待核对";
    const all = intensity === "full";
    return `<td class="availabilityCell ${intensity}"><button data-slot-index="${index}" aria-label="${escape(`${day} 第${slot.period}节，${all ? "全员空闲，" : ""}${title}，查看人员`)}"><strong>${title}</strong>${all ? '<small class="wholeDepartment">全员</small>' : ""}<span>${escape(count > 0 ? row["空闲人员"] : "")}</span></button></td>`;
  }).join("")}</tr>`).join("")}</tbody></table></div>`;
}

export function availabilitySlotDetail(row: AvailabilityRow, department = "") {
  return `<h2 id="slotDetailTitle">${escape(row["星期"])} · 第 ${escape(row["节次"])} 节</h2><p>${escape(department || "全协会")} · ${escape(row["日期"])}　${escape(row["时间"])}</p>
    <h3>空闲人员（${escape(row["空闲人数"])}）</h3><p class="slotNames">${escape(row["空闲人员"] || "无")}</p>
    <h3>有课人员（${escape(row["占用人数"])}）</h3><p class="slotNames">${escape(row["有课人员"] || "无")}</p>`;
}
