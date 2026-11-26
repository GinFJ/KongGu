import type { CourseRow, ParseIssue, ParseResult, ReviewPayload } from "./types";

type Source = ReviewPayload["sources"][number];
type Evidence = { source_hash?: string; source_file?: string };
type FileState = "blocked" | "attention" | "pending" | "done";

// Old jobs may have only a file name. Never assign an ambiguous finding to
// the first matching file (two imports may share a name or even a hash).
function sourceLookup(sources: Source[]) {
  const hashes = new Map<string, Source[]>();
  const names = new Map<string, Source[]>();
  for (const source of sources) {
    for (const [index, key] of [[hashes, source.content_hash], [names, source.file_name], [names, source.source_path]] as const) {
      if (!key) continue;
      const bucket = index.get(key) || [];
      if (!bucket.includes(source)) bucket.push(source);
      index.set(key, bucket);
    }
  }
  return (evidence: Evidence) => {
    const named = evidence.source_file ? names.get(evidence.source_file) : undefined;
    if (evidence.source_hash) {
      const hashed = hashes.get(evidence.source_hash) || [];
      if (hashed.length === 1 && (!named || named.includes(hashed[0]))) return hashed[0];
      const both = hashed.filter((source) => named?.includes(source));
      return both.length === 1 ? both[0] : undefined;
    }
    return named?.length === 1 ? named[0] : undefined;
  };
}

function sourceIssues(
  issues: ParseIssue[], blocks: CourseRow[], locate: ReturnType<typeof sourceLookup>
) {
  const byBlock = new Map(blocks.map((block) => [block.block_id, block]));
  const grouped = new Map<Source, ParseIssue[]>();
  for (const issue of issues) {
    const block = issue.block_id ? byBlock.get(issue.block_id) : undefined;
    const source = locate(issue) || (!issue.source_hash && !issue.source_file && block ? locate(block) : undefined);
    if (source) {
      const group = grouped.get(source) || [];
      group.push(issue);
      grouped.set(source, group);
    }
  }
  return grouped;
}

export function issuesForSource(review: ReviewPayload, source?: Source): ParseIssue[] {
  if (!source) return [];
  const locate = sourceLookup(review.sources);
  const current = review.sources.includes(source) ? source : locate({
    source_hash: source.content_hash, source_file: source.source_path || source.file_name
  });
  return current ? sourceIssues(review.issues, review.blocks, locate).get(current) || [] : [];
}

function missingSide(member: Record<string, unknown>) {
  if (member.chinese_schedule === "已导入" && member.english_schedule === "缺失") return "英方";
  if (member.english_schedule === "已导入" && member.chinese_schedule === "缺失") return "中方";
  return "";
}

export function batchReviewRisks(result: ParseResult, review?: ReviewPayload | null) {
  const sources = [...(review?.sources || [])];
  const initialLookup = sourceLookup(sources);
  const detailBySource = new Map<Source, Record<string, unknown>>();
  for (const detail of result.details) {
    const file = String(detail.filename || "");
    const hash = String(detail.source_hash || "");
    let source = initialLookup({ source_hash: hash, source_file: file });
    if (!source) {
      // Multiple selected originals may share both a name and content. Keep
      // their separate review entries, without manufacturing an extra source.
      if (review?.sources.some((entry) => (!hash || entry.content_hash === hash) && entry.file_name === file)) continue;
      source = { content_hash: hash, source_path: "", file_name: file, kind: String(detail.source_type || "") };
      sources.push(source);
    }
    detailBySource.set(source, detail);
  }
  const locate = sourceLookup(sources);
  const blocks = review?.blocks || result.courses;
  const issues = sourceIssues(review?.issues || result.issues, blocks, locate);
  const keysBySource = new Map<Source, Set<string>>();
  for (const block of blocks) {
    const source = locate(block);
    if (!source || !block.member_key) continue;
    const keys = keysBySource.get(source) || new Set<string>();
    keys.add(block.member_key);
    keysBySource.set(source, keys);
  }
  const pairs = result.members.filter((member) => missingSide(member)).map((member) => ({
    member, missing: missingSide(member)
  }));
  const blocksById = new Map(blocks.map((block) => [block.block_id, block]));
  const counts = new Map<Source, { total: number; confirmed: number; unlocated: number }>();
  for (const item of review?.verification?.items || []) {
    const source = locate(item);
    if (!source) continue;
    const count = counts.get(source) || { total: 0, confirmed: 0, unlocated: 0 };
    count.total++;
    if (item.confirmed) count.confirmed++;
    else if (item.block_ids?.length && !item.block_ids.some((id) => {
      const block = blocksById.get(id);
      return block?.page != null && block.bbox && block.bbox[2] > block.bbox[0] && block.bbox[3] > block.bbox[1];
    })) count.unlocated++;
    counts.set(source, count);
  }
  const files = sources.map((source) => {
    const detail = detailBySource.get(source);
    const keys = keysBySource.get(source);
    const memberName = String(detail?.member || "");
    const matchingMembers = result.members.filter((member) => member.member === memberName);
    const pair = pairs.find(({ member }) => keys?.size
      ? keys.has(String(member.member_key || ""))
      : matchingMembers.length === 1 && matchingMembers[0] === member);
    const active = (issues.get(source) || []).filter((issue) => !issue.confirmed);
    const hasError = active.some((issue) => issue.blocks_export && issue.severity === "error");
    const warning = active.find((issue) => issue.severity !== "info" || issue.blocks_export);
    const emptyChecked = detail?.status === "人工核对无课程";
    const empty = detail?.course_block_count === 0 && !emptyChecked;
    const count = counts.get(source);
    const checked = emptyChecked || (!!count?.total && count.total === count.confirmed);
    const failed = detail?.status === "解析失败" || Boolean(detail?.error);
    const state: FileState = failed || hasError || pair || empty ? "blocked" : warning || count?.unlocated ? "attention" : checked ? "done" : "pending";
    const reason = failed ? "文件解析失败" : empty ? "未识别到有课时段" : pair ? `缺${pair.missing}有效课表`
      : hasError ? "原件有待处理错误" : warning ? "识别有疑点" : count?.unlocated ? `${count.unlocated} 项缺少原页定位框` : emptyChecked ? "人工核对无课程" : "";
    // Only offer a name fallback when it navigates to a unique source.
    const target = source.source_path || (locate({ source_file: source.file_name }) === source ? source.file_name : "");
    return { source, memberName, count, state, reason, target };
  });
  return { files, pairs, singleton: result.members.length === 1 };
}
