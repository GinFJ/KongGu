import type { ParseResult } from "./types";

export type AguAction =
  | "choose-files"
  | "generate"
  | "open-availability"
  | "open-issues"
  | "open-review"
  | "retry"
  | "repair-resources";

export type AguReply = {
  text: string;
  action?: { label: string; value: AguAction };
};

export type AguAssistantContext = {
  files: string[];
  invalidFileCount: number;
  result: ParseResult | null;
  busy: boolean;
  stage: string;
  statusText: string;
  resourceReady: boolean;
  unresolvedIssues: number;
  activeTab: string;
};

export function extractPreferredName(question: string): string | null {
  const match = question.trim().match(/(?:以后)?(?:叫我|我叫)\s*([a-zA-Z0-9_\u4e00-\u9fff]{1,12})/);
  if (!match || /^(什么|啥|谁)$/.test(match[1])) return null;
  return match[1];
}

export function answerAgu(question: string, context: AguAssistantContext, userName = ""): AguReply {
  const raw = question.trim();
  const query = raw.toLowerCase();
  const address = userName ? `${userName}，` : "";
  const result = context.result;
  const unresolved = context.unresolvedIssues;

  if (!raw) return { text: "话到嘴边又收回去了？慢慢说，我在这儿。" };

  const preferredName = extractPreferredName(raw);
  if (preferredName) return { text: `好，${preferredName}。以后我就这样叫你。` };
  if (/忘掉.*名字|别叫我|清除.*称呼/.test(query)) return { text: "好，不叫了。称呼我已经忘记。" };

  if (/^(你好|嗨|哈喽|hello|在吗|阿谷在吗)[!！。,.，？?]*$/.test(query)) {
    if (context.busy) return { text: `${address}在呢。我正陪着课表往下走，图片页处理完就告诉你。` };
    if (unresolved) return { text: `${address}来啦。现在有 ${unresolved} 个地方等你看一眼，我们一起把它收好。` };
    if (result?.can_export) return { text: `${address}来啦。空课表已经整理好，看看没问题就可以导出了。` };
    if (context.files.length) return { text: `${address}来啦。课表已经选好了，接下来生成空课表就行。` };
    return { text: `${address}来啦。先把大家的中方和英方课表交给我吧。` };
  }

  if (/你是谁|叫什么|你的名字|介绍.*自己|什么性格/.test(query)) {
    return { text: "我是阿谷，空谷里的课表小帮手。我只看这台电脑上的当前结果；不确定的地方会告诉你，不替你把问题藏起来。" };
  }

  if (/谢谢|多谢|辛苦了/.test(query)) return { text: "不客气。把结果看清楚，比赶着点导出更重要。" };
  if (/笨|没用|不好用|烦人|讨厌/.test(query)) return { text: "听见了。这次先记我头上。你告诉我是哪一步卡住，我陪你把它拆开。" };
  if (/夸夸|真棒|厉害|聪明|喜欢你/.test(query)) return { text: "嘿嘿，先把课表核准了再夸我。我更在意最后导出的结果能不能放心用。" };

  if (/隐私|上传|联网|云端|安全|数据去哪/.test(query)) {
    return { text: "课表和这段对话都只在这台电脑上处理，不会为了聊天上传到云端。阿谷也没有绕过空谷的质量检查；不确定的内容仍然会留给你核对。" };
  }

  if (/下一步|接下来|怎么办|怎么做|做什么/.test(query)) return nextStep(context);

  if (/现在什么状态|处理到哪|进度|进行得怎么样|完成了吗/.test(query)) {
    if (context.busy) {
      return { text: context.statusText || "课表正在处理中。我会先处理能读懂的内容，图片课表可能需要多等一会儿。" };
    }
    if (unresolved) return { text: `目前已经生成结果，但还有 ${unresolved} 个地方需要你核对。先处理完它们，再导出会更稳。`, action: { label: "看看待处理", value: "open-issues" } };
    if (result?.can_export) return { text: "已经完成检查，当前结果可以导出。要不要先打开共同空闲看一眼？", action: { label: "查看共同空闲", value: "open-availability" } };
    if (context.files.length) return { text: "文件已经选好，还没有开始生成空课表。", action: { label: "生成空课表", value: "generate" } };
    return { text: "还没有开始。先导入课表，我才能帮你看共同空闲。", action: { label: "导入课表", value: "choose-files" } };
  }

  if (/导入|文件|课表.*有几|几份|成员.*多少/.test(query)) {
    if (!context.files.length) return { text: "还没有导入课表。建议每位成员的中方、英方课表一起选进来，文件名要能看出成员和课表类型。", action: { label: "导入课表", value: "choose-files" } };
    if (context.invalidFileCount) return { text: `现在选了 ${context.files.length} 份课表，其中 ${context.invalidFileCount} 份还看不出中方或英方。先改好文件名，我不会替你猜。` };
    const memberCount = result?.summary.member_count || 0;
    return { text: `现在选了 ${context.files.length} 份课表${memberCount ? `，整理出 ${memberCount} 位成员` : ""}。文件名检查已经通过。`, action: result ? undefined : { label: "生成空课表", value: "generate" } };
  }

  if (/待处理|问题|错误|异常|核对|为什么不能导出|导出不了|导出/.test(query)) {
    if (!result) return { text: "还没有生成结果，所以暂时没有可以核对的内容。先导入课表并生成空课表。", action: context.files.length ? { label: "生成空课表", value: "generate" } : { label: "导入课表", value: "choose-files" } };
    if (unresolved) {
      const firstIssue = result.issues.find((issue) => !issue.confirmed);
      const detail = firstIssue ? `现在最先要看的，是“${firstIssue.message}”。` : "有一些内容还没有确认。";
      return { text: `当前还有 ${unresolved} 个待处理问题，${detail}我不会把它们当成已经没事。`, action: { label: "打开待处理", value: "open-issues" } };
    }
    if (result.can_export) return { text: "目前没有未处理的问题，结果检查通过，可以放心导出。", action: { label: "导出前看看结果", value: "open-availability" } };
    return { text: "结果还不能导出，但我暂时没有拿到具体问题。建议打开原文核对，看看课表文字是否清楚。", action: { label: "打开原文核对", value: "open-review" } };
  }

  if (/原文|pdf|看不清|识别|课程.*不对|时间.*不对/.test(query)) {
    if (!result) return { text: "还没有可以对照的课表原文。生成结果后，我会把原文和识别内容放在一起。" };
    return { text: "原文核对适合处理课程名称、星期、周次和节次不确定的地方。你可以边看 PDF，边修正识别结果。", action: { label: "打开原文核对", value: "open-review" } };
  }

  if (/共同空闲|空课|空闲|什么时候有空|时间安排/.test(query)) {
    if (!result) return { text: "共同空闲还没算出来。先把成员课表放在一起，我再帮你看大家都空的时段。" };
    if (unresolved) return { text: `共同空闲已经算出一版，但还有 ${unresolved} 个问题没有确认。先处理它们，时间结果才值得拿去排班。`, action: { label: "先处理问题", value: "open-issues" } };
    return { text: "共同空闲在这里。建议先看人数和成员名单，再决定用哪几个时段排班。", action: { label: "查看共同空闲", value: "open-availability" } };
  }

  if (/重试|重新处理|失败文件/.test(query)) {
    if (!context.result || !["failed", "interrupted", "cancelled"].includes(context.stage)) return { text: "现在没有需要重新处理的失败任务。" };
    return { text: "可以重新处理刚才失败的文件。我会保留原来的问题记录，不会把失败当成成功。", action: { label: "重新处理", value: "retry" } };
  }

  if (/识别功能|图片课表|修复/.test(query) && !context.resourceReady) {
    return { text: "图片课表识别功能还没有完全准备好。先修复本地识别组件，再处理扫描版课表会更稳。", action: { label: "修复识别功能", value: "repair-resources" } };
  }

  if (/心情|开心|难过|高兴吗|怎么样/.test(query)) {
    if (context.busy) return { text: "现在有点忙，但我在认真盯着每一页课表。" };
    if (unresolved) return { text: "有点专注。待处理的问题还没收好，我不想装作已经完成。" };
    if (result?.can_export) return { text: "还不错。结果检查完了，看到可以导出，我的青心也亮了一下。" };
    return { text: "挺好的。你把问题交给我，我就有事情做了。" };
  }

  return { text: "这句我还没完全听懂。你可以问我“现在什么状态”“为什么不能导出”“哪些课表需要核对”，也可以直接让我打开对应页面。" };
}

function nextStep(context: AguAssistantContext): AguReply {
  if (context.busy) return { text: "现在先等处理完成。我会把进度和需要你确认的地方留下来，你不用一直盯着。" };
  if (!context.files.length) return { text: "第一步是导入每位成员的中方和英方课表。文件名写清楚，我就不用猜。", action: { label: "导入课表", value: "choose-files" } };
  if (context.invalidFileCount) return { text: `先处理 ${context.invalidFileCount} 份文件名不清楚的课表，再开始生成。` };
  if (!context.result) return { text: "文件准备好了，下一步生成空课表。", action: { label: "生成空课表", value: "generate" } };
  if (context.unresolvedIssues) return { text: `下一步是处理 ${context.unresolvedIssues} 个待处理问题。核对清楚后，导出按钮才有意义。`, action: { label: "打开待处理", value: "open-issues" } };
  if (context.result.can_export) return { text: "下一步可以先看共同空闲，确认人数和时段，再导出空课表。", action: { label: "查看共同空闲", value: "open-availability" } };
  return { text: "结果已经出来了，但还需要打开原文核对，确认课程时间没有读错。", action: { label: "打开原文核对", value: "open-review" } };
}
