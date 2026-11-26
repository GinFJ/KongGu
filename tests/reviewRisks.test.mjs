import assert from "node:assert/strict";
import test from "node:test";
import { batchReviewRisks, issuesForSource } from "../src/reviewRisks.ts";

const cn = { file_name: "原件.pdf", kind: "中方", content_hash: "cn", source_path: "a/原件.pdf" };
const en = { file_name: "原件.pdf", kind: "英方", content_hash: "en", source_path: "b/原件.pdf" };
const member = { member: "示例", member_key: "member-1", chinese_schedule: "已导入", english_schedule: "已导入" };
const block = { source_hash: "cn", source_file: cn.file_name, member_key: member.member_key, block_id: "b1" };
const complete = { total: 1, confirmed: 1, pending: 0, items: [{ source_hash: "cn", source_file: cn.file_name, confirmed: true }] };
const review = { sources: [cn, en], blocks: [block], issues: [], verification: complete };
const result = {
  members: [member], issues: [], courses: [block], details: [cn, en].map((source) => ({
    filename: source.file_name, source_hash: source.content_hash, source_type: source.kind,
    member: member.member, course_block_count: 1
  }))
};

test("a single member with a complete pair is advisory; verified tiles use actual records", () => {
  const batch = batchReviewRisks(result, review);
  assert.equal(batch.singleton, true);
  assert.deepEqual(batch.pairs, []);
  assert.deepEqual(batch.files.map(({ state, target }) => [state, target]), [["done", cn.source_path], ["pending", en.source_path]]);
});

test("missing side is derived per identity even when two members share their name", () => {
  const other = { ...member, member_key: "member-2", english_schedule: "缺失" };
  const ownBlock = { ...block, member_key: other.member_key, source_hash: "en" };
  const batch = batchReviewRisks({ ...result, members: [member, other] }, { ...review, blocks: [block, ownBlock] });
  assert.equal(batch.singleton, false);
  assert.equal(batch.pairs[0].missing, "英方");
  assert.equal(batch.files[0].state, "done");
  assert.equal(batch.files[1].state, "blocked");
  assert.match(batch.files[1].reason, /缺英方/);
});

test("unreadable and explicitly checked empty sources stay distinguishable", () => {
  const noCourses = { ...result, details: result.details.map((detail, index) => ({
    ...detail, course_block_count: 0, status: index ? "人工核对无课程" : "解析失败"
  })) };
  const batch = batchReviewRisks(noCourses, { ...review, blocks: [], verification: { items: [] } });
  assert.deepEqual(batch.files.map(({ state, reason }) => [state, reason]), [["blocked", "文件解析失败"], ["done", "人工核对无课程"]]);
});

test("an unconfirmed course without a usable original-page box is highlighted", () => {
  const unchecked = { ...review, verification: { items: [
    { source_hash: "cn", block_ids: ["b1"], confirmed: false }
  ] } };
  assert.equal(batchReviewRisks(result, unchecked).files[0].reason, "1 项缺少原页定位框");
  const located = { ...unchecked, blocks: [{ ...block, page: 0, bbox: [1, 2, 8, 9] }] };
  assert.equal(batchReviewRisks(result, located).files[0].state, "pending");
});

test("duplicate names, block-only findings, and batch-wide issues retain their scope", () => {
  const named = { source_file: "原件.pdf" };
  const stale = { ...named, source_hash: "changed" };
  const scoped = { ...named, source_hash: "cn", severity: "warning", message: "识别疑点" };
  const fromBlock = { block_id: "b1" };
  const withIssues = { ...review, issues: [named, stale, scoped, fromBlock, { code: "BATCH" }] };
  assert.deepEqual(issuesForSource(withIssues, cn), [scoped, fromBlock]);
  assert.deepEqual(issuesForSource(withIssues, en), []);
  assert.equal(batchReviewRisks(result, withIssues).files[0].state, "attention");
  assert.equal(batchReviewRisks(result).files[0].target, "");
});
