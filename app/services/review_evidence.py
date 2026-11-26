"""Reuse review decisions only while all of their evidence is unchanged.

Receipts contain digests, not another copy of personal timetable data. They
expire and are deleted with their owning job under the existing local policy.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from core.models import ParseIssue
from core.quality import quality_state


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def issue_digest(issue: dict[str, Any]) -> str:
    return _digest({key: value for key, value in issue.items()
                    if key not in {'confirmed', 'confirmed_at'}})


def context_digest(snapshot: dict[str, Any]) -> str | None:
    generation = snapshot.get('generation') or {}
    sources = generation.get('sources') or []
    blocks = generation.get('blocks') or []
    if not sources or not blocks or not snapshot.get('parser_signature'):
        return None
    if any(not source.get('content_hash') for source in sources):
        return None
    fields = ('block_id', 'name', 'member_key', 'department', 'role', 'source_type',
              'source_file', 'source_hash', 'week', 'weekday', 'date', 'course',
              'page', 'bbox', 'provenance', 'start_time', 'end_time')
    canonical_blocks = []
    for block in blocks:
        canonical = {field: block.get(field) for field in fields}
        canonical['periods'] = sorted(block.get('periods') or [block.get('period')])
        canonical_blocks.append(_digest(canonical))
    return _digest({
        'schema': 1, 'parser_signature': snapshot['parser_signature'],
        'sources': sorted(_digest({key: source.get(key) for key in
                                  ('file_name', 'kind', 'content_hash')}) for source in sources),
        'blocks': sorted(canonical_blocks),
        'removed_blocks': sorted(_digest(b) for b in generation.get('removed_blocks', [])),
        'calendar': sorted(_digest(row) for row in generation.get('calendar_rows', [])),
        'weeks': sorted(generation.get('weeks', [])),
        'students': sorted(generation.get('students', [])),
        'issues': sorted(issue_digest(issue) for issue in snapshot.get('issues', [])),
        'errors': generation.get('errors', []),
    })


def confirmation_receipt(snapshot: dict[str, Any], issue: dict[str, Any]) -> dict[str, str] | None:
    context = context_digest(snapshot)
    if not context or issue.get('severity') != 'warning':
        return None
    return {'context_digest': context, 'issue_digest': issue_digest(issue),
            'issue_id': str(issue['issue_id']), 'parser_signature': snapshot['parser_signature']}


def reuse_confirmed_issues(store: Any, job_id: str) -> int:
    # Most batches have never been reviewed. Avoid decoding and fingerprinting
    # every course block when there is no earlier receipt to apply.
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM audit_events WHERE event='review.evidence' AND job_id<>? LIMIT 1",
                          (job_id,)).fetchone():
            return 0
    job = store.get_job(job_id)
    snapshot = (job or {}).get('result')
    if not snapshot:
        return 0
    context = context_digest(snapshot)
    if not context:
        return 0
    receipts = store.review_evidence(context)
    prior = {item['issue_digest']: item for item in receipts if item['job_id'] != job_id}
    matched = []
    for issue in snapshot.get('issues', []):
        if issue.get('confirmed') or issue.get('severity') != 'warning':
            continue
        receipt = prior.get(issue_digest(issue))
        if receipt:
            issue['confirmed'] = True
            matched.append((issue, receipt))
    if not matched:
        return 0
    snapshot['quality_state'] = quality_state([ParseIssue(**issue) for issue in snapshot['issues']])
    with store.connect() as connection:
        for issue, prior_receipt in matched:
            store.confirm_issue(job_id, issue['issue_id'], connection=connection)
            receipt = {'context_digest': context, 'issue_digest': issue_digest(issue),
                       'issue_id': issue['issue_id'], 'parser_signature': snapshot['parser_signature'],
                       'reason': prior_receipt.get('reason', ''),
                       'operator_id': prior_receipt.get('operator_id', '')}
            store.add_audit_event(job_id, 'review.evidence', receipt, connection=connection)
            store.add_audit_event(job_id, 'review.reused', {
                'issue_id': issue['issue_id'], 'source_job_id': prior_receipt['job_id'],
                'context_digest': context,
            }, connection=connection)
        store.update_job(job_id, result_json=json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')),
                         quality_state=snapshot['quality_state'], connection=connection)
    return len(matched)
