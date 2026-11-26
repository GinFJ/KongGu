import type { CourseRow } from "./types";

export type Rect = {left: number; top: number; width: number; height: number};
export type PageLabel = {text: string; x: number; y: number};
export type OriginalFrame = {
  page: number;
  width: number;
  height: number;
  rotation: number;
  pageSize: {width: number; height: number};
  canvas: HTMLCanvasElement;
  labels: PageLabel[];
  sourceHash: string;
  marksVisible: boolean;
  rectFor: (row: CourseRow) => Rect | null;
};
export const dayMs = 86400000;
export const dayStamp = (date: string) => Date.parse(date + "T00:00:00Z");
export const isoDay = (stamp: number) => new Date(stamp).toISOString().slice(0, 10);
export function mondayOf(date: string) {
  const stamp = dayStamp(date);
  return isoDay(stamp - (new Date(stamp).getUTCDay() + 6) % 7 * dayMs);
}

/** Only the title and the weekday headings describe the whole sheet.
 * Recurrence text inside a course describes that course alone. */
export function printedWeekWarning(frame: OriginalFrame, monday: string, teachingWeek?: number): string {
  const dayHeaders = frame.labels.filter(label => /^(?:星期|周)[一二三四五六日天]$/.test(label.text));
  if (!dayHeaders.length) return "";
  const scale = frame.width / frame.pageSize.width;
  const headerY = Math.min(...dayHeaders.map(label => label.y));
  const lines: PageLabel[][] = [];
  for (const label of frame.labels.filter(label => label.y <= headerY + 12 * scale).sort((a,b)=>a.y-b.y)) {
    const last = lines[lines.length-1];
    if (last && label.y-last[0].y < 3 * scale) last.push(label);
    else lines.push([label]);
  }
  const days = Array.from({length:7}, (_,i)=>isoDay(dayStamp(monday)+i*dayMs));
  for (const labels of lines) {
    const text=labels.sort((a,b)=>a.x-b.x).map(label=>label.text).join("").normalize("NFKC").replace(/\s+/g,"");
    if (/(?:第|周次[:：]?)[\d一二三四五六七八九十]{1,3}(?:周)?[至到~～–—-](?:第)?[\d一二三四五六七八九十]{1,3}周/.test(text))
      return "原件表头包含多个教学周，请按原表选择实际日期。";
    const weeks = text.matchAll(/(?:第([\d一二三四五六七八九十]{1,3})(?:教学)?周|(?:教学周次|周次)[:：]?(\d{1,2})|(?:^|课表)(\d{1,2})周(?:课表|课程表|$))/g);
    for (const match of weeks) {
      const value = match[1] || match[2] || match[3];
      const digits = "零一二三四五六七八九";
      const [tens,ones] = value.split("十");
      const printed = /^\d+$/.test(value) ? Number(value) : ones===undefined ? digits.indexOf(tens)
        : (tens ? digits.indexOf(tens) : 1)*10 + (ones ? digits.indexOf(ones) : 0);
      if (printed !== teachingWeek) return `原件表头标注第 ${value} 周，请按原表选择实际日期。`;
    }
    const datedTitle = text.split(/(?:打印|生成|导出)(?:日期|时间)/,1)[0];
    const dates = datedTitle.matchAll(/(?<!\d)(?:(\d{4})[年./-])?(\d{1,2})[月./-](\d{1,2})(?:日)?(?!\d)/g);
    for (const match of dates) {
      const [,year,month,day] = match;
      if (Number(month)<1 || Number(month)>12 || Number(day)<1 || Number(day)>31) continue;
      const suffix = month.padStart(2,"0")+"-"+day.padStart(2,"0");
      if (!days.some(date => year ? date===year+"-"+suffix : date.endsWith("-"+suffix)))
        return "原件表头的日期与当前周不一致，请按原表选择实际日期。";
    }
  }
  return "";
}

