import type { CourseRow, ParseIssue, ReviewPayload } from "./types";
import type { OriginalHooks, ReviewActions, ReviewController } from "./reviewView";
import { dayMs, dayStamp, isoDay, locateChineseCell, locateChineseSlot, mondayOf, originalBox, printedWeekWarning, type OriginalFrame, type Rect } from "./rangeReviewGeometry";
import { issuesForSource } from "./reviewRisks";
import { periodTimes } from "./calendarCourseEvents";
import type { DateFlag } from "./rangeReviewTypes";
import "./rangeReview.css";

type Reader = (root: HTMLElement, review: ReviewPayload, actions: ReviewActions, hooks: OriginalHooks) => Promise<ReviewController>;
type Slot = {date: string; period: number; marked: boolean};
type Placement = { bbox: [number,number,number,number]; page_width:number; page_height:number };
type Target = {rect: Rect; slots: Slot[]};
type Source = ReviewPayload["sources"][number];

function esc(value: unknown) {
  return String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
    .replaceAll('"',"&quot;").replaceAll("'","&#39;");
}

function daysLabel(start: string, end: string) { return start === end ? start : start + " 至 " + end; }
function periodList(block: CourseRow) { return block.periods?.length ? block.periods : block.period ? [block.period] : []; }

export async function mountRangeReview(root: HTMLElement, initial: ReviewPayload, actions: ReviewActions, mountReader: Reader): Promise<ReviewController> {
  let review = initial;
  const scope = () => review.date_review!;
  const members = () => scope().members;
  let personKey = (members().find(member=>!member.confirmed) || members()[0])?.member_key || "";
  const member = () => members().find(person=>person.member_key===personKey);
  const sources = () => review.sources.filter(s=>member()?.source_hashes.includes(s.content_hash));
  let source = sources()[0];
  let week = mondayOf(scope().start_date);
  let reader: ReviewController | undefined;
  let frame: OriginalFrame | null = null;
  let overlay: HTMLElement | null = null;
  let targets: Target[] = [];
  let geometryLabels: OriginalFrame["labels"]|undefined;
  const nativeSlots = new Map<string,Rect|null>();
  let pendingPlace: Placement | undefined;
  let pickedPoint: {hash:string; page:number; free:boolean} | undefined;
  let issueFocus = "";
  let reporting=false;
  let flagFocus: DateFlag | null=null;
  let saving = false, destroyed = false;
  let ackPerson = "";
  let useManualSource = false;
  let logKey = "";
  const localLogs: Array<{time:string; title:string; detail:string}> = [];
  const currentStart = () => week > scope().start_date ? week : scope().start_date;
  const currentEnd = () => {
    const last = isoDay(dayStamp(week) + 6 * dayMs);
    return last < scope().end_date ? last : scope().end_date;
  };
  const weekFor = (date: string) => date >= currentStart() && date <= currentEnd();
  root.innerHTML = `<div class="rangeReview">
    <section class="rrDocument" aria-label="原课表详情">
      <div class="rrTop"><div><small>当前成员</small><select id="rrMember" aria-label="当前成员"></select></div><span id="rrMemberPosition"></span></div>
      <div class="rrNav"><div class="rrSources" id="rrSources"></div><div class="rrWeeks">
        <button id="rrPrev" aria-label="上一周">‹</button><span id="rrWeek"></span><button id="rrNext" aria-label="下一周">›</button>
      </div></div>
      <div class="rrOriginalArea"><div id="rrReader"></div><div id="rrDocError" hidden role="status"><strong></strong><div>
        <button type="button" id="rrRetry">重新读取原件</button>
        ${actions.reconnect?'<button type="button" id="rrReconnect">重新关联原件</button>':""}</div></div></div>
      <div class="rrFoot"><span>点色块取消，点漏标位置补上。</span><button id="rrExact" type="button">按日期节次调整</button></div>
    </section>
    <aside class="rrRail">
      <section class="rrPanel rrDecisions"><div class="rrDecisionTop"><header><h3>成员核对</h3><span id="rrCount"></span></header>
        <progress id="rrProgress" value="0" max="1" aria-label="成员确认进度"></progress>
        <button class="primary" id="rrConfirm" type="button">确认，下一人</button>
      </div><div class="rrIssuesScroll" tabindex="0" aria-label="本成员课表问题及核实操作">
        <div id="rrMissing"></div><p id="rrMessage" role="status"></p><button id="rrReload" type="button" data-reload-review hidden>刷新核对记录</button>
        <header class="rrIssuesHead"><h3>课表问题</h3><span id="rrIssueCount"></span></header>
        <div id="rrIssues"></div>
        <div id="rrFlags"></div><button type="button" id="rrReport" class="rrSecondary" aria-pressed="false">在原表上标记问题</button>
        <details id="rrUnlocated" hidden><summary></summary><ul></ul></details>
        <details id="rrPick" class="rrPick"><summary>调整日期与节次</summary>
          <p id="rrPickHint"></p><div id="rrChoices"></div>
          <div class="rrFields"><label>上课日期<input type="date" id="rrDate" required></label><label>节次<select id="rrPeriod"></select></label></div>
          <label class="rrManual"><input type="checkbox" id="rrManual">其他途径核实，记为人工来源</label>
          <button id="rrApply" type="button"></button>
        </details>
        <button class="rrSecondary" id="rrAdvanced" type="button" aria-expanded="false">文件检查与高级修正</button></div>
      </section>
      <section class="rrPanel rrJournal"><header><h3>操作日志</h3><span id="rrLogCount"></span></header>
        <ol id="rrLog" tabindex="0" aria-label="本批操作日志"></ol><button id="rrLatest" hidden>回到最新</button>
      </section>
    </aside></div>`;
  const el = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector<T>("#"+id)!;
  const pick = el<HTMLDetailsElement>("rrPick");
  const day = el<HTMLInputElement>("rrDate"), period = el<HTMLSelectElement>("rrPeriod");
  const confirm = el<HTMLButtonElement>("rrConfirm");
  const log = el<HTMLOListElement>("rrLog");
  const message = (text: string) => {el("rrMessage").textContent=text;};
  day.min = scope().start_date; day.max = scope().end_date; day.value = currentStart();
  period.innerHTML = Object.keys(periodTimes).map(Number).sort((a,b)=>periodTimes[a][0].localeCompare(periodTimes[b][0]))
    .map(p=>`<option value="${p}">第 ${p} 节</option>`).join("");
  let sourceIssues: Array<{issue: ParseIssue; source?:Source}> = [];
  el("rrRetry").onclick=()=>{if(source) void chooseSource(source);};
  root.querySelector<HTMLButtonElement>("#rrReconnect")?.addEventListener("click",()=>void actions.reconnect?.().catch(err=>message(String(err))));
  function showSourceFailure(reason:string){
    if(destroyed)return;
    const panel=el("rrDocError");
    panel.hidden=false;
    panel.querySelector("strong")!.textContent=reason;
  }

  function addLocal(title:string, detail:string) {
    localLogs.push({time:new Date().toISOString(),title,detail});
    renderLogs();
  }
  function renderLogs() {
    const key = JSON.stringify([scope().log,localLogs.length]);
    if (key===logKey) return;
    logKey=key;
    const follow=log.scrollHeight - log.scrollTop - log.clientHeight < 6;
    const previousTop=log.scrollTop;
    const rows=scope().log.map(event=>{
      const name=members().find(m=>m.member_key===event.member_key)?.name || event.member_key;
      const file=review.sources.find(s=>s.content_hash===event.source_hash);
      return {time:event.created_at,title:event.action==="member"?"确认成员课表":event.action==="flag"?(event.open?"标记待核实问题":"人工问题已核实"):event.marked?"补上有课标记":"取消有课标记",
        detail:event.action==="member"?name+" · "+daysLabel(scope().start_date,scope().end_date):
          [name,file?.file_name || "人工核实",event.date,"第 "+event.period+" 节"].join(" · ")};
    }).concat(localLogs).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
    el("rrLogCount").textContent=rows.length+" 条";
    log.innerHTML=rows.map(row=>`<li><time>${esc(new Date(row.time).toLocaleTimeString("zh-CN",{hour12:false}))}</time><b>${esc(row.title)}</b><small>${esc(row.detail)}</small></li>`).join("") || "<li>本次核对暂无操作记录。</li>";
    log.scrollTop=follow?log.scrollHeight:previousTop;
    el("rrLatest").hidden=log.scrollHeight-log.scrollTop-log.clientHeight<6;
  }
  log.onscroll=()=>{el("rrLatest").hidden=log.scrollHeight-log.scrollTop-log.clientHeight<6;};
  el("rrLatest").onclick=()=>{log.scrollTop=log.scrollHeight;};

  function collectIssues() {
    const found=new Map<string,{issue:ParseIssue;source?:Source}>();
    for(const s of sources()) for(const issue of issuesForSource(review,s)) {
      if(!issue.confirmed && issue.severity!=="info") found.set(issue.issue_id,{issue,source:s});
    }
    for(const issue of review.issues) if(!issue.source_hash && !issue.source_file && !issue.block_id && issue.blocks_export && !issue.confirmed) {
      found.set(issue.issue_id,{issue});
    }
    return [...found.values()];
  }
  function renderRail() {
    if(destroyed) return;
    const m=member(), done=members().filter(person=>person.confirmed).length;
    el("rrCount").textContent=`${done}/${members().length} 人已确认`;
    const progress=el<HTMLProgressElement>("rrProgress");progress.max=members().length||1;progress.value=done;
    const missing=m?.missing_dates ?? [];
    el("rrMissing").innerHTML=missing.length && !m?.confirmed ? `<details class="rrGaps"><summary>${missing.length} 天缺少完整课表</summary><p>${missing.map(esc).join("、")}</p></details>
      <label class="rrAck"><input type="checkbox" id="rrAck" ${ackPerson===personKey?"checked":""}>已核实这些日期的有课或无课情况</label>`:"";
    const ack=root.querySelector<HTMLInputElement>("#rrAck");
    if(ack) ack.onchange=()=>{ackPerson=ack.checked?personKey:"";renderRail();};
    const flags=(scope().flags||[]).filter(f=>f.member_key===personKey);
    confirm.disabled=!reader || saving || !m || (!m.confirmed && (!frame || frame.sourceHash!==source?.content_hash || (missing.length>0 && ackPerson!==personKey) || flags.length>0));
    const completedAction=actions.result()?.can_export ? "查看空课汇总" : "查看待处理问题";
    confirm.textContent=saving?"正在保存…":m?.confirmed?(done===members().length?completedAction:"下一位待确认成员"):done===members().length-1?"确认，完成核对":"确认，下一人";
    sourceIssues=collectIssues();
    el("rrIssueCount").textContent=sourceIssues.length+flags.length+" 项";
    const issuesEl=el("rrIssues");
    const html=sourceIssues.map(({issue,source:s},index)=>{
      const row=issue.block_id?review.blocks.find(b=>b.block_id===issue.block_id && b.member_key===personKey):undefined;
      const periods=row?periodList(row):[];
      const inScope=row?.date && row.date>=scope().start_date && row.date<=scope().end_date;
      return `<article><strong>${esc(issue.message)}</strong>
       <small>${esc(s?.file_name || "本批资料")}</small>${issue.suggestion?`<small>${esc(issue.suggestion)}</small>`:""}
       <div>${s?`<button data-rr-locate="${index}">${issue.block_id?"定位课程":"查看原件"}</button>`:""}
       ${s && inScope && periods.length && issue.severity==="warning"?`<button data-rr-fix="${index}">${periods.length===1?"取消此节误标":"按节调整标记"}</button>`:""}
       ${issue.severity==="warning"&&issue.blocks_export?`<button data-rr-ack="${index}">已核实原判</button>`:""}</div></article>`;
    }).join("") || (flags.length?"":"<p>当前成员暂无待处理问题。</p>");
    if(issuesEl.dataset.view!==html){issuesEl.innerHTML=html;issuesEl.dataset.view=html;}
    const flagsEl=el("rrFlags");
    const flagRows=flags.map((flag,i)=>{
      const file=sources().find(s=>s.content_hash===flag.source_hash);
      return `<article><strong>人工标记待核实</strong><small>${esc(file?.file_name)} · ${esc(flag.date)} 第 ${flag.period} 节</small>
      <div><button data-flag-locate="${i}">定位原格</button><button data-flag-fix="${i}">${markedAt(flag.date,flag.period,flag.source_hash)?"取消此节标记":"补上此节标记"}</button>
      <button data-flag-done="${i}">已核实当前标记</button></div></article>`;
    }).join("");
    if(flagsEl.dataset.view!==flagRows){flagsEl.innerHTML=flagRows;flagsEl.dataset.view=flagRows;}
    const report=el<HTMLButtonElement>("rrReport");
    report.disabled=!reader || saving || !frame?.marksVisible;report.setAttribute("aria-pressed",String(reporting));
    report.textContent=reporting?"取消标记问题":"在原表上标记问题";
    const elsewhere=review.blocks.filter(b=>b.member_key===personKey && b.source_hash===source?.content_hash
      && b.date && weekFor(b.date.slice(0,10)) && (b.page==null || !b.bbox));
    const more=el<HTMLDetailsElement>("rrUnlocated");
    more.hidden=!elsewhere.length;
    if(elsewhere.length){
      more.querySelector("summary")!.textContent=`${elsewhere.length} 条课程缺少原页定位`;
      more.querySelector("ul")!.innerHTML=elsewhere.flatMap(b=>periodList(b).map(p=>`<li><button type="button" data-date="${esc(b.date?.slice(0,10))}" data-period="${p}">${esc(b.date?.slice(0,10))} · 第 ${p} 节 · ${esc(b.course||"有课")}</button></li>`)).join("");
    }
    renderLogs(); renderApply();
  }
  function renderNav() {
    const index=members().findIndex(m=>m.member_key===personKey);
    el("rrMemberPosition").textContent=index>=0?`第 ${index+1} 位 / 共 ${members().length} 位`:"";
    el("rrMember").innerHTML=members().map(m=>`<option value="${esc(m.member_key)}"${m.member_key===personKey?" selected":""}>${esc([m.name,m.department,m.role].filter(Boolean).join(" · "))}${m.confirmed?"（已确认）":""}</option>`).join("");
    el<HTMLSelectElement>("rrMember").disabled=saving || !reader;
    el("rrSources").innerHTML=sources().map((s,i)=>`<button type="button" ${saving||!reader?"disabled":""} title="${esc(s.file_name)}" data-rr-source="${i}" aria-pressed="${s.content_hash===source?.content_hash && s.file_name===source?.file_name}">${esc(s.kind)}原表${sources().filter(x=>x.kind===s.kind).length>1?" · "+esc(s.file_name):""}</button>`).join("");
    el("rrWeek").textContent=currentStart().slice(5)+" 至 "+currentEnd().slice(5);
    el<HTMLButtonElement>("rrPrev").disabled=week<=mondayOf(scope().start_date) || saving || !reader;
    el<HTMLButtonElement>("rrNext").disabled=week>=mondayOf(scope().end_date) || saving || !reader;
  }
  function markedAt(date:string,p:number,hash=source?.content_hash) {
    return review.blocks.some(b=>b.member_key===personKey && b.source_hash===hash && b.date?.slice(0,10)===date && periodList(b).includes(p));
  }
  function renderApply(){
    const p=Number(period.value), date=day.value;
    el<HTMLButtonElement>("rrApply").disabled=saving || !member() || (!useManualSource && (!frame || !source || frame.sourceHash!==source.content_hash)) ||
      !date || date<scope().start_date || date>scope().end_date || !p;
    const pickedHash=useManualSource?"manual":source?.content_hash;
    el("rrApply").textContent=reporting?"标记这节待核实":markedAt(date,p,pickedHash)?"取消这节有课标记":"补上这节有课标记";
    el<HTMLInputElement>("rrManual").disabled=reporting;
  }
  function closePick() {
    pick.open=false; pendingPlace=undefined; pickedPoint=undefined;el("rrChoices").replaceChildren();el("rrPickHint").textContent="";
    useManualSource=false;el<HTMLInputElement>("rrManual").checked=false;
  }
  function pickDate(slots: Slot[], rect?:Rect, unresolved?:{reason:string; period?:number}) {
    if(saving) return;
    useManualSource=false; el<HTMLInputElement>("rrManual").checked=false;
    pendingPlace=rect&&frame?{bbox:originalBox(rect,frame),page_width:frame.pageSize.width,page_height:frame.pageSize.height}:undefined;
    pickedPoint=rect && frame?{hash:frame.sourceHash,page:frame.page,free:slots.length===0}:undefined;
    day.value=unresolved?"":slots[0]?.date || currentStart();
    period.value=String(unresolved?.period || slots[0]?.period || 1);
    el("rrChoices").innerHTML=slots.map((s,i)=>`<button type="button" data-rr-choice="${i}">${esc(s.date)} · 第 ${s.period} 节</button>`).join("");
    for(const btn of el("rrChoices").querySelectorAll<HTMLButtonElement>("button")) btn.onclick=()=>{
      const s=slots[Number(btn.dataset.rrChoice)];day.value=s.date;period.value=String(s.period);renderApply();
    };
    el("rrPickHint").textContent=unresolved?.reason || (slots.length>1?"此框对应多个时段，请选中要改的那一节。":rect?"已选中原件位置。请核对日期和节次。":"选择当天的节次，可补标或取消标记。");
    pick.open=true;renderApply();day.focus();
  }
  day.onchange=period.onchange=()=>{if(!pickedPoint?.free)pendingPlace=undefined;renderApply();};
  el<HTMLInputElement>("rrManual").onchange=()=>{
    useManualSource=el<HTMLInputElement>("rrManual").checked;pendingPlace=undefined;renderApply();
  };
  el("rrExact").onclick=()=>pickDate([]);
  el("rrUnlocated").onclick=event=>{
    const b=(event.target as HTMLElement).closest<HTMLElement>("button[data-period]");
    if(b)pickDate([{date:b.dataset.date!,period:Number(b.dataset.period),marked:true}]);
  };
  el("rrApply").onclick=()=>{
    if(useManualSource)void setSlot(day.value,Number(period.value),undefined,"manual");
    else void touch(day.value,Number(period.value),pendingPlace);
  };
  el("rrReport").onclick=()=>{
    if(!reader || saving || !frame?.marksVisible)return;
    reporting=!reporting;closePick();message(reporting?"请在当前原表上点选待核实的一节。":"");
    renderRail();
  };

  function paint(element:HTMLElement,f:OriginalFrame|null) {
    const readyBefore=Boolean(frame),marksBefore=frame?.marksVisible;
    frame=f;overlay=element;targets=[];element.replaceChildren();element.onclick=null;
    if(f)el("rrDocError").hidden=true;
    if(f && pickedPoint && (f.sourceHash!==pickedPoint.hash || f.page!==pickedPoint.page))closePick();
    if(f && !f.marksVisible && reporting){reporting=false;closePick();message("");}
    if(readyBefore!==Boolean(f) || marksBefore!==f?.marksVisible)renderRail();
    renderApply();
    if(!f || !f.marksVisible || !source || f.sourceHash!==source.content_hash || destroyed)return;
    const printedWarning=source.kind.includes("中")
      ? printedWeekWarning(f,week,scope().calendar?.find(day=>weekFor(day.date))?.week) : "";
    if(geometryLabels!==f.labels){nativeSlots.clear();geometryLabels=f.labels;}
    const groups=new Map<string,{rect:Rect;slots:Map<string,Slot>}>();
    function addRect(rect:Rect,date:string,p:number,marked:boolean){
      const key=[rect.left,rect.top,rect.width,rect.height].map(Math.round).join("/");
      let group=groups.get(key);if(!group){group={rect,slots:new Map()};groups.set(key,group);}
      group.slots.set(date+"/"+p,{date,period:p,marked});
    }
    const add=(b:CourseRow,marked:boolean)=>{
      const date=b.date?.slice(0,10);
      if(b.member_key!==personKey || b.source_hash!==source.content_hash || b.page!==f.page || !date || !weekFor(date))return;
      const measured=b.bbox?f.rectFor(b):null;
      for(const p of periodList(b)){
        let rect=measured;
        if(!rect && f.labels.length && source.kind.includes("中")){
          const key=date+"/"+p;
          if(!nativeSlots.has(key))nativeSlots.set(key,locateChineseSlot(f,date,p));
          rect=nativeSlots.get(key)||null;
        }
        if(rect && rect.width>=1 && rect.height>=1)addRect(rect,date,p,marked);
      }
    };
    for(const b of review.removed_blocks || [])add(b,false);
    for(const b of scope().removed_positions || [])add(b,false);
    for(const b of review.blocks)add(b,true);
    const flags=(scope().flags||[]).filter(flag=>flag.member_key===personKey && flag.source_hash===source?.content_hash &&
      flag.page===f.page && weekFor(flag.date));
    for(const flag of flags){
      if(!flag.bbox)continue;
      const rect=f.rectFor({...flag,block_id:"",name:"",week:0,weekday:"",periods:[flag.period]});
      if(rect)addRect(rect,flag.date,flag.period,markedAt(flag.date,flag.period,flag.source_hash));
    }
    const issueBlock=issueFocus?review.blocks.find(b=>b.block_id===issueFocus):undefined;
    for(const group of groups.values()){
      const slots=[...group.slots.values()].sort((a,b)=>a.date.localeCompare(b.date)||a.period-b.period);
      if(!slots.length)continue;
      const i=targets.push({rect:group.rect,slots})-1;
      const btn=document.createElement("button");
      btn.type="button";btn.className="rrMark";btn.dataset.target=String(i);
      btn.setAttribute("aria-pressed",slots.some(s=>s.marked)?"true":"false");
      btn.classList.toggle("mixed",slots.some(s=>s.marked)&&slots.some(s=>!s.marked));
      const label=slots.map(s=>s.date+" 第"+s.period+"节 "+(s.marked?"有课":"已取消")).join("、");
      btn.title=label;btn.setAttribute("aria-label",label);btn.disabled=saving;
      if(issueBlock && slots.some(s=>s.date===issueBlock.date?.slice(0,10) && periodList(issueBlock).includes(s.period)))btn.classList.add("located");
      for(const flag of flags)if(slots.some(s=>s.date===flag.date&&s.period===flag.period)){
        btn.classList.add("flagged");
        if(flagFocus?.date===flag.date && flagFocus?.period===flag.period && flagFocus?.source_hash===flag.source_hash)btn.classList.add("located");
      }
      Object.assign(btn.style,{left:group.rect.left+"px",top:group.rect.top+"px",width:group.rect.width+"px",height:group.rect.height+"px"});
      element.append(btn);
    }
    element.onclick=(event)=>{
      if(saving || frame!==f || destroyed)return;
      const clicked=(event.target as HTMLElement).closest<HTMLButtonElement>("button[data-target]");
      if(clicked){
        const target=targets[Number(clicked.dataset.target)];
        if(printedWarning){pickDate(target.slots,target.rect,{reason:printedWarning});return;}
        if(target.slots.length===1){
          const slot=target.slots[0];void touch(slot.date,slot.period,{bbox:originalBox(target.rect,f),page_width:f.pageSize.width,page_height:f.pageSize.height});
        }else pickDate(target.slots,target.rect);
        return;
      }
      if(event.target!==element)return;
      const box=element.getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top;
      const cell=source.kind.includes("中")?locateChineseCell(f,x,y):null;
      if(cell) {
        if(printedWarning){pickDate([],cell.rect,{reason:printedWarning,period:cell.period});return;}
        const date=isoDay(dayStamp(week)+cell.weekday*dayMs);
        if(!weekFor(date)){message("此格在本次合表范围外。");return;}
        if(!reporting && markedAt(date,cell.period)) {
          pickDate([{date,period:cell.period,marked:true}],cell.rect);
          el("rrPickHint").textContent="这一节已有课程记录，请核实是否要取消。";
          return;
        }
        void touch(date,cell.period,{bbox:originalBox(cell.rect,f),page_width:f.pageSize.width,page_height:f.pageSize.height});return;
      }
      pickDate([],{left:Math.max(0,x-12),top:Math.max(0,y-8),width:24,height:16},
        {reason:printedWarning || "已选中原件位置，请按原表选择实际日期和节次。"});
    };
  }

  function repaint(){if(overlay)paint(overlay,frame);}
  async function save(command:"review.slot"|"review.member"|"review.flag",fields:Record<string,unknown>) {
    if(saving || !actions.dateCommand || destroyed) return false;
    saving=true;message("");renderRail();renderNav();
    try{
      const updated=await actions.dateCommand(command,{job_id:review.job_id,revision:review.revision,
        operation_id:crypto.randomUUID(),member_key:personKey,...fields});
      if(destroyed)return false;
      ackPerson="";closePick();await update(updated);
      return true;
    }catch(error){if(!destroyed){message(String(error instanceof Error?error.message:error));el("rrReload").hidden=false;}return false;}
    finally{saving=false;if(!destroyed){renderRail();renderNav();repaint();}}
  }
  async function setSlot(date:string,p:number,place?:Placement,hash=source?.content_hash) {
    if(!member() || !date || date<scope().start_date || date>scope().end_date)return;
    if(!hash || (hash!=="manual" && (!frame || frame.sourceHash!==hash)))return;
    return await save("review.slot",{source_hash:hash,date,period:p,marked:!markedAt(date,p,hash),
      page:hash==="manual"?null:frame!.page,...(hash==="manual"?{}:place)});
  }
  async function touch(date:string,p:number,place?:Placement) {
    if(!reporting)return setSlot(date,p,place);
    if(!reader || !frame || !source || frame.sourceHash!==source.content_hash)return;
    const existing=(scope().flags||[]).find(f=>f.member_key===personKey && f.source_hash===source.content_hash && f.date===date && f.period===p);
    reporting=false;
    if(existing){closePick();renderRail();message("这节已在右侧待核实问题中。");return;}
    return save("review.flag",{source_hash:source.content_hash,date,period:p,page:frame.page,open:true,...place});
  }
  let sourceSelection=0;
  async function chooseSource(next:Source,blockId?:string,pageIndex?:number) {
    const version=++sourceSelection;
    const show=root.querySelector<HTMLInputElement>("#showMarks");
    if(show && (blockId || flagFocus))show.checked=true;
    source=next;frame=null;el("rrDocError").hidden=true;closePick(); renderNav();renderRail();
    if(reader)await reader.selectSource(next.source_path||next.file_name,blockId,pageIndex);
    if(!destroyed && version===sourceSelection)repaint();
  }
  async function choosePerson(key:string) {
    if(!reader || saving || !members().some(m=>m.member_key===key))return;
    personKey=key;ackPerson="";issueFocus="";flagFocus=null;reporting=false;week=mondayOf(scope().start_date);message("");
    const next=sources()[0];
    if(next)await chooseSource(next);
    else {source=next;closePick();renderNav();renderRail();repaint();}
  }
  async function selectSource(path:string,blockId?:string,pageIndex?:number) {
    const next=review.sources.find(s=>s.source_path===path) || review.sources.find(s=>s.file_name===path);
    if(!next || saving)return;
    const m=members().find(m=>m.source_hashes.includes(next.content_hash));
    if(!m){message("该原件尚未关联到本次成员，请返回资料页检查。");return;}
    personKey=m.member_key;ackPerson="";issueFocus=blockId||"";reporting=false;flagFocus=null;
    const block=blockId?review.blocks.find(b=>b.block_id===blockId):undefined;
    if(block?.date && block.date>=scope().start_date && block.date<=scope().end_date) week=mondayOf(block.date.slice(0,10));
    await chooseSource(next,blockId,pageIndex);
  }
  function changeWeek(offset:number){
    if(saving)return;
    week=isoDay(dayStamp(week)+offset*dayMs);closePick();issueFocus="";reporting=false;flagFocus=null;renderNav();renderRail();repaint();
    addLocal("翻看教学周",daysLabel(currentStart(),currentEnd())+" · "+(member()?.name||""));
  }
  el("rrPrev").onclick=()=>changeWeek(-7);el("rrNext").onclick=()=>changeWeek(7);
  el<HTMLSelectElement>("rrMember").onchange=e=>void choosePerson((e.target as HTMLSelectElement).value);
  el("rrSources").onclick=e=>{
    const btn=(e.target as HTMLElement).closest<HTMLElement>("[data-rr-source]");
    if(!btn || saving || !reader)return;
    const next=sources()[Number(btn.dataset.rrSource)];
    if(next){issueFocus="";flagFocus=null;reporting=false;void chooseSource(next);addLocal("切换原表",[member()?.name,next.file_name].join(" · "));}
  };
  el("rrFlags").onclick=async e=>{
    const btn=(e.target as HTMLElement).closest<HTMLButtonElement>("button");
    if(!btn || saving || !reader)return;
    const index=Number(btn.dataset.flagLocate??btn.dataset.flagFix??btn.dataset.flagDone);
    const flag=(scope().flags||[]).filter(f=>f.member_key===personKey)[index];
    const original=sources().find(s=>s.content_hash===flag?.source_hash);
    if(!flag || !original)return;
    const close=()=>save("review.flag",{source_hash:flag.source_hash,date:flag.date,period:flag.period,page:flag.page,open:false});
    if(btn.dataset.flagDone!=null){await close();return;}
    reporting=false;issueFocus="";flagFocus=flag;week=mondayOf(flag.date);
    await chooseSource(original,undefined,flag.page);
    if(destroyed || personKey!==flag.member_key || frame?.sourceHash!==flag.source_hash || frame?.page!==flag.page)return;
    if(btn.dataset.flagLocate!=null){
      overlay?.querySelector(".located")?.scrollIntoView({block:"nearest",inline:"nearest"});
      addLocal("定位人工问题",daysLabel(flag.date,flag.date)+" 第 "+flag.period+" 节 · "+original.file_name);
      return;
    }
    const place=flag.bbox&&flag.page_width&&flag.page_height?{bbox:flag.bbox,page_width:flag.page_width,page_height:flag.page_height}:undefined;
    if(await setSlot(flag.date,flag.period,place))await close();
  };
  el("rrIssues").onclick=async event=>{
    if(saving)return;
    const btn=(event.target as HTMLElement).closest<HTMLElement>("button");if(!btn)return;
    const i=Number(btn.dataset.rrLocate ?? btn.dataset.rrAck ?? btn.dataset.rrFix), entry=sourceIssues[i];if(!entry)return;
    try{
      if(btn.dataset.rrLocate!=null && entry.source){
        await selectSource(entry.source.source_path||entry.source.file_name,entry.issue.block_id);
        overlay?.querySelector(".located")?.scrollIntoView({block:"nearest",inline:"nearest"});
        addLocal("定位课表问题", entry.issue.message);
      }else if(btn.dataset.rrFix!=null && entry.source){
        await selectSource(entry.source.source_path||entry.source.file_name,entry.issue.block_id);
        const row=review.blocks.find(b=>b.member_key===personKey && b.block_id===entry.issue.block_id);
        if(!row?.date||!frame||frame.sourceHash!==entry.source.content_hash)return;
        const slots=periodList(row).map(p=>({date:row.date!.slice(0,10),period:p,marked:true}));
        if(slots.length===1 && markedAt(slots[0].date,slots[0].period))await setSlot(slots[0].date,slots[0].period);
        else pickDate(slots,row.bbox?frame.rectFor(row)??undefined:undefined);
      }else if(btn.dataset.rrAck!=null){
        await actions.confirm(entry.issue.issue_id);addLocal("核实课表问题",entry.issue.message);
      }
    }catch(error){message(error instanceof Error?error.message:String(error));}
  };
  confirm.onkeydown=event=>{
    if(event.repeat && (event.key==="Enter" || event.key===" "))event.preventDefault();
  };
  confirm.onclick=async event=>{
    if(event.detail>1)return;
    const m=member();if(!m || saving)return;
    if(!m.confirmed && !await save("review.member",{acknowledge_missing:ackPerson===personKey}))return;
    const next=members().find(p=>!p.confirmed);
    if(next)await choosePerson(next.member_key);
    else actions.rangeComplete?.();
  };
  el("rrAdvanced").onclick=()=>{
    const host=el("rrReader");const open=host.classList.toggle("rrShowAdvanced");
    el("rrAdvanced").setAttribute("aria-expanded",String(open));
    if(open)host.querySelector<HTMLElement>(".reviewInspector")?.scrollIntoView({block:"nearest"});
  };
  async function update(next:ReviewPayload){
    if(destroyed || !next.date_review)return;
    review=next;el("rrReload").hidden=true;if(!member())personKey=members()[0]?.member_key||"";
    source=sources().find(s=>s.content_hash===source?.content_hash && s.file_name===source?.file_name)||sources()[0];
    if(reader)await reader.update(next);
    if(destroyed)return;
    renderNav();renderRail();repaint();
  }
  renderNav();renderRail();
  reader=await mountReader(el("rrReader"),review,actions,{initialSource:source?.content_hash,overlay:paint,failure:showSourceFailure});
  renderNav();renderRail();
  return {update,selectSource,destroy:()=>{destroyed=true;reader?.destroy();root.replaceChildren();},
    getViewState:()=>reader!.getViewState()};
}
