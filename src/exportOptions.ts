import type { BatchDates } from "./batchDates";
type ExportSelection = { mode: "classic" | "qinghe"; week?: number };

export const exportDialog = `
  <dialog id="exportOptions" class="workbookDialog" aria-labelledby="exportOptionsTitle">
    <form method="dialog">
      <header><h2 id="exportOptionsTitle">导出空课表</h2><p id="exportScope"></p></header>
      <fieldset><legend>选择模板</legend>
        <label><input type="radio" name="template" value="classic" checked />
          <span><strong>完整明细工作簿</strong><small>周表、逐节名单和课程明细，供内部查阅。</small></span></label>
        <label><input type="radio" name="template" value="qinghe" />
          <span><strong id="qingheTitle">青禾工作日周表</strong><small id="qingheHint">每周一张，按周一至周五和大节排版，方便排班或打印。</small></span></label>
      </fieldset>
      <label class="exportWeekLine" for="exportWeeks">导出周次<select id="exportWeeks" name="weeks">
        <option value="all">全部覆盖周</option><option value="current">当前周</option>
      </select></label>
      <footer><button value="cancel" type="submit">取消</button><button value="save" type="submit" id="saveWorkbook" class="primary">选择保存位置</button></footer>
    </form>
  </dialog>`;

export function chooseExportOptions(department: string, memberCount: number, week: number, range?: BatchDates): Promise<ExportSelection | null> {
  const dialog = document.querySelector<HTMLDialogElement>("#exportOptions")!;
  if (dialog.open) return Promise.resolve(null);
  dialog.querySelector<HTMLElement>("#exportScope")!.textContent = `${department || "全协会"} · ${memberCount} 位成员` + (range ? ` · ${range.start_date} 至 ${range.end_date}` : "");
  const weeks = dialog.querySelector<HTMLSelectElement>("#exportWeeks")!;
  dialog.querySelector<HTMLElement>("#qingheTitle")!.textContent = range ? "青禾排班周表" : "青禾工作日周表";
  dialog.querySelector<HTMLElement>("#qingheHint")!.textContent = range ? "按大节排版，每周一张，包含所选范围内的周末。" : "每周一张，按周一至周五和大节排版，方便排班或打印。";
  dialog.querySelector<HTMLElement>(".exportWeekLine")!.hidden = Boolean(range);
  weeks.disabled = Boolean(range);
  weeks.options[1].textContent = week ? `当前周（第 ${week} 周）` : "当前周";
  weeks.options[1].disabled = !week;
  if (!week) weeks.value = "all";
  dialog.returnValue = "";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "save") return resolve(null);
      const mode = dialog.querySelector<HTMLInputElement>('input[name="template"]:checked')?.value === "qinghe" ? "qinghe" : "classic";
      resolve({ mode, week: !range && weeks.value === "current" ? week : undefined });
    }, { once: true });
    dialog.showModal();
  });
}

export function workbookName(department: string, choice: ExportSelection, range?: BatchDates) {
  const scope = (department || "全部部门").replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").slice(0, 80);
  const kind = choice.mode === "qinghe" ? (range ? "青禾排班空课表" : "青禾工作日空课表") : "空课明细";
  const dates = range ? `${range.start_date}至${range.end_date}` : choice.week ? `第${choice.week}周` : "全部覆盖周";
  return `${scope}_${kind}_${dates}.xlsx`;
}