// Invert the same rotation used to draw the PDF overlay. Store original page
// units so a manual mark still follows the paper after zooming or reopening.
export function originalBox(rect: Rect, frame: OriginalFrame): [number, number, number, number] {
  const points = [[rect.left, rect.top], [rect.left + rect.width, rect.top + rect.height]].map(([x, y]) => {
    const u = Math.max(0, Math.min(1, x / frame.width)), v = Math.max(0, Math.min(1, y / frame.height));
    switch (frame.rotation) {
      case 90: return [v, 1 - u];
      case 180: return [1 - u, 1 - v];
      case 270: return [1 - v, u];
      default: return [u, v];
    }
  });
  const xs = points.map(point => point[0] * frame.pageSize.width);
  const ys = points.map(point => point[1] * frame.pageSize.height);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// A native weekly grid is directly editable only if both physical cell borders
// and unique weekday and period headers identify the clicked cell. Printed
// multi-period cells, embedded dates, English date rows and scans use the
// explicit date/period chooser when their layout cannot establish this link.
export function locateChineseCell(frame: OriginalFrame, x: number, y: number) {
  if (frame.rotation !== 0 || !frame.labels.length) return null;
  const headers = frame.labels.filter(item => /^(星期|周)[一二三四五六日天]$/.test(item.text));
  if (headers.length < 3 || new Set(headers.map(h => h.text.slice(-1))).size !== headers.length) return null;
  if (Math.max(...headers.map(h => h.y)) - Math.min(...headers.map(h => h.y)) > 6) return null;
  if (frame.labels.some(item => /\d{1,2}[月\/.\-]\d{1,2}/.test(item.text) && Math.abs(item.y - headers[0].y) < 28)) return null;
  const ctx = frame.canvas.getContext("2d");
  if (!ctx) return null;
  const dx = frame.canvas.width / frame.width, dy = frame.canvas.height / frame.height;
  const cx = Math.round(x * dx), cy = Math.round(y * dy);
  const left = Math.max(0, cx - Math.ceil(180 * dx)), top = Math.max(0, cy - Math.ceil(120 * dy));
  const width = Math.min(frame.canvas.width - left, cx - left + Math.ceil(180 * dx));
  const height = Math.min(frame.canvas.height - top, cy - top + Math.ceil(120 * dy));
  if (width < 12 || height < 12) return null;
  const data = ctx.getImageData(left, top, width, height).data;
  const sx = cx-left, sy = cy-top;
  const vertical = (px: number) => isLine(px, sy, true);
  const horizontal = (py: number) => isLine(sx, py, false);
  function isLine(px: number, py: number, verticalAxis: boolean) {
    let count = 0;
    for (let offset = -8; offset <= 8; offset++) {
      const xx = px + (verticalAxis ? 0 : offset), yy = py + (verticalAxis ? offset : 0);
      if (xx < 0 || xx >= width || yy < 0 || yy >= height) return false;
      const i = (yy * width + xx) * 4;
      if (Math.max(data[i],data[i+1],data[i+2]) < 205) count++;
    }
    return count >= 16;
  }
  function edge(start: number, step: number, limit: number, line: (n:number)=>boolean) {
    for (let i=start; i>=0 && i<limit; i+=step) if (line(i)) return i;
    return -1;
  }
  const edges=[edge(sx,-1,width,vertical),edge(sx,1,width,vertical),edge(sy,-1,height,horizontal),edge(sy,1,height,horizontal)];
  if (edges.some(v => v < 0)) return null;
  const [l,r,t,b]=[(left+edges[0])/dx,(left+edges[1])/dx,(top+edges[2])/dy,(top+edges[3])/dy];
  if (r-l < 18 || b-t < 10) return null;
  const days = headers.filter(h=>h.x>l && h.x<r && h.y<t);
  const firstHeader = Math.min(...headers.map(h=>h.x));
  const rows = frame.labels.filter(a => /^(?:第)?(?:[1-9]|1[0-9]|20)(?:节)?$/.test(a.text)
    && a.x<Math.min(firstHeader,l) && a.y>t && a.y<b);
  if (days.length !== 1 || rows.length !== 1) return null;
  const day = days[0].text.slice(-1).replace("天","日");
  return { weekday: "一二三四五六日".indexOf(day), period: Number(rows[0].text.match(/\d+/)![0]),
    rect: {left:l,top:t,width:r-l,height:b-t} };
}

/** Locate an unboxed parsed record using *printed* weekday/period headers and borders. */
export function locateChineseSlot(frame: OriginalFrame, date:string, period:number): Rect|null {
  const index=(new Date(dayStamp(date)).getUTCDay()+6)%7;
  const weekday="一二三四五六日"[index];
  const headers=frame.labels.filter(label=>["周"+weekday,"星期"+weekday].includes(label.text.replace("天","日")));
  if(headers.length!==1)return null;
  const rows=frame.labels.filter(label=>label.x<headers[0].x && label.y>headers[0].y &&
    new RegExp("^(?:第)?0?"+period+"(?:节)?$").test(label.text));
  if(rows.length!==1)return null;
  const found=locateChineseCell(frame,headers[0].x,rows[0].y);
  return found?.period===period && found.weekday===index ? found.rect : null;
}
