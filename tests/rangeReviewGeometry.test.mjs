import assert from "node:assert/strict";
import test from "node:test";
import { printedWeekWarning } from "../src/rangeReviewGeometry.ts";

function sheet(titleLabels, rowText = "") {
  const label = (text, x, y) => ({text, x, y});
  return {
    width: 600, pageSize: {width:600},
    labels: [...titleLabels, label("周一",120,75), label("周二",220,75),
      label("周三",320,75), label(rowText,190,135)]
  };
}

test("printed week takes precedence over the week displayed by the UI", () => {
  const splitTitle = sheet([{text:"2 周课表", x:110, y:30}, {text:"第", x:90, y:31}]);
  assert.match(printedWeekWarning(splitTitle,"2026-03-02",1),/第 2 周/);
  assert.equal(printedWeekWarning(splitTitle,"2026-03-09",2),"");
  assert.match(printedWeekWarning(sheet([{text:"第二周课程表",x:110,y:30}]),"2026-03-02",1),/二/);
  assert.ok(printedWeekWarning(splitTitle,"2026-03-09"));
});

test("conflicting dates and titles spanning multiple weeks require an actual date", () => {
  const title = text=>sheet([{text,x:100,y:30}]);
  assert.ok(printedWeekWarning(title("2026年03月02日 至 3月11日"),"2026-03-02",1));
  assert.ok(printedWeekWarning(title("第2至3周课表"),"2026-03-02",1));
  assert.equal(printedWeekWarning(title("12/28 至 01/03"),"2026-12-28",18),"");
  assert.ok(printedWeekWarning(title("2025-12-28"),"2026-12-28",18));
});

test("printing dates and course recurrence text do not set the scope of the sheet", () => {
  const ordinary=sheet([{text:"打印日期：2026-05-15",x:140,y:29}],"第2周上课");
  assert.equal(printedWeekWarning(ordinary,"2026-03-02",1),"");
  const dated=sheet([{text:"第2周课表  生成日期：2026-05-15",x:140,y:29}]);
  assert.ok(printedWeekWarning(dated,"2026-03-02",1));
  assert.equal(printedWeekWarning(dated,"2026-03-09",2),"");
});
