import { batchReviewRisks } from "./reviewRisks";
import type { ParseResult, ReviewPayload } from "./types";

const labels = { blocked: "需处理", attention: "有疑点", pending: "待核验", done: "已核验" };
const marks = { blocked: "!", attention: "!", pending: "○", done: "✓" };

function escape(value: string) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function semesterIssues(result: ParseResult) {
  const issues = result.issues.filter(issue => !issue.confirmed &&
    (issue.code === "WEEK_OUT_OF_SEMESTER" || issue.code === "PRE_TERM_DATE"));
  if (!issues.length) return "";
  const courses = new Map(result.courses.map(course => [course.block_id, course]));
  return issues.map(issue => {
    const rows = (result.range_evidence?.[issue.code] || []).flatMap(id => {
      const course = courses.get(id);
      if (!course) return [];
      const file = course.source_file || "";
      const when = [course.date, `第 ${course.week} 周`, course.weekday, `第 ${course.periods.join("、")} 节`].filter(Boolean).join(" · ");
      const page = course.page == null ? "" : ` · 第 ${course.page + 1} 页`;
      return [`<li><div><b>${escape(course.name)} · ${escape(course.course || "课程")}</b><span>${escape(when)}</span><small>${escape(file)}${page}</small></div>
        ${file ? `<button type="button" data-open-source="${escape(file)}" data-open-block="${escape(id)}">核对这门课</button>` : ""}</li>`];
    });
    const field = issue.code === "PRE_TERM_DATE" ? "semesterStart" : "teachingWeeks";
    const lastWeek = result.detected_max_week;
    const guidance = field === "teachingWeeks" && lastWeek > 0 && lastWeek <= 30
      ? `先对照校历上的第一周周一。若学期确实包含第 ${lastWeek} 周，按校历修改“教学周数”，再点“保存并重新生成”。日期和周数都正确时，点“核对这门课”看原页。`
      : "请对照校历核实第一周周一和教学周数。改动后点“保存并重新生成”。如果设置正确，点“核对这门课”查看原页日期。";
    return `<section class="semesterProblem"><h3>先核对学期日期</h3><p>${escape(issue.message)}</p>
      ${rows.length ? `<ul>${rows[0]}</ul>` : ""}
      ${rows.length > 1 ? `<details><summary>查看其余 ${rows.length - 1} 条课程记录</summary><ul>${rows.slice(1).join("")}</ul></details>` : ""}
      <p>${escape(guidance)}</p>
      <button type="button" data-semester-setting="${field}">前往学期设置</button></section>`;
  }).join("");
}

export type ReviewFilter = "all" | "attention" | "pending" | "done";

export function renderReviewOverview(result: ParseResult, review?: ReviewPayload | null, filter: ReviewFilter = "all") {
  const { files, pairs, singleton } = batchReviewRisks(result, review);
  const progress = review?.verification || result.verification;
  const notes = [];
  if (singleton) {
    notes.push('<p class="reviewAdvisory"><b>本批仅识别到 1 位成员</b><span>如要汇总多人空课，请检查是否漏选了其他成员的课表。</span></p>');
  }
  for (const { member, missing } of pairs) {
    const identity = [member.department, member.member, member.role].filter(Boolean).join(" · ");
    notes.push(`<p class="reviewPairRisk"><b>${escape(identity || "身份待确认")} · 缺${missing}有效课表</b><span>请检查${missing}原件是否已导入、能否识别，再重新生成。</span></p>`);
  }
  const filtered = {
    all: files,
    attention: files.filter(file => file.state === "blocked" || file.state === "attention"),
    pending: files.filter(file => file.count ? file.count.confirmed < file.count.total : file.state !== "done"),
    done: files.filter(file => file.state === "done")
  };
  const filters = ([["all", "全部原件"], ["attention", "有异常"], ["pending", "待核验"], ["done", "已核验"]] as const).map(([key, label]) =>
    `<button type="button" data-review-filter="${key}" aria-pressed="${filter === key}">${label} <span>${filtered[key].length}</span></button>`
  ).join("");
  const tiles = filtered[filter].map(({ source, memberName, count, state, reason, target }) => {
    const title = [memberName === "未识别" ? "" : memberName, source.kind ? source.kind + "课表" : ""].filter(Boolean).join(" · ");
    const name = source.file_name.split(/[\\/]/).pop() || "未命名文件";
    const meta = [reason, count ? `${count.confirmed}/${count.total} 项已核验` : ""].filter(Boolean).join(" · ");
    return `<button type="button" class="reviewSourceTile" data-state="${state}" ${target ? `data-open-source="${escape(target)}"` : 'disabled title="同名文件，请在原文核对中选择原件"'}>
      <span class="reviewTileState"><span aria-hidden="true">${marks[state]}</span> ${labels[state]}</span>
      <strong>${escape(title || name)}</strong>
      ${title ? `<small title="${escape(name)}">${escape(name)}</small>` : ""}
      ${meta ? `<span class="reviewTileMeta">${escape(meta)}</span>` : ""}
    </button>`;
  }).join("");
  const nextFile = files.find(file => file.target && file.count && file.count.confirmed < file.count.total);
  const nextAction = nextFile
    ? `<button type="button" class="primary batchNext" data-open-source="${escape(nextFile.target)}">${progress?.confirmed ? "继续核验" : "开始逐项核验"}</button>`
    : result.can_export ? '<button type="button" class="primary batchNext" data-tab="availability">查看空课结果</button>' : "";
  return `<section class="reviewOverview" aria-label="本批课表核验总览">
    <div class="reviewOverviewHead"><div><h2>本批课表</h2><span>${result.members.length} 位成员 · ${files.length} 份原件${progress ? ` · 已核验 ${progress.confirmed}/${progress.total} 项` : ""}</span></div>${nextAction}</div>
    ${semesterIssues(result)}
    ${notes.length ? `<div class="reviewOverviewNotes">${notes.join("")}</div>` : ""}
    ${files.length ? `<div class="reviewFilters" role="group" aria-label="按原件状态筛选">${filters}</div>` : ""}
    ${tiles ? `<div class="reviewSourceGrid">${tiles}</div>` : files.length ? '<p class="emptyNote">此筛选下没有课表。</p>' : '<p class="emptyNote">本批没有可打开的课表原件，请检查导入结果。</p>'}
  </section>`;
}
